"""
Baseline #2: Equal-Weight S&P100
strategy_code : baseline_ew_sp100
benchmark_code: SPY

Day 1: 100.000 / N diviso uguale su tutti gli S&P100 con prezzi disponibili.
       Costo per ogni trade: 0.10% del valore compravenduto.
Rebalance trimestrale: ultimo venerdì di marzo, giugno, settembre, dicembre.
       Se il venerdì non è un giorno di borsa, si usa il primo giorno disponibile successivo.
NAV giornaliero: somma posizioni mark-to-market + cash.
Usa quote frazionarie (paper trading).
"""
from __future__ import annotations
import calendar
from datetime import date, timedelta
import logging

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe

logger = logging.getLogger(__name__)

STRATEGY_CODE    = "baseline_ew_sp100"
BENCHMARK_CODE   = "SPY"
STARTING_CAPITAL = 100_000.0
TRADE_COST       = 0.001   # 0.10% per trade

# Mesi di rebalance trimestrale
_REBALANCE_MONTHS = {3, 6, 9, 12}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _last_friday(year: int, month: int) -> date:
    """Ultimo venerdì del mese."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    offset = (d.weekday() - 4) % 7   # weekday() 4 = venerdì
    return d - timedelta(days=offset)


def _rebalance_dates(start: date, end: date, trading_days: pd.DatetimeIndex) -> set[date]:
    """
    Restituisce le date di rebalance effettive (primo giorno di borsa ≥ ultimo venerdì
    del mese di rebalance), nell'intervallo [start, end].
    """
    trading_set = {ts.date() for ts in trading_days}
    result: set[date] = set()
    for year in range(start.year, end.year + 1):
        for month in _REBALANCE_MONTHS:
            target = _last_friday(year, month)
            # Trova il primo giorno di borsa ≥ target
            d = target
            for _ in range(10):   # max 10 giorni avanti
                if start <= d <= end and d in trading_set:
                    result.add(d)
                    break
                d += timedelta(days=1)
    return result


# ──────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────

def _load_sp100_prices(engine) -> pd.DataFrame:
    """
    Carica prezzi adj_close (fallback: close) per tutti i ticker S&P100 in prices_daily.
    Ritorna DataFrame wide: index=date, columns=ticker_id.
    Forward-fill per coprire giorni non di borsa del singolo ticker.
    """
    sql = text("""
        SELECT p.date, p.ticker_id,
               COALESCE(p.adj_close, p.close) AS price
        FROM prices_daily p
        JOIN ticker_universe t ON t.id = p.ticker_id
        WHERE t.universe_group = 'sp100'
          AND t.is_active = TRUE
        ORDER BY p.date ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, parse_dates=["date"])

    if df.empty:
        return pd.DataFrame()

    wide = df.pivot(index="date", columns="ticker_id", values="price")
    wide = wide.sort_index().ffill()
    return wide


def _load_spy_prices(engine) -> pd.Series:
    """Serie SPY close da benchmark_prices, per calcolo alpha."""
    sql = text("""
        SELECT date, close FROM benchmark_prices
        WHERE benchmark_code = 'SPY'
        ORDER BY date ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, parse_dates=["date"])
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("date")["close"].sort_index()


# ──────────────────────────────────────────────
# Core
# ──────────────────────────────────────────────

def run(engine=None) -> int:
    """
    Calcola/aggiorna paper_strategy_daily per baseline_ew_sp100.
    Idempotente. Ritorna righe processate.
    """
    if engine is None:
        engine = get_engine()

    price_matrix = _load_sp100_prices(engine)
    if price_matrix.empty:
        logger.error("Nessun prezzo S&P100 in prices_daily. Esegui prima l'ingestione.")
        return 0

    spy = _load_spy_prices(engine)

    trading_days  = price_matrix.index
    rebalance_set = _rebalance_dates(trading_days[0].date(), trading_days[-1].date(), trading_days)

    # Day 1 — acquisto iniziale con tutti i ticker che hanno prezzo
    day0_prices = price_matrix.iloc[0].dropna()
    N           = len(day0_prices)
    if N == 0:
        logger.error("Nessun ticker con prezzo al giorno 1")
        return 0

    target_per_ticker = STARTING_CAPITAL / N
    # Quote frazionarie, costo 0.10% detratto dalla posizione
    shares: dict[int, float] = {}
    total_cost = 0.0
    for tid, price in day0_prices.items():
        s = (target_per_ticker * (1 - TRADE_COST)) / float(price)
        shares[tid] = s
        total_cost += target_per_ticker * TRADE_COST

    invested = sum(shares[t] * float(day0_prices[t]) for t in shares)
    cash = STARTING_CAPITAL - invested - total_cost

    rows:     list[dict] = []
    peak_nav = STARTING_CAPITAL
    prev_nav = STARTING_CAPITAL
    spy_0    = float(spy.iloc[0]) if not spy.empty else None

    for dt in trading_days:
        prices_today = price_matrix.loc[dt].dropna()
        dt_date = dt.date() if hasattr(dt, "date") else dt

        # Rebalance se necessario (PRIMA di calcolare il NAV del giorno)
        if dt_date in rebalance_set and rows:   # non al primo giorno
            nav_pre = sum(shares.get(t, 0) * float(prices_today.get(t, 0)) for t in shares) + cash
            n_now   = len(prices_today)
            if n_now > 0:
                target = nav_pre / n_now
                reb_cost = 0.0
                for tid in list(shares.keys()):
                    cur_val = shares[tid] * float(prices_today.get(tid, 0))
                    reb_cost += abs(target - cur_val) * TRADE_COST
                nav_net = nav_pre - reb_cost
                new_target = nav_net / n_now
                shares = {}
                for tid, price in prices_today.items():
                    shares[tid] = new_target / float(price)
                invested_new = sum(shares[t] * float(prices_today[t]) for t in shares)
                cash = nav_net - invested_new

        # NAV giornaliero
        invested_val = sum(shares.get(t, 0) * float(prices_today.get(t, 0)) for t in shares)
        nav = invested_val + cash

        peak_nav  = max(peak_nav, nav)
        drawdown  = (nav / peak_nav - 1) * 100
        total_ret = (nav / STARTING_CAPITAL - 1) * 100
        daily_ret = (nav / prev_nav - 1) * 100 if prev_nav > 0 else 0.0

        # Alpha vs SPY
        alpha = None
        if spy_0 is not None:
            spy_today = spy.get(dt) if hasattr(spy, "get") else (spy.loc[dt] if dt in spy.index else None)
            if spy_today is not None:
                spy_ret = (float(spy_today) / spy_0 - 1) * 100
                alpha   = round(total_ret - spy_ret, 6)

        rows.append({
            "strategy_code":    STRATEGY_CODE,
            "date":             dt_date,
            "portfolio_value":  round(nav, 4),
            "cash_value":       round(cash, 4),
            "invested_value":   round(invested_val, 4),
            "cash_pct":         round(cash / nav * 100, 4) if nav > 0 else 0.0,
            "num_positions":    len(shares),
            "daily_return_pct": round(daily_ret, 6),
            "total_return_pct": round(total_ret, 6),
            "drawdown_pct":     round(drawdown, 6),
            "benchmark_code":   BENCHMARK_CODE,
            "alpha_pct":        alpha,
        })
        prev_nav = nav

    if not rows:
        return 0

    df = pd.DataFrame(rows)
    n  = upsert_dataframe(engine, df, "paper_strategy_daily", ["strategy_code", "date"])

    final  = rows[-1]
    max_dd = min(r["drawdown_pct"] for r in rows)
    logger.info(
        f"EW S&P100: {n} righe | N={N} ticker | NAV {final['portfolio_value']:,.0f} "
        f"({final['total_return_pct']:+.2f}%) | MaxDD {max_dd:.2f}%"
    )
    return n
