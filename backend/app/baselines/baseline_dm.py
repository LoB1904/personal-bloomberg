"""
Baseline #3: Dual Momentum Antonacci (Global Equity Momentum)
strategy_code : baseline_dual_momentum
benchmark_code: SPY

Regole mensili (segnale calcolato sull'ultimo giorno del mese, eseguito il primo giorno del mese successivo):

1. Absolute Momentum: se SPY_12m < 0  → hold SHY (safe asset)
2. Relative Momentum:
   - SPY_12m ≥ EFA_12m   → hold SPY
   - EFA_12m  > SPY_12m  → hold EFA

Costo switch: 0.05% applicato al valore del portfolio quando si cambia posizione.
Usa total_return_index da benchmark_prices (adj_close, include dividendi).
Fallback su close se total_return_index non disponibile.
"""
from __future__ import annotations
from datetime import date
import logging

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe

logger = logging.getLogger(__name__)

STRATEGY_CODE    = "baseline_dual_momentum"
BENCHMARK_CODE   = "SPY"
STARTING_CAPITAL = 100_000.0
SWITCH_COST      = 0.0005   # 0.05%
LOOKBACK_MONTHS  = 12


# ──────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────

def _load_benchmark(engine, code: str) -> pd.Series:
    """
    Carica una serie prezzo da benchmark_prices.
    Preferisce total_return_index (adj), fallback su close.
    """
    sql = text("""
        SELECT date,
               COALESCE(total_return_index, close) AS price
        FROM benchmark_prices
        WHERE benchmark_code = :code
        ORDER BY date ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"code": code}, parse_dates=["date"])
    if df.empty:
        return pd.Series(dtype=float, name=code)
    return df.set_index("date")["price"].sort_index().rename(code)


# ──────────────────────────────────────────────
# Signal computation
# ──────────────────────────────────────────────

def _compute_monthly_signals(
    spy: pd.Series, efa: pd.Series, shy: pd.Series
) -> pd.Series:
    """
    Calcola il segnale mensile per ciascun mese con almeno 12 mesi di storico.
    Ritorna pd.Series {last_day_of_month → 'SPY'|'EFA'|'SHY'}.
    Il segnale è calcolato sull'ultimo giorno del mese e applicato dal primo giorno del mese successivo.
    """
    # Allinea sulle date comuni
    common = spy.index.intersection(efa.index).intersection(shy.index)
    spy, efa, shy = spy.loc[common], efa.loc[common], shy.loc[common]

    # Monthly end prices
    spy_m = spy.resample("ME").last()
    efa_m = efa.resample("ME").last()
    shy_m = shy.resample("ME").last()  # noqa: F841 — disponibile per estensioni

    signals: dict[pd.Timestamp, str] = {}

    for i, dt in enumerate(spy_m.index):
        # Cerca il prezzo 12 mesi prima (usa la data del mese -12)
        target_prev = dt - pd.DateOffset(months=LOOKBACK_MONTHS)
        # Trova il mese end più vicino precedente a target_prev
        prev_candidates = spy_m.index[spy_m.index <= target_prev]
        if len(prev_candidates) == 0:
            continue   # non abbastanza storico

        prev_dt   = prev_candidates[-1]
        spy_12m   = float(spy_m.loc[dt]) / float(spy_m.loc[prev_dt]) - 1
        efa_12m   = float(efa_m.loc[dt]) / float(efa_m.loc[prev_dt]) - 1

        # Regola Antonacci
        if spy_12m < 0:
            signals[dt] = "SHY"
        elif spy_12m >= efa_12m:
            signals[dt] = "SPY"
        else:
            signals[dt] = "EFA"

    return pd.Series(signals)


# ──────────────────────────────────────────────
# Daily simulation
# ──────────────────────────────────────────────

def run(engine=None) -> int:
    """
    Calcola/aggiorna paper_strategy_daily per baseline_dual_momentum.
    Idempotente. Ritorna righe processate.
    """
    if engine is None:
        engine = get_engine()

    spy = _load_benchmark(engine, "SPY")
    efa = _load_benchmark(engine, "EFA")
    shy = _load_benchmark(engine, "SHY")

    if spy.empty or efa.empty or shy.empty:
        logger.error("Prezzi SPY/EFA/SHY mancanti. Esegui prima: fetch_benchmarks")
        return 0

    # Segnali mensili: {last_day_of_month → asset_code}
    monthly_signals = _compute_monthly_signals(spy, efa, shy)
    if monthly_signals.empty:
        logger.error("Impossibile calcolare segnali: storico insufficiente (< 12 mesi)")
        return 0

    # Mappa asset_code → serie prezzi
    price_series: dict[str, pd.Series] = {"SPY": spy, "EFA": efa, "SHY": shy}

    # Trading days = unione date disponibili
    common_dates = spy.index.intersection(efa.index).intersection(shy.index).sort_values()

    # Inizializza: primo segnale disponibile
    first_signal_month = monthly_signals.index[0]
    # Il segnale del mese M viene eseguito il primo giorno del mese M+1
    first_exec_date = (first_signal_month + pd.DateOffset(months=1)).replace(day=1)
    exec_trading_days = common_dates[common_dates >= first_exec_date]
    if exec_trading_days.empty:
        logger.error("Nessun giorno di trading dopo il primo segnale")
        return 0

    # Stato iniziale
    current_asset = monthly_signals.iloc[0]
    nav           = STARTING_CAPITAL
    shares        = nav / float(price_series[current_asset].loc[exec_trading_days[0]])
    cash          = 0.0
    peak_nav      = nav
    prev_nav      = nav

    # Mappa month_end → segnale per lookup veloce
    signal_map = {ts.date(): sig for ts, sig in monthly_signals.items()}

    rows: list[dict] = []
    spy_0 = float(spy.loc[exec_trading_days[0]])

    for dt in exec_trading_days:
        dt_date  = dt.date() if hasattr(dt, "date") else dt
        price    = float(price_series[current_asset].loc[dt])
        nav      = shares * price + cash

        # Controlla se questo mese ha un nuovo segnale
        # Il segnale del mese scorso è applicato dall'inizio di questo mese
        # Cerca il segnale per il mese precedente (ultimo giorno del mese scorso)
        month_start = dt.replace(day=1)
        prev_month_end = month_start - pd.Timedelta(days=1)
        # Cerca segnale per prev_month_end (o il più vicino disponibile)
        candidates = [d for d in signal_map if d <= prev_month_end.date()]
        new_signal = signal_map[max(candidates)] if candidates else None

        if new_signal and new_signal != current_asset:
            # Switch: vendi posizione corrente, compra nuova
            switch_cost = nav * SWITCH_COST
            nav -= switch_cost
            cash = 0.0
            new_price = float(price_series[new_signal].loc[dt])
            shares    = nav / new_price
            price     = new_price
            nav       = shares * price   # ricalcola dopo switch
            current_asset = new_signal
            logger.debug(f"{dt_date}: switch → {current_asset} (costo {switch_cost:.2f})")

        peak_nav  = max(peak_nav, nav)
        drawdown  = (nav / peak_nav - 1) * 100
        total_ret = (nav / STARTING_CAPITAL - 1) * 100
        daily_ret = (nav / prev_nav - 1) * 100 if prev_nav > 0 else 0.0

        spy_today = spy.loc[dt] if dt in spy.index else None
        alpha = round(total_ret - (float(spy_today) / spy_0 - 1) * 100, 6) if spy_today is not None else None

        rows.append({
            "strategy_code":    STRATEGY_CODE,
            "date":             dt_date,
            "portfolio_value":  round(nav, 4),
            "cash_value":       round(cash, 4),
            "invested_value":   round(shares * price, 4),
            "cash_pct":         0.0,
            "num_positions":    1,
            "daily_return_pct": round(daily_ret, 6),
            "total_return_pct": round(total_ret, 6),
            "drawdown_pct":     round(drawdown, 6),
            "benchmark_code":   BENCHMARK_CODE,
            "alpha_pct":        alpha,
            "metadata":         f'{{"holding": "{current_asset}"}}',
        })
        prev_nav = nav

    if not rows:
        return 0

    df = pd.DataFrame(rows)
    n  = upsert_dataframe(engine, df, "paper_strategy_daily", ["strategy_code", "date"])

    final  = rows[-1]
    max_dd = min(r["drawdown_pct"] for r in rows)
    logger.info(
        f"Dual Momentum: {n} righe | holding {current_asset} | NAV {final['portfolio_value']:,.0f} "
        f"({final['total_return_pct']:+.2f}%) | MaxDD {max_dd:.2f}%"
    )
    return n
