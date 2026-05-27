"""
Orchestratore Fase 0.5 — Baseline passive strategies.

Sequenza:
  1. Fetch prezzi benchmark (VWCE/SPY/EFA/SHY/IWQU) → benchmark_prices
  2. Fetch prezzi S&P100 (103 ticker) → prices_daily  [opzionale con --skip-sp100]
  3. Run B&H VWCE
  4. Run Equal-Weight S&P100
  5. Run Dual Momentum Antonacci
  6. Stampa tabella riepilogo metriche

Uso:
    python -m app.baselines.run_baselines
    python -m app.baselines.run_baselines --days 3650 --baseline bh
    python -m app.baselines.run_baselines --skip-sp100     # salta fetch S&P100
"""
import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import urllib3
import requests
import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe
from app.baselines.fetch_benchmarks import fetch_benchmarks
from app.baselines import baseline_bh, baseline_ew, baseline_dm
from app.baselines.metrics import compute_metrics

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

# Cartella universe/ (relativa al backend/)
_UNIVERSE_DIR = Path(__file__).parents[2] / "universe"


# ──────────────────────────────────────────────
# S&P100 price ingestion
# ──────────────────────────────────────────────

def _read_sp100_tickers() -> list[str]:
    csv_path = _UNIVERSE_DIR / "sp100.csv"
    if not csv_path.exists():
        logger.warning(f"sp100.csv non trovato in {_UNIVERSE_DIR}")
        return []
    df = pd.read_csv(csv_path)
    return df["ticker"].dropna().tolist()


def _get_ticker_id_map(engine, tickers: list[str]) -> dict[str, int]:
    placeholders = ", ".join(f":t{i}" for i in range(len(tickers)))
    params = {f"t{i}": t for i, t in enumerate(tickers)}
    sql = text(f"SELECT id, ticker FROM ticker_universe WHERE ticker IN ({placeholders})")
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {r.ticker: r.id for r in rows}


def _fetch_prices_yahoo(ticker: str, start: date, end: date) -> list[dict]:
    """Fetch prezzi per un singolo ticker via Yahoo Finance chart API."""
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
            adj = adjclose[i] if i < len(adjclose) else None

            def _safe(v, cast=float):
                return cast(v) if v is not None and not pd.isna(v) else None

            rows.append({
                "ticker":    ticker,
                "date":      pd.Timestamp(ts, unit="s", tz=meta.get("exchangeTimezoneName", "UTC")).date(),
                "open":      _safe((quote.get("open") or [None])[i] if i < len(quote.get("open") or []) else None),
                "high":      _safe((quote.get("high") or [None])[i] if i < len(quote.get("high") or []) else None),
                "low":       _safe((quote.get("low") or [None])[i] if i < len(quote.get("low") or []) else None),
                "close":     float(close),
                "adj_close": _safe(adj),
                "volume":    _safe((quote.get("volume") or [None])[i] if i < len(quote.get("volume") or []) else None, cast=int),
            })
        return rows
    except Exception as e:
        logger.warning(f"{ticker}: errore — {e}")
        return []


def ingest_sp100_prices(engine, days: int) -> int:
    """Fetcha prezzi per tutti i ticker S&P100 e li scrive in prices_daily."""
    tickers = _read_sp100_tickers()
    if not tickers:
        return 0

    id_map = _get_ticker_id_map(engine, tickers)
    missing = [t for t in tickers if t not in id_map]
    if missing:
        logger.warning(f"{len(missing)} ticker S&P100 non in ticker_universe: {missing[:5]}{'...' if len(missing)>5 else ''}")
        logger.warning("Esegui prima: python -m app.ingestion.load_universe")

    valid = [t for t in tickers if t in id_map]
    if not valid:
        logger.error("Nessun ticker S&P100 valido. Esegui prima load_universe.")
        return 0

    logger.info(f"Fetch prezzi S&P100: {len(valid)} ticker, {days} giorni")
    end   = date.today()
    start = end - timedelta(days=days)

    all_rows: list[dict] = []
    for tk in valid:
        rows = _fetch_prices_yahoo(tk, start, end)
        for row in rows:
            row["ticker_id"] = id_map[tk]
            row["source"]    = "yfinance"
            del row["ticker"]
        all_rows.extend(rows)

    if not all_rows:
        return 0

    df = pd.DataFrame(all_rows)
    n  = upsert_dataframe(engine, df, "prices_daily", ["ticker_id", "date"])
    logger.info(f"S&P100 prezzi: {n} righe in prices_daily ({df['ticker_id'].nunique()} ticker)")
    return n


# ──────────────────────────────────────────────
# Summary table
# ──────────────────────────────────────────────

def _print_summary(engine) -> None:
    sql = text("""
        SELECT strategy_code, date, portfolio_value, total_return_pct, drawdown_pct
        FROM paper_strategy_daily
        WHERE strategy_code IN ('baseline_bh_vwce','baseline_ew_sp100','baseline_dual_momentum')
        ORDER BY strategy_code, date ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, parse_dates=["date"])

    if df.empty:
        print("Nessun dato in paper_strategy_daily.")
        return

    print()
    print("=" * 80)
    print(f"{'Strategia':<30} {'Giorni':>6} {'Return%':>9} {'MaxDD%':>8} {'NAV finale':>12}")
    print("-" * 80)

    for code, grp in df.groupby("strategy_code"):
        grp = grp.sort_values("date")
        nav = grp.set_index("date")["portfolio_value"]
        m   = compute_metrics(nav)
        days_n  = len(grp)
        ret     = m.get("total_return_pct", 0)
        maxdd   = m.get("max_drawdown_pct", 0)
        final   = float(grp["portfolio_value"].iloc[-1])
        cagr    = m.get("cagr_pct")
        sharpe  = m.get("sharpe")
        cagr_s  = f"{cagr:+.2f}%" if cagr else "n/a"
        shr_s   = f"{sharpe:.2f}" if sharpe else "n/a"
        print(
            f"{code:<30} {days_n:>6} {ret:>+8.2f}% {maxdd:>7.2f}%"
            f"  {final:>12,.0f}  CAGR {cagr_s}  Sharpe {shr_s}"
        )

    print("=" * 80)
    print()


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Fase 0.5 — Baseline passive strategies")
    parser.add_argument("--days",       type=int,   default=3650,
                        help="Giorni di storico da scaricare (default 3650 = ~10 anni)")
    parser.add_argument("--baseline",   type=str,   default="all",
                        choices=["all", "bh", "ew", "dm"],
                        help="Quale baseline eseguire (default: all)")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Salta il fetch prezzi (usa dati già in DB)")
    parser.add_argument("--skip-sp100", action="store_true",
                        help="Salta il fetch prezzi S&P100 (solo benchmark)")
    args = parser.parse_args()

    engine = get_engine()

    # ── 1. Fetch prezzi ──
    if not args.skip_fetch:
        logger.info("=" * 50)
        logger.info("Step 1/2 — Fetch benchmark prezzi")
        fetch_benchmarks(args.days)

        if not args.skip_sp100:
            logger.info("Step 2/2 — Fetch S&P100 prezzi")
            ingest_sp100_prices(engine, args.days)
        logger.info("=" * 50)

    # ── 2. Run baselines ──
    run_bh = args.baseline in ("all", "bh")
    run_ew = args.baseline in ("all", "ew")
    run_dm = args.baseline in ("all", "dm")

    if run_bh:
        logger.info("[1/3] Buy & Hold VWCE...")
        baseline_bh.run(engine)

    if run_ew:
        logger.info("[2/3] Equal-Weight S&P100...")
        baseline_ew.run(engine)

    if run_dm:
        logger.info("[3/3] Dual Momentum Antonacci...")
        baseline_dm.run(engine)

    # ── 3. Summary ──
    _print_summary(engine)

    return 0


if __name__ == "__main__":
    sys.exit(main())
