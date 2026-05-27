"""
Baseline #1: Buy & Hold VWCE.DE
strategy_code : baseline_bh_vwce
benchmark_code: VWCE

Day 1: compra quante quote VWCE possibili con 100.000 capital (arrotondamento intero).
Non vende mai. VWCE è un ETF ad accumulazione, i dividendi sono già incorporati nel prezzo.
Alpha vs benchmark = 0 per definizione (è la stessa serie, a meno del cash residuo giorno 1).
"""
from __future__ import annotations
from datetime import date
import logging

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe

logger = logging.getLogger(__name__)

STRATEGY_CODE    = "baseline_bh_vwce"
BENCHMARK_CODE   = "VWCE"
STARTING_CAPITAL = 100_000.0


def _load_vwce_prices(engine) -> pd.Series:
    """Serie VWCE close price, indicizzata per data, ordinata ascending."""
    sql = text("""
        SELECT date, close FROM benchmark_prices
        WHERE benchmark_code = 'VWCE'
        ORDER BY date ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, parse_dates=["date"])
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("date")["close"].sort_index()


def run(engine=None) -> int:
    """
    Calcola/aggiorna paper_strategy_daily per baseline_bh_vwce.
    Idempotente: ricalcola sempre dall'inizio e fa upsert.
    Ritorna righe processate.
    """
    if engine is None:
        engine = get_engine()

    prices = _load_vwce_prices(engine)
    if prices.empty:
        logger.error("Nessun prezzo VWCE in benchmark_prices. Esegui prima: fetch_benchmarks")
        return 0

    # Posizione iniziale: acquisto intero massimo
    price_0 = float(prices.iloc[0])
    shares  = int(STARTING_CAPITAL // price_0)          # quote intere
    cash    = STARTING_CAPITAL - shares * price_0       # residuo cash (non investito)

    rows     = []
    peak_nav = STARTING_CAPITAL
    prev_nav = STARTING_CAPITAL

    for dt, price in prices.items():
        price = float(price)
        nav   = shares * price + cash

        peak_nav  = max(peak_nav, nav)
        drawdown  = (nav / peak_nav - 1) * 100
        total_ret = (nav / STARTING_CAPITAL - 1) * 100
        daily_ret = (nav / prev_nav - 1) * 100 if prev_nav > 0 else 0.0

        # Alpha vs VWCE = ~0 (lo stesso asset); piccola divergenza da cash residuo
        benchmark_nav_0 = prices.iloc[0]
        benchmark_ret   = (price / float(benchmark_nav_0) - 1) * 100
        alpha           = total_ret - benchmark_ret

        rows.append({
            "strategy_code":   STRATEGY_CODE,
            "date":            dt.date() if hasattr(dt, "date") else dt,
            "portfolio_value": round(nav, 4),
            "cash_value":      round(cash, 4),
            "invested_value":  round(shares * price, 4),
            "cash_pct":        round(cash / nav * 100, 4) if nav > 0 else 0.0,
            "num_positions":   1,
            "daily_return_pct":  round(daily_ret, 6),
            "total_return_pct":  round(total_ret, 6),
            "drawdown_pct":      round(drawdown, 6),
            "benchmark_code":    BENCHMARK_CODE,
            "alpha_pct":         round(alpha, 6),
        })
        prev_nav = nav

    if not rows:
        return 0

    df = pd.DataFrame(rows)
    n  = upsert_dataframe(engine, df, "paper_strategy_daily", ["strategy_code", "date"])

    final    = rows[-1]
    max_dd   = min(r["drawdown_pct"] for r in rows)
    logger.info(
        f"B&H VWCE: {n} righe | NAV {final['portfolio_value']:,.0f} "
        f"({final['total_return_pct']:+.2f}%) | MaxDD {max_dd:.2f}%"
    )
    return n
