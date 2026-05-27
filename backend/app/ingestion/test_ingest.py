"""
Primo script di ingestione end-to-end — Fase 0.

Scarica prezzi EOD via yfinance per 20 ticker di test e li scrive in prices_daily.
yfinance e' fonte di TEST/BACKUP, non primaria — vedi docs/DECISIONS.md ADR-003.

Uso:
    python -m app.ingestion.test_ingest
    python -m app.ingestion.test_ingest --days 90
    python -m app.ingestion.test_ingest --tickers AAPL,MSFT,VWCE.DE
"""
from datetime import date, timedelta
import argparse
import logging
import sys
from typing import Sequence

import os
import requests
import urllib3
import pandas as pd
from sqlalchemy import text  # noqa: F401 (usato in get_ticker_id_map)

# Bypass SSL per proxy aziendale
urllib3.disable_warnings()
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0.0.0 Safari/537.36")
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _BROWSER_UA})
_SESSION.verify = False

from app.core.db import get_engine, upsert_dataframe

logger = logging.getLogger(__name__)


# 20 ticker di test = 5 baseline + 5 sector ETF + 5 S&P 100 + 5 FTSE MIB
DEFAULT_TICKERS = [
    # Baseline
    "VWCE.DE", "SPY", "EFA", "SHY", "IWQU.L",
    # Sector ETF
    "XLK", "XLF", "XLE", "XLV", "XLY",
    # S&P 100 (blue chip)
    "AAPL", "MSFT", "GOOGL", "AMZN", "JPM",
    # FTSE MIB
    "ISP.MI", "UCG.MI", "ENI.MI", "ENEL.MI", "STLAM.MI",
]


def get_ticker_id_map(engine, tickers: Sequence[str]) -> dict[str, int]:
    """Ritorna {ticker: id} per i ticker presenti in ticker_universe."""
    placeholders = ", ".join(f":t{i}" for i in range(len(tickers)))
    params = {f"t{i}": t for i, t in enumerate(tickers)}
    sql = text(f"SELECT id, ticker FROM ticker_universe WHERE ticker IN ({placeholders})")
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {r.ticker: r.id for r in rows}


def fetch_prices_yfinance(tickers: Sequence[str], start: date, end: date) -> pd.DataFrame:
    """
    Scarica prezzi EOD da Yahoo Finance API (chart endpoint diretto).
    yfinance come libreria usa getcrumb che viene bloccato dal proxy aziendale;
    chiamiamo direttamente query2.finance.yahoo.com/v8/finance/chart che funziona.
    Ritorna DataFrame long-format: [ticker, date, open, high, low, close, adj_close, volume]
    """
    logger.info(f"Scarico {len(tickers)} ticker da Yahoo Finance ({start} -> {end})")
    start_ts = int(pd.Timestamp(start).timestamp())
    end_ts   = int(pd.Timestamp(end + timedelta(days=1)).timestamp())
    out_rows: list[dict] = []

    for tk in tickers:
        url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{tk}"
               f"?interval=1d&period1={start_ts}&period2={end_ts}")
        try:
            r = _SESSION.get(url, timeout=15)
            if r.status_code != 200:
                logger.warning(f"{tk}: HTTP {r.status_code}")
                continue
            data = r.json()
            result = (data.get("chart") or {}).get("result") or []
            if not result:
                logger.warning(f"{tk}: nessun dato")
                continue
            res   = result[0]
            meta  = res.get("meta", {})
            times = res.get("timestamp", [])
            ohlcv = res.get("indicators", {}).get("quote", [{}])[0]
            adjclose = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose", [])
            for i, ts in enumerate(times):
                close = (ohlcv.get("close") or [None])[i] if i < len(ohlcv.get("close") or []) else None
                if close is None:
                    continue
                out_rows.append({
                    "ticker":    tk,
                    "date":      pd.Timestamp(ts, unit="s", tz=meta.get("exchangeTimezoneName","UTC")).date(),
                    "open":      _none_if_nan((ohlcv.get("open") or [None])[i] if i < len(ohlcv.get("open") or []) else None),
                    "high":      _none_if_nan((ohlcv.get("high") or [None])[i] if i < len(ohlcv.get("high") or []) else None),
                    "low":       _none_if_nan((ohlcv.get("low") or [None])[i] if i < len(ohlcv.get("low") or []) else None),
                    "close":     float(close),
                    "adj_close": _none_if_nan(adjclose[i] if i < len(adjclose) else None),
                    "volume":    _none_if_nan((ohlcv.get("volume") or [None])[i] if i < len(ohlcv.get("volume") or []) else None, cast=int),
                })
        except Exception as e:
            logger.warning(f"{tk}: errore fetch — {e}")

    if not out_rows:
        logger.warning("Nessun dato scaricato")
        return pd.DataFrame()

    df = pd.DataFrame(out_rows)
    logger.info(f"Yahoo Finance: {len(df)} righe su {df['ticker'].nunique()} ticker")
    return df


def _none_if_nan(v, cast=float):
    if v is None or pd.isna(v):
        return None
    return cast(v)


def ingest_prices(tickers: Sequence[str], days: int) -> int:
    """End-to-end: yfinance -> ticker_id mapping -> prices_daily upsert."""
    engine = get_engine()

    # 1. Mappa ticker -> id (i ticker devono essere stati caricati in ticker_universe)
    id_map = get_ticker_id_map(engine, tickers)
    missing = [t for t in tickers if t not in id_map]
    if missing:
        logger.warning(f"Ticker mancanti in ticker_universe: {missing}")
        logger.warning("Esegui prima: python -m app.ingestion.load_universe")

    valid = [t for t in tickers if t in id_map]
    if not valid:
        logger.error("Nessun ticker valido da ingerire. Stop.")
        return 0

    # 2. Fetch yfinance
    end = date.today()
    start = end - timedelta(days=days)
    prices_df = fetch_prices_yfinance(valid, start, end)
    if prices_df.empty:
        return 0

    # 3. Inietta ticker_id + source + ingested_at
    prices_df["ticker_id"] = prices_df["ticker"].map(id_map)
    prices_df["source"] = "yfinance"
    prices_df = prices_df.drop(columns=["ticker"])

    # 4. Upsert
    n = upsert_dataframe(
        engine=engine,
        df=prices_df,
        table="prices_daily",
        conflict_cols=["ticker_id", "date"],
    )
    logger.info(f"DONE — {n} righe prezzo upsertate in prices_daily")
    return n


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Ingest prezzi EOD da yfinance (Fase 0 test)")
    parser.add_argument("--days", type=int, default=30,
                        help="Quanti giorni di storico scaricare (default 30)")
    parser.add_argument("--tickers", type=str, default=None,
                        help="Lista CSV di ticker; se assente usa i 20 di default")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else DEFAULT_TICKERS
    ingest_prices(tickers, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
