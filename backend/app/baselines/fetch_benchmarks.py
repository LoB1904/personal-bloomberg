"""
Scarica prezzi storici per i benchmark (VWCE, SPY, EFA, SHY, IWQU) e li
inserisce in benchmark_prices. Stesso pattern SSL/proxy di test_ingest.py.

Uso:
    python -m app.baselines.fetch_benchmarks
    python -m app.baselines.fetch_benchmarks --days 3650
"""
from datetime import date, timedelta
import argparse
import logging
import os
import sys

import urllib3
import requests
import pandas as pd

from app.core.db import get_engine, upsert_dataframe

urllib3.disable_warnings()
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _BROWSER_UA})
_SESSION.verify = False

logger = logging.getLogger(__name__)

# ticker Yahoo → benchmark_code nel DB
BENCHMARKS: dict[str, str] = {
    "VWCE.DE": "VWCE",
    "SPY":     "SPY",
    "EFA":     "EFA",
    "SHY":     "SHY",
    "IWQU.L":  "IWQU",
}


def _fetch_one(ticker: str, code: str, start: date, end: date) -> list[dict]:
    """Fetch singolo ticker via Yahoo chart API."""
    start_ts = int(pd.Timestamp(start).timestamp())
    end_ts   = int(pd.Timestamp(end + timedelta(days=1)).timestamp())
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&period1={start_ts}&period2={end_ts}"
    )
    try:
        r = _SESSION.get(url, timeout=15)
        if r.status_code != 200:
            logger.warning(f"{ticker}: HTTP {r.status_code}")
            return []
        data = r.json()
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            logger.warning(f"{ticker}: nessun dato")
            return []
        res       = result[0]
        meta      = res.get("meta", {})
        times     = res.get("timestamp", [])
        quote     = res.get("indicators", {}).get("quote", [{}])[0]
        adjclose  = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose", [])
        rows = []
        for i, ts in enumerate(times):
            close = (quote.get("close") or [None])[i] if i < len(quote.get("close") or []) else None
            if close is None:
                continue
            adj = adjclose[i] if i < len(adjclose) else None
            rows.append({
                "benchmark_code":     code,
                "date":               pd.Timestamp(ts, unit="s", tz=meta.get("exchangeTimezoneName", "UTC")).date(),
                "close":              float(close),
                "total_return_index": float(adj) if adj is not None and not pd.isna(adj) else None,
                "source":             "yfinance",
            })
        logger.info(f"{ticker} ({code}): {len(rows)} righe")
        return rows
    except Exception as e:
        logger.warning(f"{ticker}: errore — {e}")
        return []


def fetch_benchmarks(days: int = 3650) -> int:
    """Fetcha tutti i benchmark e fa upsert in benchmark_prices. Ritorna righe processate."""
    engine = get_engine()
    end   = date.today()
    start = end - timedelta(days=days)
    logger.info(f"Fetch benchmark: {start} → {end} ({days} giorni, {len(BENCHMARKS)} ticker)")

    all_rows: list[dict] = []
    for ticker, code in BENCHMARKS.items():
        all_rows.extend(_fetch_one(ticker, code, start, end))

    if not all_rows:
        logger.warning("Nessun dato scaricato")
        return 0

    df = pd.DataFrame(all_rows)
    n = upsert_dataframe(engine, df, "benchmark_prices", ["benchmark_code", "date"])
    logger.info(f"DONE — {n} righe in benchmark_prices ({df['benchmark_code'].nunique()} benchmark)")
    return n


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Fetch prezzi benchmark (VWCE/SPY/EFA/SHY/IWQU)")
    parser.add_argument("--days", type=int, default=3650, help="Giorni di storico (default 3650 = ~10 anni)")
    args = parser.parse_args()
    fetch_benchmarks(args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
