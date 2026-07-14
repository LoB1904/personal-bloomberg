"""
Fase 2 — Screener Orchestrator.

Coordina due screener paralleli e scrive i risultati in screener_results:

  Speculative (trend_etf):
    TrendFollowingScreener.screen_all() → ETF in LONG
    Equal-weight tra tutti gli ETF in trend

  Wealth (quality_garp):
    FundamentalScreener.combined_score() su tutti i single-name
    Top 30 per combined_score, equal-weight

I due screener sono indipendenti: se uno fallisce l'altro continua.

Uso:
    python -m app.analytics.screener
    python -m app.analytics.screener --profile speculative
    python -m app.analytics.screener --profile wealth --top-n 20
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe
from app.analytics.trend import TrendFollowingScreener
from app.analytics.fundamental import FundamentalScreener

logger = logging.getLogger(__name__)

_PROFILE_SPECULATIVE = "speculative_trend_etf"
_PROFILE_WEALTH      = "wealth_quality_garp"


class ScreenerOrchestrator:
    """Coordina Speculative e Wealth screener, scrive risultati in DB."""

    def __init__(self, engine=None):
        self.engine     = engine or get_engine()
        self.trend      = TrendFollowingScreener(self.engine)
        self.fundamental = FundamentalScreener(self.engine)

    # ── Helpers DB ────────────────────────────────────────────────────

    def _fetch_single_name_ids(self) -> list[dict]:
        """Ticker single-name attivi (sp100, sp500, ftsemib, wildcard)."""
        sql = text("""
            SELECT id, ticker
            FROM ticker_universe
            WHERE universe_group IN ('sp100', 'sp500', 'ftsemib', 'wildcard')
              AND is_active = TRUE
            ORDER BY id
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [{"id": r.id, "ticker": r.ticker} for r in rows]

    # ── Speculative Screener ──────────────────────────────────────────

    def run_speculative(self) -> dict[str, Any]:
        """
        Trend-Following su sector ETF.
        Ritorna dict con shortlist equal-weight + metadata.

        Output:
            {
                "profile":    "speculative_trend_etf",
                "run_date":   date,
                "shortlist":  [{ticker_id, ticker, rank, score, weight, scores_json}, ...],
                "n_signals":  int,
                "status":     "success" | "error",
                "error":      str | None,
            }
        """
        try:
            long_etfs = self.trend.screen_all()
            n         = len(long_etfs)
            weight    = round(1.0 / n, 6) if n > 0 else 0.0

            shortlist = [
                {
                    "ticker_id":   r["ticker_id"],
                    "ticker":      r["ticker"],
                    "rank":        i + 1,
                    "score":       r["trend_score"],
                    "weight":      weight,
                    "scores_json": {
                        "price":     r.get("price"),
                        "ma200":     r.get("ma200"),
                        "ma50":      r.get("ma50"),
                        "return_3m": r.get("return_3m"),
                        "return_6m": r.get("return_6m"),
                        "signal":    r.get("signal"),
                        "weight":    weight,
                    },
                }
                for i, r in enumerate(long_etfs)
            ]

            logger.info(f"Speculative: {n} ETF in LONG")
            return {
                "profile":   _PROFILE_SPECULATIVE,
                "run_date":  date.today(),
                "shortlist": shortlist,
                "n_signals": n,
                "status":    "success",
                "error":     None,
            }

        except Exception as e:
            logger.error(f"Speculative screener fallito: {e}", exc_info=True)
            return {
                "profile":   _PROFILE_SPECULATIVE,
                "run_date":  date.today(),
                "shortlist": [],
                "n_signals": 0,
                "status":    "error",
                "error":     str(e),
            }

    # ── Wealth Screener ───────────────────────────────────────────────

    def run_wealth(self, top_n: int = 30) -> dict[str, Any]:
        """
        Quality + GARP su single-name.
        Ritorna top_n per combined_score, equal-weight.

        Output: stessa struttura di run_speculative().
        """
        try:
            tickers = self._fetch_single_name_ids()
            logger.info(f"Wealth: calcolo score su {len(tickers)} single-name")

            scores: list[dict] = []
            for t in tickers:
                try:
                    result = self.fundamental.combined_score(t["id"])
                    if result.get("combined_score") is not None:
                        scores.append({
                            "ticker_id":     t["id"],
                            "ticker":        t["ticker"],
                            "combined_score": result["combined_score"],
                            "quality_score": result.get("quality_score"),
                            "garp_score":    result.get("garp_score"),
                        })
                except Exception as e:
                    logger.debug(f"Wealth skip {t['ticker']}: {e}")

            scores.sort(key=lambda x: x["combined_score"], reverse=True)
            top    = scores[:top_n]
            n      = len(top)
            weight = round(1.0 / n, 6) if n > 0 else 0.0

            shortlist = [
                {
                    "ticker_id":   r["ticker_id"],
                    "ticker":      r["ticker"],
                    "rank":        i + 1,
                    "score":       r["combined_score"],
                    "weight":      weight,
                    "scores_json": {
                        "quality_score": r.get("quality_score"),
                        "garp_score":    r.get("garp_score"),
                        "weight":        weight,
                    },
                }
                for i, r in enumerate(top)
            ]

            logger.info(f"Wealth: {n} ticker in shortlist (top {top_n})")
            return {
                "profile":   _PROFILE_WEALTH,
                "run_date":  date.today(),
                "shortlist": shortlist,
                "n_signals": n,
                "status":    "success",
                "error":     None,
            }

        except Exception as e:
            logger.error(f"Wealth screener fallito: {e}", exc_info=True)
            return {
                "profile":   _PROFILE_WEALTH,
                "run_date":  date.today(),
                "shortlist": [],
                "n_signals": 0,
                "status":    "error",
                "error":     str(e),
            }

    # ── Save to DB ────────────────────────────────────────────────────

    def save_to_db(self, results: dict[str, Any]) -> int:
        """
        Scrive shortlist in screener_results.
        Upsert su (run_date, screener_profile, ticker_id).
        Ritorna numero di righe processate.
        """
        shortlist = results.get("shortlist") or []
        if not shortlist:
            logger.info(f"save_to_db: shortlist vuota per {results.get('profile')} — skip")
            return 0

        now      = datetime.now(timezone.utc)
        run_date = results.get("run_date") or date.today()
        profile  = results["profile"]

        rows = [
            {
                "run_date":         run_date,
                "screener_profile": profile,
                "ticker_id":        item["ticker_id"],
                "rank":             item["rank"],
                "score":            item["score"],
                "scores_json":      json.dumps(item.get("scores_json") or {}),
                "generated_at":     now,
            }
            for item in shortlist
        ]

        df = pd.DataFrame(rows)
        n  = upsert_dataframe(
            self.engine, df, "screener_results",
            conflict_cols=["run_date", "screener_profile", "ticker_id"],
        )
        logger.info(f"save_to_db: {n} righe in screener_results ({profile})")
        return n

    # ── Run All ───────────────────────────────────────────────────────

    def run_all(self, top_n: int = 30) -> dict[str, Any]:
        """
        Esegue entrambi gli screener e salva i risultati in DB.
        I due screener sono indipendenti: se uno fallisce l'altro continua.

        Ritorna:
            {
                "n_speculative":    int,
                "n_wealth":         int,
                "rows_saved":       int,
                "execution_time_s": float,
                "status":           "ok" | "partial" | "error",
                "errors":           list[str],
            }
        """
        start  = datetime.now(timezone.utc)
        errors: list[str] = []

        spec   = self.run_speculative()
        wealth = self.run_wealth(top_n=top_n)

        if spec["status"] == "error":
            errors.append(f"speculative: {spec['error']}")
        if wealth["status"] == "error":
            errors.append(f"wealth: {wealth['error']}")

        rows_spec   = self.save_to_db(spec)
        rows_wealth = self.save_to_db(wealth)

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        if len(errors) == 2:
            status = "error"
        elif errors:
            status = "partial"
        else:
            status = "ok"

        return {
            "n_speculative":    spec["n_signals"],
            "n_wealth":         wealth["n_signals"],
            "rows_saved":       rows_spec + rows_wealth,
            "execution_time_s": round(elapsed, 2),
            "status":           status,
            "errors":           errors,
        }


# ── Smoke test / entry point ──────────────────────────────────────────

def main(profile: str = "all", top_n: int = 30) -> None:
    engine      = get_engine()
    orchestrator = ScreenerOrchestrator(engine)

    if profile == "speculative":
        result = orchestrator.run_speculative()
        saved  = orchestrator.save_to_db(result)
        print(f"Speculative: {result['n_signals']} ETF LONG | {saved} righe salvate")
        for r in result["shortlist"]:
            print(f"  [{r['rank']:2d}] {r['ticker']:8s} score={r['score']:.1f}")

    elif profile == "wealth":
        result = orchestrator.run_wealth(top_n=top_n)
        saved  = orchestrator.save_to_db(result)
        print(f"Wealth: {result['n_signals']} ticker | {saved} righe salvate")
        for r in result["shortlist"][:10]:
            print(f"  [{r['rank']:2d}] {r['ticker']:8s} score={r['score']:.1f}")

    else:
        summary = orchestrator.run_all(top_n=top_n)
        print(f"run_all() completato in {summary['execution_time_s']}s")
        print(f"  Speculative : {summary['n_speculative']} ETF LONG")
        print(f"  Wealth      : {summary['n_wealth']} ticker")
        print(f"  Righe in DB : {summary['rows_saved']}")
        print(f"  Status      : {summary['status']}")
        if summary["errors"]:
            for e in summary["errors"]:
                print(f"  ERRORE: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Personal Bloomberg Screener")
    parser.add_argument("--profile", choices=["all", "speculative", "wealth"], default="all")
    parser.add_argument("--top-n",   type=int, default=30)
    args = parser.parse_args()
    main(profile=args.profile, top_n=args.top_n)
