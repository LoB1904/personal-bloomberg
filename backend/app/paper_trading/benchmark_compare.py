"""
Fase 2 — Benchmark Comparator per paper trading.

Per ogni segnale in paper_signals con P&L compilato, confronta
il rendimento del segnale vs benchmark sullo stesso holding period.

Alpha = pnl_pct_segnale - rendimento_benchmark

Benchmark disponibili in benchmark_prices:
  VWCE  — World ETF (proxy buy & hold passivo)
  SPY   — S&P 500 USA
  IWQU  — Quality factor ETF

Uso:
    python -m app.paper_trading.benchmark_compare
    python -m app.paper_trading.benchmark_compare --profile speculative_trend_etf
    python -m app.paper_trading.benchmark_compare --benchmark VWCE SPY IWQU
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from app.core.db import get_engine

logger = logging.getLogger(__name__)

# Finestre temporali (stesse di pnl_calculator)
_WINDOWS: list[tuple[int, str]] = [
    (7,   "1w"),
    (30,  "1m"),
    (91,  "3m"),
    (182, "6m"),
    (365, "12m"),
]

_DEFAULT_BENCHMARKS = ["VWCE", "SPY", "IWQU"]

# Tolleranza ricerca prezzo benchmark (giorni)
_BENCH_TOLERANCE_DAYS = 10


class BenchmarkCompare:
    """Confronta P&L dei segnali paper trading vs benchmark su stesso holding period."""

    def __init__(self, engine=None):
        self.engine = engine or get_engine()

    # ── Fetch helpers ─────────────────────────────────────────────────

    def _get_benchmark_return(
        self,
        benchmark_code: str,
        start_date: date,
        days: int,
    ) -> float | None:
        """
        Rendimento percentuale del benchmark per lo stesso holding period.

        Strategia identica a pnl_calculator._get_exit_price:
          - cerca primo giorno >= target_date entro tolleranza
          - fallback: cerca il giorno precedente più vicino
        Ritorna None se i dati non coprono il periodo.
        """
        target_date = start_date + timedelta(days=days)
        if target_date > date.today():
            return None

        sql_start = text("""
            SELECT close
            FROM benchmark_prices
            WHERE benchmark_code = :code
              AND date <= :d
            ORDER BY date DESC
            LIMIT 1
        """)
        sql_fwd = text("""
            SELECT close
            FROM benchmark_prices
            WHERE benchmark_code = :code
              AND date >= :target
              AND date <= :max_d
            ORDER BY date ASC
            LIMIT 1
        """)
        sql_bwd = text("""
            SELECT close
            FROM benchmark_prices
            WHERE benchmark_code = :code
              AND date < :target
              AND date >= :min_d
            ORDER BY date DESC
            LIMIT 1
        """)

        with self.engine.connect() as conn:
            # Prezzo all'entry
            row_start = conn.execute(
                sql_start, {"code": benchmark_code, "d": start_date}
            ).fetchone()
            if row_start is None or row_start[0] is None:
                return None

            start_price = float(row_start[0])

            # Prezzo all'exit — cerca in avanti
            max_d  = target_date + timedelta(days=_BENCH_TOLERANCE_DAYS)
            row_end = conn.execute(
                sql_fwd, {"code": benchmark_code, "target": target_date, "max_d": max_d}
            ).fetchone()

            if row_end is None:
                # Fallback: cerca all'indietro
                min_d = target_date - timedelta(days=_BENCH_TOLERANCE_DAYS)
                row_end = conn.execute(
                    sql_bwd,
                    {"code": benchmark_code, "target": target_date, "min_d": min_d},
                ).fetchone()

        if row_end is None or row_end[0] is None:
            return None

        end_price = float(row_end[0])
        return (end_price / start_price - 1) * 100

    def _get_signals_with_pnl(self, profile: str | None = None) -> list[dict[str, Any]]:
        """Carica segnali con almeno una finestra P&L compilata."""
        base = """
            SELECT ps.id, ps.ticker_id, t.ticker,
                   ps.screener_profile, ps.entry_date,
                   ps.pnl_pct_1w, ps.pnl_pct_1m, ps.pnl_pct_3m,
                   ps.pnl_pct_6m, ps.pnl_pct_12m,
                   ps.status
            FROM paper_signals ps
            JOIN ticker_universe t ON t.id = ps.ticker_id
            WHERE (
                ps.pnl_pct_1w  IS NOT NULL OR
                ps.pnl_pct_1m  IS NOT NULL OR
                ps.pnl_pct_3m  IS NOT NULL OR
                ps.pnl_pct_6m  IS NOT NULL OR
                ps.pnl_pct_12m IS NOT NULL
            )
        """
        if profile:
            sql    = text(base + " AND ps.screener_profile = :p ORDER BY ps.entry_date, ps.id")
            params: dict = {"p": profile}
        else:
            sql    = text(base + " ORDER BY ps.screener_profile, ps.entry_date, ps.id")
            params = {}

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r._mapping) for r in rows]

    def _get_available_benchmarks(self) -> list[str]:
        """Ritorna i benchmark_code disponibili in benchmark_prices."""
        sql = text("SELECT DISTINCT benchmark_code FROM benchmark_prices ORDER BY benchmark_code")
        with self.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [r[0] for r in rows]

    # ── Signal-level comparison ───────────────────────────────────────

    def compare_signals(
        self,
        profile: str | None = None,
        benchmark_code: str = "VWCE",
    ) -> list[dict[str, Any]]:
        """
        Per ogni segnale con P&L compilato, calcola il rendimento del benchmark
        sullo stesso holding period e l'alpha (segnale - benchmark).

        Args:
            profile: filtra per profilo (None = tutti)
            benchmark_code: codice benchmark da confrontare

        Returns:
            lista di dict per segnale/finestra:
            [{
                "id":               int,
                "ticker":           str,
                "screener_profile": str,
                "entry_date":       date,
                "window":           "1w"|"1m"|"3m"|"6m"|"12m",
                "signal_pnl":       float,
                "bench_return":     float | None,
                "alpha":            float | None,
            }]
        """
        signals = self._get_signals_with_pnl(profile)
        results: list[dict[str, Any]] = []

        for signal in signals:
            entry_date = signal["entry_date"]

            for days, suffix in _WINDOWS:
                pnl_col = f"pnl_pct_{suffix}"
                signal_pnl = signal.get(pnl_col)
                if signal_pnl is None:
                    continue

                signal_pnl = float(signal_pnl)
                bench_ret  = self._get_benchmark_return(benchmark_code, entry_date, days)
                alpha      = (signal_pnl - bench_ret) if bench_ret is not None else None

                results.append({
                    "id":               signal["id"],
                    "ticker":           signal["ticker"],
                    "screener_profile": signal["screener_profile"],
                    "entry_date":       entry_date,
                    "window":           suffix,
                    "signal_pnl":       round(signal_pnl, 4),
                    "bench_return":     round(bench_ret, 4) if bench_ret is not None else None,
                    "alpha":            round(alpha, 4) if alpha is not None else None,
                })

        return results

    # ── Aggregate alpha summary ───────────────────────────────────────

    def alpha_summary(
        self,
        profile: str | None = None,
        benchmarks: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Statistiche aggregate alpha per profilo, finestra, benchmark.

        Args:
            profile:    filtra per profilo (None = tutti)
            benchmarks: lista benchmark da confrontare (default: VWCE, SPY, IWQU)

        Returns:
            {
                "<profile>": {
                    "n_signals":       int,
                    "n_with_data":     int,
                    "benchmarks": {
                        "VWCE": {
                            "1w":  {
                                "alpha_avg":    float | None,
                                "signal_avg":   float | None,
                                "bench_avg":    float | None,
                                "hit_rate_vs_bench": float | None,  # % segnali > benchmark
                                "n":            int,
                            },
                            ...
                        },
                        ...
                    }
                }
            }
        """
        if benchmarks is None:
            available = set(self._get_available_benchmarks())
            benchmarks = [b for b in _DEFAULT_BENCHMARKS if b in available]

        signals = self._get_signals_with_pnl(profile)

        # Raggruppa per profilo
        by_profile: dict[str, list[dict]] = {}
        for s in signals:
            by_profile.setdefault(s["screener_profile"], []).append(s)

        # Conta totale segnali per profilo (includendo quelli senza dati)
        all_signals_count = self._count_all_signals(profile)

        summary: dict[str, Any] = {}
        for prof, prof_signals in by_profile.items():
            bench_results: dict[str, dict] = {}

            for bcode in benchmarks:
                windows_data: dict[str, dict] = {}

                for days, suffix in _WINDOWS:
                    pnl_col = f"pnl_pct_{suffix}"
                    rows_with_data = []

                    for s in prof_signals:
                        spnl = s.get(pnl_col)
                        if spnl is None:
                            continue
                        bret = self._get_benchmark_return(bcode, s["entry_date"], days)
                        if bret is None:
                            continue
                        rows_with_data.append((float(spnl), bret))

                    if not rows_with_data:
                        windows_data[suffix] = {
                            "alpha_avg":          None,
                            "signal_avg":         None,
                            "bench_avg":          None,
                            "hit_rate_vs_bench":  None,
                            "n":                  0,
                        }
                        continue

                    signal_vals = [r[0] for r in rows_with_data]
                    bench_vals  = [r[1] for r in rows_with_data]
                    alphas      = [s - b for s, b in rows_with_data]
                    n           = len(rows_with_data)

                    windows_data[suffix] = {
                        "alpha_avg":         round(sum(alphas) / n, 4),
                        "signal_avg":        round(sum(signal_vals) / n, 4),
                        "bench_avg":         round(sum(bench_vals) / n, 4),
                        "hit_rate_vs_bench": round(
                            sum(1 for a in alphas if a > 0) / n * 100, 1
                        ),
                        "n": n,
                    }

                bench_results[bcode] = windows_data

            summary[prof] = {
                "n_signals":   all_signals_count.get(prof, len(prof_signals)),
                "n_with_data": len(prof_signals),
                "benchmarks":  bench_results,
            }

        return summary

    def _count_all_signals(self, profile: str | None = None) -> dict[str, int]:
        """Conta tutti i segnali (anche senza P&L) per profilo."""
        if profile:
            sql = text("""
                SELECT screener_profile, COUNT(*) as n
                FROM paper_signals
                WHERE screener_profile = :p
                GROUP BY screener_profile
            """)
            params: dict = {"p": profile}
        else:
            sql = text("""
                SELECT screener_profile, COUNT(*) as n
                FROM paper_signals
                GROUP BY screener_profile
            """)
            params = {}

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {r[0]: r[1] for r in rows}

    # ── Vs baseline strategies ────────────────────────────────────────

    def vs_baseline_summary(
        self,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """
        Confronta P&L medio dei segnali vs rendimento delle strategie baseline
        in paper_strategy_daily per lo stesso periodo.

        Baseline disponibili:
            baseline_bh_vwce, baseline_dual_momentum, baseline_ew_sp100

        Ritorna per ogni finestra: rendimento baseline nello stesso periodo.
        """
        signals = self._get_signals_with_pnl(profile)
        if not signals:
            return {}

        # Carica tutti i total_return_pct delle baseline
        sql = text("""
            SELECT strategy_code, date, total_return_pct
            FROM paper_strategy_daily
            WHERE total_return_pct IS NOT NULL
            ORDER BY strategy_code, date
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()

        # Indicizza: {strategy_code: {date: total_return_pct}}
        strat_index: dict[str, dict[date, float]] = {}
        for r in rows:
            strat_index.setdefault(r[0], {})[r[1]] = float(r[2])

        # Calcola rendimento baseline per holding period:
        # (total_return_pct at end) - (total_return_pct at start)
        # perché total_return_pct è cumulativo dall'inizio della simulazione

        def _baseline_return(code: str, start: date, days: int) -> float | None:
            if code not in strat_index:
                return None
            idx = strat_index[code]
            # Trova il valore più vicino alla start_date
            start_val = _nearest_value(idx, start, direction="back")
            end_date  = start + timedelta(days=days)
            end_val   = _nearest_value(idx, end_date, direction="forward")
            if start_val is None or end_val is None:
                return None
            # total_return_pct è già in %, quindi il delta è il rendimento del periodo
            return end_val - start_val

        def _nearest_value(
            idx: dict[date, float], target: date, direction: str
        ) -> float | None:
            tol = timedelta(days=_BENCH_TOLERANCE_DAYS)
            if direction == "back":
                candidates = {d: v for d, v in idx.items()
                              if target - tol <= d <= target}
                if not candidates:
                    return None
                return candidates[max(candidates)]
            else:
                candidates = {d: v for d, v in idx.items()
                              if target <= d <= target + tol}
                if not candidates:
                    return None
                return candidates[min(candidates)]

        # Aggrega per profilo
        by_profile: dict[str, list[dict]] = {}
        for s in signals:
            by_profile.setdefault(s["screener_profile"], []).append(s)

        result: dict[str, Any] = {}
        for prof, prof_signals in by_profile.items():
            baselines: dict[str, dict] = {}

            for strategy_code in strat_index:
                windows_data: dict[str, dict] = {}

                for days, suffix in _WINDOWS:
                    pnl_col = f"pnl_pct_{suffix}"
                    pairs = []

                    for s in prof_signals:
                        spnl = s.get(pnl_col)
                        if spnl is None:
                            continue
                        bret = _baseline_return(strategy_code, s["entry_date"], days)
                        if bret is None:
                            continue
                        pairs.append((float(spnl), bret))

                    if not pairs:
                        windows_data[suffix] = {"alpha_avg": None, "n": 0}
                    else:
                        n      = len(pairs)
                        alphas = [s - b for s, b in pairs]
                        windows_data[suffix] = {
                            "alpha_avg":  round(sum(alphas) / n, 4),
                            "signal_avg": round(sum(s for s, _ in pairs) / n, 4),
                            "bench_avg":  round(sum(b for _, b in pairs) / n, 4),
                            "n":          n,
                        }

                baselines[strategy_code] = windows_data

            result[prof] = baselines

        return result


# ── Smoke test / entry point ──────────────────────────────────────────

def main(profile: str | None = None, benchmarks: list[str] | None = None) -> None:
    engine  = get_engine()
    compare = BenchmarkCompare(engine)

    available_benchmarks = compare._get_available_benchmarks()
    print(f"Benchmark disponibili: {', '.join(available_benchmarks)}")

    if benchmarks is None:
        benchmarks = [b for b in _DEFAULT_BENCHMARKS if b in available_benchmarks]

    print(f"\nCalcolo alpha summary (benchmark: {', '.join(benchmarks)})...")
    summary = compare.alpha_summary(profile=profile, benchmarks=benchmarks)

    if not summary:
        print("\nNessun segnale con P&L compilato.")
        print("Attendere che le finestre temporali maturino.")
        print("  1w  = 7 giorni dall'entry_date")
        print("  1m  = 30 giorni")
        print("  3m  = 91 giorni")
        return

    for prof, data in summary.items():
        print(f"\n{'='*60}")
        print(f"  Profilo : {prof}")
        print(f"  Segnali : {data['n_signals']} totali, {data['n_with_data']} con dati P&L")

        for bcode, windows in data["benchmarks"].items():
            print(f"\n  vs {bcode}:")
            for suffix in ("1w", "1m", "3m", "6m", "12m"):
                w = windows[suffix]
                if w["n"] == 0:
                    print(f"    {suffix:>3s} : -- (nessun dato)")
                else:
                    alpha = w["alpha_avg"]
                    sign  = "+" if alpha is not None and alpha >= 0 else ""
                    print(
                        f"    {suffix:>3s} : "
                        f"segnale={w['signal_avg']:+.2f}%  "
                        f"bench={w['bench_avg']:+.2f}%  "
                        f"alpha={sign}{alpha:.2f}%  "
                        f"hit={w['hit_rate_vs_bench']:.0f}%  "
                        f"n={w['n']}"
                    )

    print(f"\n{'='*60}")
    print("\nVs baseline strategies:")
    vs_base = compare.vs_baseline_summary(profile=profile)
    if not vs_base:
        print("  Nessun dato.")
        return

    for prof, baselines in vs_base.items():
        print(f"\n  {prof}:")
        for strat, windows in baselines.items():
            print(f"    vs {strat}:")
            for suffix in ("1w", "1m", "3m", "6m", "12m"):
                w = windows[suffix]
                if w["n"] == 0:
                    print(f"      {suffix:>3s} : --")
                else:
                    sign = "+" if w["alpha_avg"] >= 0 else ""
                    print(
                        f"      {suffix:>3s} : "
                        f"segnale={w['signal_avg']:+.2f}%  "
                        f"base={w['bench_avg']:+.2f}%  "
                        f"alpha={sign}{w['alpha_avg']:.2f}%  "
                        f"n={w['n']}"
                    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Benchmark comparator paper trading")
    parser.add_argument(
        "--profile", type=str, default=None,
        help="Filtra per screener_profile (default: tutti)"
    )
    parser.add_argument(
        "--benchmark", nargs="+", default=None,
        help="Codici benchmark (default: VWCE SPY IWQU)"
    )
    args = parser.parse_args()
    main(profile=args.profile, benchmarks=args.benchmark)
