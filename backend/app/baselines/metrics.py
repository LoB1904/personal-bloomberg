"""
Calcolo metriche di performance per le strategie paper trading.

Funzione principale: compute_metrics(nav, benchmark_nav=None) → dict
Input: pd.Series indicizzata per data (ordinata ascending), valori = NAV portfolio.
"""
import numpy as np
import pandas as pd

RISK_FREE_ANNUAL = 0.04   # ~Euribor 3m attuale come proxy


def compute_metrics(nav: pd.Series, benchmark_nav: pd.Series | None = None) -> dict:
    """
    Calcola metriche per una serie NAV.

    Returns:
        dict con: total_return_pct, cagr_pct, volatility_ann_pct, sharpe,
                  max_drawdown_pct, calmar, win_rate_monthly_pct, alpha_pct
    """
    nav = nav.sort_index().dropna()
    if len(nav) < 2:
        return {}

    total_return_pct = float((nav.iloc[-1] / nav.iloc[0] - 1) * 100)

    days = (nav.index[-1] - nav.index[0]).days
    cagr_pct = float((nav.iloc[-1] / nav.iloc[0]) ** (365.0 / days) - 1) * 100 if days >= 90 else None

    daily_rets = nav.pct_change().dropna()
    vol_daily  = float(daily_rets.std())
    volatility_ann_pct = vol_daily * np.sqrt(252) * 100

    rf_daily = RISK_FREE_ANNUAL / 252
    sharpe = (
        float((daily_rets.mean() - rf_daily) / vol_daily * np.sqrt(252))
        if vol_daily > 1e-10 else None
    )

    peak = nav.cummax()
    dd   = (nav / peak - 1) * 100
    max_drawdown_pct = float(dd.min())

    calmar = (
        float(-cagr_pct / max_drawdown_pct)
        if (cagr_pct is not None and max_drawdown_pct < -0.01)
        else None
    )

    monthly = nav.resample("ME").last().pct_change().dropna()
    win_rate_monthly_pct = float((monthly > 0).mean() * 100) if len(monthly) >= 3 else None

    alpha_pct = None
    if benchmark_nav is not None and len(benchmark_nav) >= 2:
        common = nav.index.intersection(benchmark_nav.index)
        if len(common) >= 2:
            s = float((nav.loc[common[-1]] / nav.loc[common[0]] - 1) * 100)
            b = float((benchmark_nav.loc[common[-1]] / benchmark_nav.loc[common[0]] - 1) * 100)
            alpha_pct = round(s - b, 4)

    return {
        "total_return_pct":     round(total_return_pct, 4),
        "cagr_pct":             round(cagr_pct, 4) if cagr_pct is not None else None,
        "volatility_ann_pct":   round(volatility_ann_pct, 4),
        "sharpe":               round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown_pct":     round(max_drawdown_pct, 4),
        "calmar":               round(calmar, 4) if calmar is not None else None,
        "win_rate_monthly_pct": round(win_rate_monthly_pct, 2) if win_rate_monthly_pct is not None else None,
        "alpha_pct":            alpha_pct,
    }


def drawdown_series(nav: pd.Series) -> pd.Series:
    """Serie di drawdown (valori negativi, %) per ogni giorno."""
    nav = nav.sort_index().dropna()
    return (nav / nav.cummax() - 1) * 100
