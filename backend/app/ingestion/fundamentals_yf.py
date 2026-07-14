"""
Fase 1 — Ingestione fondamentali via yfinance (fonte gratuita).

Rimpiazza EODHD (che sul piano free restituisce 403 sui fondamentali) con
yfinance, che espone gli stessi campi tramite yf.Ticker(symbol).info.
Fallback su FMP (codice riusato da fundamentals.py) se yfinance non
restituisce nulla di utile per un ticker.

Universo processato: single-name attivi in ('sp500', 'ftsemib', 'wildcard').

Scrive in fundamentals_snapshot con source='yfinance' (o 'fmp' nel fallback),
upsert su (ticker_id, snapshot_date, fiscal_period) — il vincolo UNIQUE reale
del DB. fiscal_period è sempre valorizzato (mai NULL) per garantire idempotenza.

Nota unità: yfinance espone debtToEquity in scala percentuale (es. 195 = 1.95x).
Lo normalizziamo dividendo per 100, così debt_to_equity nel DB è il rapporto
vero e le soglie downstream (es. alert debt/equity < 1.5) funzionano.

Uso:
    python -m app.ingestion.fundamentals_yf
    python -m app.ingestion.fundamentals_yf --ticker AAPL
    python -m app.ingestion.fundamentals_yf --limit 50
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import requests
import urllib3
import yfinance as yf
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe
from app.core.config import settings
from app.ingestion.fundamentals import (
    TICKER_BLACKLIST,
    _fetch_fmp_fundamentals,
    _parse_fmp,
)

# Disattiva verifica SSL a livello di ambiente (proxy aziendale con MITM cert).
# In GitHub Actions non serve ma non fa danni.
os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
urllib3.disable_warnings()

logger = logging.getLogger(__name__)

# Session condivisa con verify=False: aggira il MITM cert del proxy aziendale.
# In CI (nessun proxy) è innocua. yfinance accetta una session custom.
_SESSION = requests.Session()
_SESSION.verify = False
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

_SLEEP_BETWEEN = 0.5   # rate limit soft tra un ticker e l'altro
_LOG_EVERY     = 50    # progress log ogni N ticker
_YF_RETRIES    = 3     # tentativi su 429/empty (Yahoo rate-limita gli IP freddi)

# Universi single-name da processare (ETF e baseline esclusi: non hanno fondamentali)
_UNIVERSE_GROUPS = ("sp500", "ftsemib", "wildcard")

# Colonne canoniche scritte in fundamentals_snapshot.
# Ogni riga (yfinance o FMP) viene proiettata esattamente su queste chiavi:
# uniformità garantita → un solo upsert, niente NaN sparsi.
_CANON_COLS = [
    "ticker_id", "snapshot_date", "fiscal_period",
    "market_cap", "pe_ratio", "pb_ratio",
    "roe", "gross_margin", "net_margin",
    "debt_to_equity", "free_cash_flow",
    "revenue_growth_yoy", "eps_growth_yoy",
    "source", "raw_json",
]


# ── Helpers ───────────────────────────────────────────────────────────

def _clean(v: Any) -> float | None:
    """Converte a float finito o None. Scarta NaN/Inf/None/stringhe non numeriche."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _fiscal_period(info: dict, snapshot: date) -> str:
    """
    fiscal_period deterministico e mai NULL (serve al vincolo UNIQUE a 3 colonne).
    Formato 'Q{trimestre}-{anno}' (es. 'Q2-2025', max 7 char → sta in VARCHAR(8)).
    Deriva da mostRecentQuarter (epoch); fallback FY{anno snapshot}.
    """
    mrq = info.get("mostRecentQuarter")
    if mrq:
        try:
            d = datetime.fromtimestamp(int(mrq), tz=timezone.utc).date()
            quarter = (d.month - 1) // 3 + 1
            return f"Q{quarter}-{d.year}"   # es. 'Q2-2025'
        except (TypeError, ValueError, OSError):
            pass
    return f"FY{snapshot.year}"


def _load_tickers(engine, ticker: str | None, limit: int | None) -> list[dict]:
    """Carica i single-name attivi da processare."""
    groups_sql = ", ".join(f"'{g}'" for g in _UNIVERSE_GROUPS)
    where = f"is_active = TRUE AND universe_group IN ({groups_sql})"
    params: dict = {}
    if ticker:
        where += " AND ticker = :tk"
        params["tk"] = ticker
    sql = f"SELECT id, ticker FROM ticker_universe WHERE {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [{"id": r.id, "ticker": r.ticker} for r in rows]


# ── yfinance fetch + map ──────────────────────────────────────────────

def _fetch_yf(symbol: str) -> dict | None:
    """
    Scarica yf.Ticker(symbol).info usando la session verify=False.
    Retry con backoff su 429/empty. Ritorna il dict grezzo o None.
    """
    for attempt in range(1, _YF_RETRIES + 1):
        try:
            info = yf.Ticker(symbol, session=_SESSION).info
            # Una risposta valida ha molte chiavi; se ne ha pochissime è un 429 mascherato
            if info and isinstance(info, dict) and len(info) > 5:
                return info
        except Exception as e:
            logger.debug(f"yfinance {symbol} (tentativo {attempt}): {e}")
        if attempt < _YF_RETRIES:
            time.sleep(1.5 * attempt)   # backoff lineare
    return None


def _map_yf(info: dict, ticker_id: int, snapshot: date) -> dict[str, Any]:
    """Mappa yf.info → riga fundamentals_snapshot (colonne canoniche)."""
    de_raw = _clean(info.get("debtToEquity"))
    # yfinance: debtToEquity in percentuale (195 = 1.95x) → normalizza a rapporto
    debt_to_equity = de_raw / 100.0 if de_raw is not None else None

    mapped = {
        "market_cap":         _clean(info.get("marketCap")),
        "pe_ratio":           _clean(info.get("trailingPE")),
        "pb_ratio":           _clean(info.get("priceToBook")),
        "roe":                _clean(info.get("returnOnEquity")),
        "gross_margin":       _clean(info.get("grossMargins")),
        "net_margin":         _clean(info.get("profitMargins")),
        "debt_to_equity":     debt_to_equity,
        "free_cash_flow":     _clean(info.get("freeCashflow")),
        "revenue_growth_yoy": _clean(info.get("revenueGrowth")),
        "eps_growth_yoy":     _clean(info.get("earningsGrowth")),
    }
    row = {c: None for c in _CANON_COLS}
    row.update(mapped)
    row["ticker_id"]     = ticker_id
    row["snapshot_date"] = snapshot
    row["fiscal_period"] = _fiscal_period(info, snapshot)
    row["source"]        = "yfinance"
    row["raw_json"]      = json.dumps({k: v for k, v in mapped.items()})
    return row


def _all_none(row: dict) -> bool:
    """True se tutti i campi fondamentali (esclusi meta) sono None."""
    data_cols = [c for c in _CANON_COLS
                 if c not in ("ticker_id", "snapshot_date", "fiscal_period", "source", "raw_json")]
    return all(row.get(c) is None for c in data_cols)


def _project_fmp(fmp_row: dict, snapshot: date) -> dict[str, Any]:
    """Proietta l'output di _parse_fmp sulle colonne canoniche + fiscal_period non-null."""
    row = {c: None for c in _CANON_COLS}
    for c in _CANON_COLS:
        if c in fmp_row:
            row[c] = fmp_row[c]
    row["snapshot_date"] = snapshot
    # _parse_fmp può lasciare fiscal_period None → forziamo un valore stabile
    if not row.get("fiscal_period"):
        row["fiscal_period"] = f"FY{snapshot.year}"
    row["source"]   = "fmp"
    row["raw_json"] = None
    return row


# ── Batch ─────────────────────────────────────────────────────────────

def fetch_fundamentals_yf(
    engine=None,
    ticker: str | None = None,
    limit: int | None = None,
) -> int:
    """
    Fetcha fondamentali via yfinance (fallback FMP) per l'universo single-name.
    Ritorna il numero di righe upsertate in fundamentals_snapshot.
    """
    if engine is None:
        engine = get_engine()

    fmp_key  = getattr(settings, "fmp_api_key", None) or os.getenv("FMP_API_KEY", "")
    tickers  = _load_tickers(engine, ticker, limit)
    snapshot = date.today()

    if not tickers:
        logger.warning("Nessun ticker da processare")
        return 0

    logger.info(f"yfinance fundamentals: {len(tickers)} ticker | snapshot={snapshot}")

    rows: list[dict] = []
    n_yf = n_fmp = n_skip = 0

    for i, t in enumerate(tickers, start=1):
        tk  = t["ticker"]
        tid = t["id"]

        info = _fetch_yf(tk)
        row  = _map_yf(info, tid, snapshot) if info else None

        # Fallback FMP se yfinance non ha dato nulla di utile
        if (row is None or _all_none(row)) and fmp_key:
            base = tk.split(".")[0]
            if base.upper() not in TICKER_BLACKLIST:
                fmp_data = _fetch_fmp_fundamentals(base, fmp_key)
                if fmp_data:
                    row = _project_fmp(_parse_fmp(fmp_data, tid), snapshot)
                    if not _all_none(row):
                        n_fmp += 1

        if row is None or _all_none(row):
            n_skip += 1
        else:
            rows.append(row)
            if row["source"] == "yfinance":
                n_yf += 1

        if i % _LOG_EVERY == 0:
            logger.info(f"{i}/{len(tickers)} completati... (yf={n_yf} fmp={n_fmp} skip={n_skip})")

        time.sleep(_SLEEP_BETWEEN)

    if not rows:
        logger.warning("Nessun fondamentale recuperato")
        return 0

    df = pd.DataFrame(rows, columns=_CANON_COLS)
    n  = upsert_dataframe(
        engine, df, "fundamentals_snapshot",
        conflict_cols=["ticker_id", "snapshot_date", "fiscal_period"],
    )
    logger.info(f"DONE — {n} snapshot upsertati (yfinance={n_yf}, fmp={n_fmp}, skip={n_skip})")
    return n


# ── Entry point ───────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Fetch fondamentali via yfinance (+ FMP fallback)")
    parser.add_argument("--ticker", type=str, default=None, help="Un singolo ticker (es. AAPL)")
    parser.add_argument("--limit",  type=int, default=None, help="Processa solo i primi N ticker")
    args = parser.parse_args()

    n = fetch_fundamentals_yf(ticker=args.ticker, limit=args.limit)
    print(f"Upsertati {n} snapshot in fundamentals_snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
