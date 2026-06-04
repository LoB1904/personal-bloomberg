"""
Fase 2 — FastAPI endpoints per Personal Bloomberg.

Endpoints:
    GET /screener/{profile}     — ultima shortlist screener da DB
    GET /paper/signals          — segnali paper trading con P&L
    GET /paper/track-record     — metriche aggregate + confronto vs baseline
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.core.db import get_engine

router = APIRouter()

_VALID_PROFILES = {"speculative_trend_etf", "wealth_quality_garp"}
_VALID_STATUSES = {"open", "closed_12m"}

# Finestre per track-record
_WINDOWS = [
    (7,   "1w"),
    (30,  "1m"),
    (91,  "3m"),
    (182, "6m"),
    (365, "12m"),
]


def _engine():
    return get_engine()


# ── GET /screener/{profile} ───────────────────────────────────────────

@router.get("/screener/{profile}")
def get_screener(profile: str) -> dict[str, Any]:
    """
    Ritorna l'ultima shortlist per il profilo specificato.

    Path param:
        profile: 'speculative_trend_etf' | 'wealth_quality_garp'

    Response:
        {
            "profile":   str,
            "run_date":  str (YYYY-MM-DD),
            "n_signals": int,
            "shortlist": [
                {
                    "rank":           int,
                    "ticker":         str,
                    "score":          float,
                    "signal_details": dict,
                }
            ]
        }
    """
    if profile not in _VALID_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Profile non valido. Valori ammessi: {sorted(_VALID_PROFILES)}",
        )

    engine = _engine()

    # Trova la run_date più recente per questo profilo
    sql_date = text("""
        SELECT MAX(run_date)
        FROM screener_results
        WHERE screener_profile = :p
    """)
    with engine.connect() as conn:
        latest_date = conn.execute(sql_date, {"p": profile}).scalar()

    if latest_date is None:
        raise HTTPException(
            status_code=404,
            detail=f"Nessun risultato screener per il profilo '{profile}'",
        )

    # Carica la shortlist per quella data
    sql = text("""
        SELECT sr.rank, t.ticker, sr.score, sr.scores_json
        FROM screener_results sr
        JOIN ticker_universe t ON t.id = sr.ticker_id
        WHERE sr.screener_profile = :p
          AND sr.run_date = :d
        ORDER BY sr.rank
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"p": profile, "d": latest_date}).fetchall()

    shortlist = [
        {
            "rank":           r.rank,
            "ticker":         r.ticker,
            "score":          float(r.score),
            "signal_details": r.scores_json if isinstance(r.scores_json, dict) else {},
        }
        for r in rows
    ]

    return {
        "profile":   profile,
        "run_date":  latest_date.isoformat(),
        "n_signals": len(shortlist),
        "shortlist": shortlist,
    }


# ── GET /paper/signals ────────────────────────────────────────────────

@router.get("/paper/signals")
def get_paper_signals(
    profile: str | None = Query(default=None),
    status:  str | None = Query(default=None),
) -> dict[str, Any]:
    """
    Ritorna i segnali paper trading con P&L per finestra.

    Query params (opzionali):
        profile: 'speculative_trend_etf' | 'wealth_quality_garp'
        status:  'open' | 'closed_12m'

    Response:
        {
            "n_signals": int,
            "signals": [
                {
                    "id":               int,
                    "ticker":           str,
                    "screener_profile": str,
                    "entry_date":       str,
                    "entry_price":      float,
                    "status":           str,
                    "pnl": {
                        "1w":  float | null,
                        "1m":  float | null,
                        "3m":  float | null,
                        "6m":  float | null,
                        "12m": float | null,
                    }
                }
            ]
        }
    """
    if profile is not None and profile not in _VALID_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Profile non valido. Valori ammessi: {sorted(_VALID_PROFILES)}",
        )
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Status non valido. Valori ammessi: {sorted(_VALID_STATUSES)}",
        )

    base = """
        SELECT ps.id, t.ticker, ps.screener_profile,
               ps.entry_date, ps.entry_price, ps.status,
               ps.pnl_pct_1w, ps.pnl_pct_1m, ps.pnl_pct_3m,
               ps.pnl_pct_6m, ps.pnl_pct_12m
        FROM paper_signals ps
        JOIN ticker_universe t ON t.id = ps.ticker_id
        WHERE 1=1
    """
    params: dict[str, Any] = {}

    if profile:
        base += " AND ps.screener_profile = :profile"
        params["profile"] = profile
    if status:
        base += " AND ps.status = :status"
        params["status"] = status

    base += " ORDER BY ps.entry_date DESC, ps.screener_profile, t.ticker"

    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(text(base), params).fetchall()

    signals = [
        {
            "id":               r.id,
            "ticker":           r.ticker,
            "screener_profile": r.screener_profile,
            "entry_date":       r.entry_date.isoformat(),
            "entry_price":      float(r.entry_price),
            "status":           r.status,
            "pnl": {
                "1w":  float(r.pnl_pct_1w)  if r.pnl_pct_1w  is not None else None,
                "1m":  float(r.pnl_pct_1m)  if r.pnl_pct_1m  is not None else None,
                "3m":  float(r.pnl_pct_3m)  if r.pnl_pct_3m  is not None else None,
                "6m":  float(r.pnl_pct_6m)  if r.pnl_pct_6m  is not None else None,
                "12m": float(r.pnl_pct_12m) if r.pnl_pct_12m is not None else None,
            },
        }
        for r in rows
    ]

    return {"n_signals": len(signals), "signals": signals}


# ── GET /paper/track-record ───────────────────────────────────────────

@router.get("/paper/track-record")
def get_track_record(
    profile: str | None = Query(default=None),
) -> dict[str, Any]:
    """
    Metriche aggregate per i segnali screener + confronto vs baseline.

    Query params (opzionali):
        profile: filtra per profilo screener

    Response:
        {
            "screener": {
                "<profile>": {
                    "n_signals":   int,
                    "n_closed":    int,
                    "windows": {
                        "1w": {"avg_pnl": float|null, "hit_rate": float|null, "n": int},
                        ...
                    }
                }
            },
            "baselines": {
                "<strategy_code>": {
                    "start_date":     str,
                    "end_date":       str,
                    "total_return":   float,
                    "cagr":          float,
                    "max_drawdown":   float,
                    "sharpe":        float | null,
                }
            }
        }
    """
    if profile is not None and profile not in _VALID_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Profile non valido. Valori ammessi: {sorted(_VALID_PROFILES)}",
        )

    engine = _engine()

    # ── Screener signal metrics ────────────────────────────────────────
    base = """
        SELECT ps.screener_profile, ps.status,
               ps.pnl_pct_1w, ps.pnl_pct_1m, ps.pnl_pct_3m,
               ps.pnl_pct_6m, ps.pnl_pct_12m
        FROM paper_signals ps
        WHERE 1=1
    """
    params: dict[str, Any] = {}
    if profile:
        base += " AND ps.screener_profile = :profile"
        params["profile"] = profile
    base += " ORDER BY ps.screener_profile"

    with engine.connect() as conn:
        rows = conn.execute(text(base), params).fetchall()

    by_profile: dict[str, list[dict]] = {}
    for r in rows:
        by_profile.setdefault(r.screener_profile, []).append(dict(r._mapping))

    screener_metrics: dict[str, Any] = {}
    for prof, signals in by_profile.items():
        n_total  = len(signals)
        n_closed = sum(1 for s in signals if s["status"] == "closed_12m")
        windows: dict[str, dict] = {}

        for _, suffix in _WINDOWS:
            col    = f"pnl_pct_{suffix}"
            values = [float(s[col]) for s in signals if s.get(col) is not None]
            if not values:
                windows[suffix] = {"avg_pnl": None, "hit_rate": None, "n": 0}
            else:
                n = len(values)
                windows[suffix] = {
                    "avg_pnl":  round(sum(values) / n, 4),
                    "hit_rate": round(sum(1 for v in values if v > 0) / n * 100, 1),
                    "n":        n,
                }

        screener_metrics[prof] = {
            "n_signals": n_total,
            "n_closed":  n_closed,
            "windows":   windows,
        }

    # ── Baseline metrics da paper_strategy_daily ───────────────────────
    sql_base = text("""
        SELECT strategy_code, date,
               daily_return_pct, total_return_pct, drawdown_pct
        FROM paper_strategy_daily
        ORDER BY strategy_code, date
    """)
    with engine.connect() as conn:
        base_rows = conn.execute(sql_base).fetchall()

    by_strategy: dict[str, list] = {}
    for r in base_rows:
        by_strategy.setdefault(r.strategy_code, []).append(r)

    baseline_metrics: dict[str, Any] = {}
    for strat, strat_rows in by_strategy.items():
        n = len(strat_rows)
        if n < 2:
            continue

        start_date = strat_rows[0].date
        end_date   = strat_rows[-1].date
        years      = (end_date - start_date).days / 365.25

        total_ret = float(strat_rows[-1].total_return_pct)
        max_dd    = min(float(r.drawdown_pct) for r in strat_rows if r.drawdown_pct is not None)

        # CAGR: (1 + total_ret/100)^(1/years) - 1
        cagr = ((1 + total_ret / 100) ** (1 / years) - 1) * 100 if years > 0 else None

        # Sharpe: media / std dei daily_return_pct * sqrt(252)
        daily_rets = [
            float(r.daily_return_pct)
            for r in strat_rows
            if r.daily_return_pct is not None
        ]
        sharpe = None
        if len(daily_rets) > 30:
            mean = sum(daily_rets) / len(daily_rets)
            variance = sum((x - mean) ** 2 for x in daily_rets) / len(daily_rets)
            std = math.sqrt(variance)
            if std > 0:
                sharpe = round((mean / std) * math.sqrt(252), 3)

        baseline_metrics[strat] = {
            "start_date":   start_date.isoformat(),
            "end_date":     end_date.isoformat(),
            "total_return": round(total_ret, 2),
            "cagr":         round(cagr, 2) if cagr is not None else None,
            "max_drawdown": round(max_dd, 2),
            "sharpe":       sharpe,
        }

    return {
        "screener":  screener_metrics,
        "baselines": baseline_metrics,
    }
