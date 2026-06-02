"""
Fase 1 — Ingestione fondamentali da EODHD (priorità) con fallback FMP.

Fetch: PE, PB, ROE, ROIC, Debt/Equity, FCF, Revenue Growth, EPS.
CreditBudgetGuard: max 800 crediti/giorno su EODHD (1000 limite piano, -200 margine).
Blacklist ticker ambigui (parole inglesi comuni che EODHD interpreta male).
Retry su HTTP 429/503 con backoff esponenziale via tenacity.

Uso:
    python -m app.ingestion.fundamentals
    python -m app.ingestion.fundamentals --ticker AAPL
    python -m app.ingestion.fundamentals --max-credits 400
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from typing import Any

import requests
import urllib3
import pandas as pd
from sqlalchemy import text
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from app.core.db import get_engine, upsert_dataframe
from app.core.config import settings

urllib3.disable_warnings()
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

logger = logging.getLogger(__name__)

# ── Costanti ──────────────────────────────────────────────────────────

# Ticker che coincidono con parole comuni/abbreviazioni inglesi:
# EODHD o FMP possono restituire dati di aziende sbagliate.
TICKER_BLACKLIST: frozenset[str] = frozenset({
    "IT", "AI", "NOW", "REAL", "ARE", "WELL", "ALL", "KEY", "DFS", "RE",
})

# Exchange Yahoo Finance → EODHD exchange code
_EXCHANGE_MAP: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE":   "US",
    "MIL":    "MI",
    "Xetra":  "XETRA",
    "LSE":    "LSE",
    "":       "US",   # default
}

# Exchange suffix dei ticker (es. VWCE.DE) → EODHD
_SUFFIX_MAP: dict[str, str] = {
    ".MI":  "MI",
    ".DE":  "XETRA",
    ".F":   "XETRA",   # Borsa Frankfurt secondaria
    ".L":   "LSE",
    ".AS":  "AS",    # Euronext Amsterdam
    ".PA":  "PA",    # Euronext Paris
    ".SW":  "XSWX",  # SIX Swiss Exchange
    ".TO":  "TO",    # TSX Canada
}


# ── CreditBudgetGuard ─────────────────────────────────────────────────

class BudgetExceededError(Exception):
    pass


class CreditBudgetGuard:
    """
    Traccia i crediti EODHD consumati nella sessione corrente.
    Ogni chiamata all'endpoint fundamentals costa 1 credito.
    Limite piano: 1000/giorno — usiamo 800 per lasciare margine operativo.
    """
    def __init__(self, max_credits: int = 800):
        self._max = max_credits
        self._used = 0

    def charge(self, credits: int = 1) -> None:
        if self._used + credits > self._max:
            raise BudgetExceededError(
                f"EODHD daily budget raggiunto: {self._used}/{self._max} crediti. "
                "Riprendi domani o aumenta --max-credits se hai un piano superiore."
            )
        self._used += credits

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return self._max - self._used

    def __repr__(self) -> str:
        return f"CreditBudgetGuard(used={self._used}, max={self._max})"


# ── Helpers ticker format ─────────────────────────────────────────────

def _to_eodhd_ticker(ticker: str, exchange: str = "") -> str | None:
    """
    Converte il ticker Yahoo Finance nel formato EODHD (TICKER.EXCHANGE).
    Ritorna None se il ticker è in blacklist.

    Esempi:
        AAPL, NYSE  → AAPL.US
        ISP.MI, MIL → ISP.MI
        VWCE.DE     → VWCE.XETRA
        IWQU.L      → IWQU.LSE
    """
    # Estrai il ticker base (rimuovi suffisso exchange se presente)
    base = ticker
    eodhd_exchange = _EXCHANGE_MAP.get(exchange, "US")

    for suffix, ex_code in _SUFFIX_MAP.items():
        if ticker.endswith(suffix):
            base = ticker[: -len(suffix)]
            eodhd_exchange = ex_code
            break

    if base.upper() in TICKER_BLACKLIST:
        logger.debug(f"Ticker {base} in blacklist — skip")
        return None

    return f"{base}.{eodhd_exchange}"


# ── EODHD Client ──────────────────────────────────────────────────────

_EODHD_BASE = "https://eodhd.com/api"

_SESSION = requests.Session()
_SESSION.verify = False
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code in (429, 503)
    return isinstance(exc, requests.ConnectionError)


@retry(
    retry=retry_if_exception_type((requests.HTTPError, requests.ConnectionError)),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _get_eodhd(url: str, params: dict) -> dict:
    r = _SESSION.get(url, params=params, timeout=20)
    if r.status_code == 429:
        logger.warning("EODHD rate limit (429) — retry tra poco")
        r.raise_for_status()
    if r.status_code == 503:
        logger.warning("EODHD servizio non disponibile (503) — retry")
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


def _fetch_eodhd_fundamentals(eodhd_ticker: str, api_token: str) -> dict[str, Any] | None:
    """Chiama EODHD /api/fundamentals/{ticker} e ritorna dict grezzo."""
    url = f"{_EODHD_BASE}/fundamentals/{eodhd_ticker}"
    params = {"api_token": api_token, "fmt": "json"}
    try:
        data = _get_eodhd(url, params)
        if not data or "Highlights" not in data:
            logger.warning(f"EODHD {eodhd_ticker}: risposta vuota o inattesa")
            return None
        return data
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            logger.warning(f"EODHD {eodhd_ticker}: ticker non trovato (404)")
        else:
            logger.error(f"EODHD {eodhd_ticker}: HTTP error {e}")
        return None
    except Exception as e:
        logger.error(f"EODHD {eodhd_ticker}: errore {e}")
        return None


def _parse_eodhd(data: dict, ticker_id: int) -> dict[str, Any]:
    """Mappa il JSON EODHD alle colonne di fundamentals_snapshot."""
    h  = data.get("Highlights", {})
    v  = data.get("Valuation", {})
    bs = data.get("BalanceSheet", {}).get("quarterly", {})
    cf = data.get("CashFlow", {}).get("annual", {})

    def _f(d: dict, *keys) -> float | None:
        for k in keys:
            val = d.get(k)
            if val is not None and val not in ("None", "", "N/A"):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
        return None

    # FCF: più recente annuale
    fcf = None
    if cf:
        latest_cf = next(iter(cf.values()), {})
        fcf = _f(latest_cf, "freeCashFlow", "totalCashFromOperatingActivities")

    return {
        "ticker_id":          ticker_id,
        "snapshot_date":      date.today(),
        "fiscal_period":      h.get("MostRecentQuarter", None),
        "market_cap":         (
            _f(h, "MarketCapitalizationMln") * 1e6
            if _f(h, "MarketCapitalizationMln") is not None
            else None
        ),
        "enterprise_value":   _f(v, "EnterpriseValue"),
        "pe_ratio":           _f(v, "TrailingPE") or _f(h, "PERatio"),
        "forward_pe":         _f(v, "ForwardPE"),
        "pb_ratio":           _f(v, "PriceBookMRQ"),
        "ps_ratio":           _f(v, "PriceSalesTTM"),
        "ev_ebitda":          _f(v, "EnterpriseValueEbitda"),
        "roe":                _f(h, "ReturnOnEquityTTM"),
        "roic":               None,   # EODHD non espone ROIC direttamente — calcolabile in Fase 2
        "gross_margin":       (
            _f(h, "GrossProfitTTM") / _f(h, "RevenueTTM")
            if _f(h, "GrossProfitTTM") is not None
            and _f(h, "RevenueTTM") is not None
            and _f(h, "RevenueTTM") > 0
            else None
        ),
        "operating_margin":   _f(h, "OperatingMarginTTM"),
        "net_margin":         _f(h, "ProfitMargin"),
        "debt_to_equity":     None,   # da BalanceSheet se disponibile
        "net_debt":           None,
        "cash_and_equiv":     None,
        "free_cash_flow":     fcf,
        "revenue":            _f(h, "RevenueTTM"),
        "revenue_growth_yoy": _f(h, "QuarterlyRevenueGrowthYOY"),
        "eps":                _f(h, "DilutedEpsTTM") or _f(h, "EPS"),
        "eps_growth_yoy":     _f(h, "QuarterlyEarningsGrowthYOY"),
        "dividend_yield":     _f(h, "DividendYield") and _f(h, "DividendYield") * 100,
        "payout_ratio":       None,
        "source":             "eodhd",
        "fetched_at":         datetime.now(tz=timezone.utc),
    }


# ── FMP Fallback ──────────────────────────────────────────────────────

_FMP_BASE = "https://financialmodelingprep.com/api/v3"


def _fetch_fmp_fundamentals(ticker: str, api_key: str) -> dict[str, Any] | None:
    """Fallback FMP: chiama /ratios e /key-metrics."""
    try:
        r = _SESSION.get(
            f"{_FMP_BASE}/ratios/{ticker}",
            params={"apikey": api_key, "limit": 1},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json()
        if not items:
            return None
        return items[0]
    except Exception as e:
        logger.warning(f"FMP {ticker}: {e}")
        return None


def _parse_fmp(data: dict, ticker_id: int) -> dict[str, Any]:
    """Mappa il JSON FMP alle colonne di fundamentals_snapshot."""
    def _f(key: str) -> float | None:
        v = data.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
        return None

    return {
        "ticker_id":          ticker_id,
        "snapshot_date":      date.today(),
        "fiscal_period":      data.get("period"),
        "market_cap":         None,
        "enterprise_value":   None,
        "pe_ratio":           _f("priceEarningsRatio"),
        "forward_pe":         None,
        "pb_ratio":           _f("priceToBookRatio"),
        "ps_ratio":           _f("priceToSalesRatio"),
        "ev_ebitda":          _f("enterpriseValueMultiple"),
        "roe":                _f("returnOnEquity"),
        "roic":               _f("returnOnInvestedCapital"),
        "gross_margin":       _f("grossProfitMargin"),
        "operating_margin":   _f("operatingProfitMargin"),
        "net_margin":         _f("netProfitMargin"),
        "debt_to_equity":     _f("debtEquityRatio"),
        "net_debt":           None,
        "cash_and_equiv":     None,
        "free_cash_flow":     _f("freeCashFlowPerShare"),  # per share — nota
        "revenue":            None,
        "revenue_growth_yoy": _f("revenueGrowth"),
        "eps":                _f("eps"),
        "eps_growth_yoy":     _f("epsgrowth"),
        "dividend_yield":     _f("dividendYield"),
        "payout_ratio":       _f("payoutRatio"),
        "source":             "fmp",
        "fetched_at":         datetime.now(tz=timezone.utc),
    }


# ── Batch fetch ───────────────────────────────────────────────────────

def _load_tickers(engine, ticker: str | None = None) -> list[dict]:
    """Carica i ticker attivi da ticker_universe (escludi ETF puri e crypto)."""
    where = "t.is_active = TRUE AND t.asset_class IN ('equity', 'etf')"
    params: dict = {}
    if ticker:
        where += " AND t.ticker = :ticker"
        params["ticker"] = ticker
    sql = text(f"""
        SELECT t.id, t.ticker, t.exchange, t.asset_class
        FROM ticker_universe t
        WHERE {where}
        ORDER BY t.id
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"id": r.id, "ticker": r.ticker, "exchange": r.exchange or ""} for r in rows]


def fetch_fundamentals(
    engine=None,
    ticker: str | None = None,
    max_credits: int = 800,
) -> int:
    """
    Fetcha fondamentali per tutti i ticker attivi (o uno solo con --ticker).
    Priorità EODHD, fallback FMP se EODHD_TOKEN non configurato o ticker non trovato.
    Ritorna numero di righe upsertate.
    """
    if engine is None:
        engine = get_engine()

    eodhd_token = getattr(settings, "eodhd_api_key", None) or os.getenv("EODHD_API_KEY", "")
    fmp_key     = getattr(settings, "fmp_api_key", None) or os.getenv("FMP_API_KEY", "")

    if not eodhd_token and not fmp_key:
        logger.error("Nessuna API key configurata. Aggiungi EODHD_API_KEY o FMP_API_KEY in .env")
        return 0

    guard   = CreditBudgetGuard(max_credits)
    tickers = _load_tickers(engine, ticker)

    if not tickers:
        logger.warning("Nessun ticker trovato in ticker_universe")
        return 0

    logger.info(f"Fetch fondamentali: {len(tickers)} ticker | budget {guard.remaining} crediti EODHD")

    rows: list[dict] = []

    for t in tickers:
        if guard.remaining == 0:
            logger.warning(f"Budget esaurito dopo {guard.used} fetch. Ticker rimanenti saltati.")
            break

        tk  = t["ticker"]
        tid = t["id"]
        ex  = t["exchange"]

        # EODHD
        if eodhd_token:
            eodhd_tk = _to_eodhd_ticker(tk, ex)
            if eodhd_tk is None:
                logger.debug(f"Skip {tk} (blacklist)")
                continue
            try:
                guard.charge(1)
                data = _fetch_eodhd_fundamentals(eodhd_tk, eodhd_token)
                if data:
                    rows.append(_parse_eodhd(data, tid))
                    logger.debug(f"EODHD OK: {tk} ({eodhd_tk})")
                    continue
            except BudgetExceededError:
                logger.warning("Budget EODHD raggiunto — switchando a FMP per i restanti")
                eodhd_token = ""   # disabilita EODHD per questo run

        # FMP fallback
        if fmp_key:
            base_tk = tk.split(".")[0]   # rimuovi suffisso exchange
            if base_tk.upper() in TICKER_BLACKLIST:
                continue
            data = _fetch_fmp_fundamentals(base_tk, fmp_key)
            if data:
                rows.append(_parse_fmp(data, tid))
                logger.debug(f"FMP fallback OK: {base_tk}")

    if not rows:
        logger.warning("Nessun dato fondamentale recuperato")
        return 0

    df = pd.DataFrame(rows)
    n  = upsert_dataframe(engine, df, "fundamentals_snapshot", ["ticker_id", "snapshot_date"])
    logger.info(
        f"DONE — {n} fondamentali upsertati | EODHD crediti usati: {guard.used}/{guard._max}"
    )
    return n


# ── Entry point ───────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Fetch fondamentali da EODHD/FMP")
    parser.add_argument("--ticker",      type=str, default=None,
                        help="Fetch un singolo ticker (es. AAPL)")
    parser.add_argument("--max-credits", type=int, default=800,
                        help="Max crediti EODHD da usare in questo run (default 800)")
    args = parser.parse_args()
    fetch_fundamentals(ticker=args.ticker, max_credits=args.max_credits)
    return 0


if __name__ == "__main__":
    # Smoke test: verifica connessione DB e stampa il primo ticker disponibile
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    engine = get_engine()
    rows = _load_tickers(engine)
    if rows:
        tk = rows[0]["ticker"]
        eodhd_fmt = _to_eodhd_ticker(tk, rows[0]["exchange"])
        print(f"DB OK -- primo ticker: {tk} -> EODHD format: {eodhd_fmt}")
        print(f"CreditBudgetGuard: {CreditBudgetGuard()}")
        print(f"Blacklist attiva: {sorted(TICKER_BLACKLIST)}")
    else:
        print("Nessun ticker in DB — esegui prima load_universe")
    sys.exit(0)
