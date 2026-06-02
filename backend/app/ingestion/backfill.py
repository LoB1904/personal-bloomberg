"""
Fase 1 — Backfill storico prezzi su tutti i ticker in ticker_universe.

Features:
  - Backfill 5 anni (1825 giorni) per default su tutti i ticker attivi
  - Rate limiting: max 5 richieste/secondo (rispettoso dei limiti Yahoo)
  - Progress bar con ETA (tqdm)
  - Resume: salta i ticker che hanno già N giorni di storico sufficiente
  - Checkpoint su file locale per riprendere dopo un'interruzione (Ctrl+C)

Uso:
    python -m app.ingestion.backfill
    python -m app.ingestion.backfill --days 1825 --max-rps 3
    python -m app.ingestion.backfill --force          # ignora checkpoint e ri-fetcha tutto
    python -m app.ingestion.backfill --ticker AAPL    # singolo ticker
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time
import json
from datetime import date, timedelta
from pathlib import Path

import requests
import urllib3
import pandas as pd
from tqdm import tqdm
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe

urllib3.disable_warnings()
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

logger = logging.getLogger(__name__)

# File di checkpoint locale (relativo alla cartella backend/)
_CHECKPOINT_FILE = Path(__file__).parents[3] / ".backfill_checkpoint.json"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _BROWSER_UA})
_SESSION.verify = False


# ── Checkpoint ────────────────────────────────────────────────────────

def _load_checkpoint() -> set[int]:
    """Carica gli ticker_id già completati dal file di checkpoint."""
    if not _CHECKPOINT_FILE.exists():
        return set()
    try:
        data = json.loads(_CHECKPOINT_FILE.read_text())
        return set(data.get("completed_ids", []))
    except Exception:
        return set()


def _save_checkpoint(completed_ids: set[int]) -> None:
    try:
        tmp = _CHECKPOINT_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"completed_ids": sorted(completed_ids), "updated_at": date.today().isoformat()})
        )
        tmp.replace(_CHECKPOINT_FILE)   # atomico su Windows e POSIX
    except Exception as e:
        logger.warning(f"Checkpoint non salvato: {e}")


def _clear_checkpoint() -> None:
    if _CHECKPOINT_FILE.exists():
        _CHECKPOINT_FILE.unlink()
        logger.info("Checkpoint rimosso (--force)")


# ── Resume logic ──────────────────────────────────────────────────────

def _tickers_needing_backfill(
    engine,
    days: int,
    ticker: str | None = None,
) -> list[dict]:
    """
    Ritorna i ticker che NON hanno già abbastanza storico.
    Un ticker è considerato "completo" se ha almeno (days * 0.65) righe in prices_daily
    — 65% per tolerare weekend/festivi/halting.
    """
    min_rows = int(days * 0.65)
    where = "t.is_active = TRUE"
    params: dict = {"min_rows": min_rows}

    if ticker:
        where += " AND t.ticker = :ticker"
        params["ticker"] = ticker

    sql = text(f"""
        SELECT
            t.id,
            t.ticker,
            t.exchange,
            COUNT(p.id) AS price_rows
        FROM ticker_universe t
        LEFT JOIN prices_daily p ON p.ticker_id = t.id
        WHERE {where}
        GROUP BY t.id, t.ticker, t.exchange
        HAVING COUNT(p.id) < :min_rows
        ORDER BY t.id
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"id": r.id, "ticker": r.ticker, "exchange": r.exchange or "", "has_rows": r.price_rows} for r in rows]


# ── Yahoo Finance fetch ───────────────────────────────────────────────

def _fetch_yahoo(ticker: str, start: date, end: date) -> list[dict]:
    """Fetch prezzi per un ticker via Yahoo Finance chart API."""
    start_ts = int(pd.Timestamp(start).timestamp())
    end_ts   = int(pd.Timestamp(end + timedelta(days=1)).timestamp())
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}"
    )
    try:
        r = _SESSION.get(url, timeout=15)
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            logger.debug(f"{ticker}: HTTP {r.status_code}")
            return []
        data   = r.json()
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return []
        res      = result[0]
        meta     = res.get("meta", {})
        times    = res.get("timestamp", [])
        quote    = res.get("indicators", {}).get("quote", [{}])[0]
        adjclose = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose", [])

        rows = []
        for i, ts in enumerate(times):
            close = (quote.get("close") or [None])[i] if i < len(quote.get("close") or []) else None
            if close is None:
                continue

            def _safe(v, cast=float):
                return cast(v) if v is not None and not pd.isna(v) else None

            adj = adjclose[i] if i < len(adjclose) else None
            rows.append({
                "date":      pd.Timestamp(ts, unit="s", tz=meta.get("exchangeTimezoneName", "UTC")).date(),
                "open":      _safe((quote.get("open") or [None])[i] if i < len(quote.get("open") or []) else None),
                "high":      _safe((quote.get("high") or [None])[i] if i < len(quote.get("high") or []) else None),
                "low":       _safe((quote.get("low") or [None])[i] if i < len(quote.get("low") or []) else None),
                "close":     float(close),
                "adj_close": _safe(adj) if adj is not None else float(close),
                "volume":    _safe((quote.get("volume") or [None])[i] if i < len(quote.get("volume") or []) else None, cast=int),
            })
        return rows
    except Exception as e:
        logger.debug(f"{ticker}: errore fetch — {e}")
        return []


# ── Rate limiter semplice ─────────────────────────────────────────────

class _RateLimiter:
    """Mantiene al massimo max_rps richieste al secondo."""
    def __init__(self, max_rps: float):
        self._interval = 1.0 / max_rps
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        remaining = self._interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last = time.monotonic()


# ── Main backfill ─────────────────────────────────────────────────────

def backfill_prices(
    engine=None,
    days: int = 1825,
    max_rps: float = 5.0,
    ticker: str | None = None,
    force: bool = False,
) -> int:
    """
    Backfill storico prezzi per tutti i ticker che ne hanno bisogno.

    Args:
        days:    Giorni di storico (default 1825 = 5 anni)
        max_rps: Richieste al secondo verso Yahoo Finance (default 5)
        ticker:  Se specificato, backfilla solo quel ticker
        force:   Se True, ignora checkpoint e ri-fetcha tutto

    Returns: righe totali upsertate in prices_daily.
    """
    if engine is None:
        engine = get_engine()

    if force:
        _clear_checkpoint()

    completed = _load_checkpoint()
    needs     = _tickers_needing_backfill(engine, days, ticker)

    # Escludi ticker già completati in sessioni precedenti
    if not force:
        needs = [t for t in needs if t["id"] not in completed]

    if not needs:
        logger.info("Tutti i ticker hanno già storico sufficiente — niente da fare.")
        logger.info("Usa --force per ri-fetchare comunque.")
        return 0

    end   = date.today()
    start = end - timedelta(days=days)
    limiter = _RateLimiter(max_rps)

    logger.info(
        f"Backfill: {len(needs)} ticker | {start} → {end} ({days} giorni) | max {max_rps} req/s"
    )

    total_rows = 0
    errors     = 0

    pbar = tqdm(
        needs,
        desc="Backfill prezzi",
        unit="ticker",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    )

    try:
        for t in pbar:
            limiter.wait()
            pbar.set_postfix(ticker=t["ticker"], errors=errors, refresh=False)

            rows = _fetch_yahoo(t["ticker"], start, end)

            if not rows:
                logger.debug(f"No data: {t['ticker']}")
                errors += 1
                completed.add(t["id"])   # Segna come processato anche se vuoto (evita retry inutili)
                continue

            df = pd.DataFrame(rows)
            df["ticker_id"] = t["id"]
            df["source"]    = "yfinance"

            n = upsert_dataframe(engine, df, "prices_daily", ["ticker_id", "date"])
            total_rows += n
            completed.add(t["id"])

            # Salva checkpoint ogni 10 ticker
            if len(completed) % 10 == 0:
                _save_checkpoint(completed)

    except KeyboardInterrupt:
        logger.warning("Interrotto dall'utente. Checkpoint salvato — riprendi con lo stesso comando.")
        _save_checkpoint(completed)
        return total_rows

    _save_checkpoint(completed)
    logger.info(
        f"BACKFILL DONE — {total_rows} righe in prices_daily | "
        f"{len(needs) - errors}/{len(needs)} ticker OK | {errors} senza dati"
    )
    return total_rows


# ── Entry point ───────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Backfill storico prezzi su tutti i ticker in ticker_universe"
    )
    parser.add_argument("--days",    type=int,   default=1825,
                        help="Anni di storico (default 1825 = 5 anni)")
    parser.add_argument("--max-rps", type=float, default=5.0,
                        help="Max richieste al secondo verso Yahoo (default 5)")
    parser.add_argument("--ticker",  type=str,   default=None,
                        help="Backfilla solo un ticker specifico")
    parser.add_argument("--force",   action="store_true",
                        help="Ignora checkpoint e ri-fetcha tutto")
    args = parser.parse_args()

    backfill_prices(
        days=args.days,
        max_rps=args.max_rps,
        ticker=args.ticker,
        force=args.force,
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main())
    # Smoke test senza argomenti: mostra quanti ticker necessitano di backfill
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    engine = get_engine()
    needs = _tickers_needing_backfill(engine, days=1825)
    print(f"DB OK — {len(needs)} ticker necessitano backfill (< 65% di 1825 giorni)")
    if needs:
        print(f"Primo: {needs[0]['ticker']} ({needs[0]['has_rows']} righe attualmente)")
    checkpoint = _load_checkpoint()
    print(f"Checkpoint: {len(checkpoint)} ticker già completati in sessioni precedenti")
    sys.exit(0)
