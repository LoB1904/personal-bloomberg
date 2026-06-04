"""
Fase 2 — P&L Calculator per paper trading.

Per ogni segnale open in paper_signals verifica se le finestre temporali
(1w/1m/3m/6m/12m) sono trascorse e, se sì, aggiorna exit price e pnl_pct.

Finestre:
  1w  =  7 giorni calendari dall'entry_date
  1m  = 30 giorni
  3m  = 91 giorni
  6m  = 182 giorni
  12m = 365 giorni

Exit price: primo giorno di trading disponibile >= target_date,
            entro una tolleranza di 10 giorni calendari.
            Se non disponibile (finestra non ancora chiusa), la finestra
            viene saltata e verrà aggiornata alla prossima esecuzione.

Status: 'open' fino a quando tutte e 5 le finestre sono compilate,
        poi 'closed_12m'.

Uso:
    python -m app.paper_trading.pnl_calculator
    python -m app.paper_trading.pnl_calculator --profile speculative_trend_etf
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from app.core.db import get_engine

logger = logging.getLogger(__name__)

# (giorni_offset, suffisso_colonna)
_WINDOWS: list[tuple[int, str]] = [
    (7,   "1w"),
    (30,  "1m"),
    (91,  "3m"),
    (182, "6m"),
    (365, "12m"),
]

# Tolleranza massima in giorni per trovare il prezzo di uscita
_EXIT_PRICE_TOLERANCE_DAYS = 10

# Colonne price/pnl per window — derivate da _WINDOWS, hardcoded per sicurezza
_PRICE_COLS = {"price_1w", "price_1m", "price_3m", "price_6m", "price_12m"}
_PNL_COLS   = {"pnl_pct_1w", "pnl_pct_1m", "pnl_pct_3m", "pnl_pct_6m", "pnl_pct_12m"}
_ALLOWED_UPDATE_COLS = _PRICE_COLS | _PNL_COLS | {"status"}


class PnLCalculator:
    """Aggiorna exit prices e P&L per i segnali open in paper trading."""

    def __init__(self, engine=None):
        self.engine = engine or get_engine()

    # ── Fetch helpers ─────────────────────────────────────────────────

    def _get_open_signals(self, profile: str | None = None) -> list[dict[str, Any]]:
        """Carica tutti i segnali open (o filtrati per profilo)."""
        base = """
            SELECT ps.id, ps.ticker_id, t.ticker,
                   ps.screener_profile, ps.entry_date, ps.entry_price,
                   ps.price_1w,  ps.price_1m,  ps.price_3m,
                   ps.price_6m,  ps.price_12m,
                   ps.pnl_pct_1w, ps.pnl_pct_1m, ps.pnl_pct_3m,
                   ps.pnl_pct_6m, ps.pnl_pct_12m,
                   ps.status
            FROM paper_signals ps
            JOIN ticker_universe t ON t.id = ps.ticker_id
            WHERE ps.status = 'open'
        """
        if profile:
            sql    = text(base + " AND ps.screener_profile = :p ORDER BY ps.entry_date, ps.id")
            params: dict = {"p": profile}
        else:
            sql    = text(base + " ORDER BY ps.entry_date, ps.id")
            params = {}

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r._mapping) for r in rows]

    def _get_exit_price(
        self,
        ticker_id: int,
        entry_date: date,
        days: int,
    ) -> float | None:
        """
        Cerca il close del giorno di trading più vicino a entry_date + days.
        Strategia: primo giorno disponibile >= target_date,
                   entro _EXIT_PRICE_TOLERANCE_DAYS giorni.
        Ritorna None se la finestra non è ancora chiusa o il prezzo manca.
        """
        target_date = entry_date + timedelta(days=days)
        max_date    = target_date + timedelta(days=_EXIT_PRICE_TOLERANCE_DAYS)

        # Non cercare prezzi futuri
        if target_date > date.today():
            return None

        # Prima cerca in avanti (>= target, entro tolleranza)
        sql_fwd = text("""
            SELECT close
            FROM prices_daily
            WHERE ticker_id = :tid
              AND date >= :target
              AND date <= :max_d
            ORDER BY date ASC
            LIMIT 1
        """)
        # Fallback: cerca all'indietro se nessun prezzo in avanti
        # (es. target su weekend con dati non ancora aggiornati)
        sql_bwd = text("""
            SELECT close
            FROM prices_daily
            WHERE ticker_id = :tid
              AND date < :target
              AND date >= :min_d
            ORDER BY date DESC
            LIMIT 1
        """)
        with self.engine.connect() as conn:
            row = conn.execute(
                sql_fwd, {"tid": ticker_id, "target": target_date, "max_d": max_date}
            ).fetchone()
            if row is None:
                min_date = target_date - timedelta(days=_EXIT_PRICE_TOLERANCE_DAYS)
                row = conn.execute(
                    sql_bwd,
                    {"tid": ticker_id, "target": target_date, "min_d": min_date},
                ).fetchone()

        return float(row[0]) if row and row[0] is not None else None

    def _update_signal(self, signal_id: int, changes: dict[str, Any]) -> None:
        """
        Aggiorna le colonne specificate per un segnale.
        Solo colonne in _ALLOWED_UPDATE_COLS sono accettate (whitelist anti-injection).
        """
        safe_changes = {k: v for k, v in changes.items() if k in _ALLOWED_UPDATE_COLS}
        if not safe_changes:
            return

        set_clause = ", ".join(f"{col} = :{col}" for col in safe_changes)
        sql = text(f"UPDATE paper_signals SET {set_clause} WHERE id = :signal_id")

        with self.engine.begin() as conn:
            conn.execute(sql, {**safe_changes, "signal_id": signal_id})

    # ── Update P&L ────────────────────────────────────────────────────

    def update_pnl(self, profile: str | None = None) -> dict[str, int]:
        """
        Per ogni segnale open verifica le finestre trascorse e aggiorna
        exit price + pnl_pct. Imposta status='closed_12m' quando complete.

        Args:
            profile: filtra per profilo (None = tutti)

        Returns:
            {signals_checked, signals_updated, newly_closed}
        """
        open_signals = self._get_open_signals(profile)
        logger.info(f"update_pnl: {len(open_signals)} segnali open da verificare")

        signals_updated = 0
        newly_closed    = 0

        for signal in open_signals:
            entry_date  = signal["entry_date"]
            entry_price = float(signal["entry_price"])
            changes: dict[str, Any] = {}

            for days, suffix in _WINDOWS:
                price_col = f"price_{suffix}"
                pnl_col   = f"pnl_pct_{suffix}"

                # Già compilata — skip
                if signal.get(price_col) is not None:
                    continue

                exit_price = self._get_exit_price(signal["ticker_id"], entry_date, days)
                if exit_price is None:
                    continue   # finestra non ancora chiusa o prezzo mancante

                pnl_pct = (exit_price / entry_price - 1) * 100
                changes[price_col] = exit_price
                changes[pnl_col]   = round(pnl_pct, 4)

                logger.debug(
                    f"{signal['ticker']} {suffix}: "
                    f"exit={exit_price:.2f} pnl={pnl_pct:+.2f}%"
                )

            if not changes:
                continue

            # Controlla se tutte le finestre sono ora complete
            all_complete = all(
                signal.get(f"price_{suffix}") is not None
                or f"price_{suffix}" in changes
                for _, suffix in _WINDOWS
            )
            if all_complete:
                changes["status"] = "closed_12m"
                newly_closed += 1
                logger.info(f"{signal['ticker']} id={signal['id']}: closed_12m")

            self._update_signal(signal["id"], changes)
            signals_updated += 1

        logger.info(
            f"update_pnl: {signals_updated}/{len(open_signals)} aggiornati, "
            f"{newly_closed} chiusi"
        )
        return {
            "signals_checked": len(open_signals),
            "signals_updated": signals_updated,
            "newly_closed":    newly_closed,
        }

    # ── Performance Summary ───────────────────────────────────────────

    def get_performance_summary(
        self, profile: str | None = None
    ) -> dict[str, Any]:
        """
        Statistiche aggregate per profilo (o tutti i profili).

        Per ogni finestra temporale con almeno un dato:
          - avg_pnl_pct: P&L medio
          - hit_rate:    % segnali con P&L > 0
          - n_data:      numero segnali con quella finestra compilata

        Ritorna dict:
          {
            "<profile>": {
                "n_signals": int,
                "n_closed":  int,
                "windows": {
                    "1w":  {"avg_pnl_pct": float, "hit_rate": float, "n_data": int},
                    "1m":  {...},
                    ...
                }
            },
            ...
          }
        """
        if profile:
            sql    = text("""
                SELECT ps.screener_profile,
                       ps.pnl_pct_1w,  ps.pnl_pct_1m,  ps.pnl_pct_3m,
                       ps.pnl_pct_6m,  ps.pnl_pct_12m,
                       ps.status
                FROM paper_signals ps
                WHERE ps.screener_profile = :p
                ORDER BY ps.entry_date
            """)
            params: dict = {"p": profile}
        else:
            sql    = text("""
                SELECT ps.screener_profile,
                       ps.pnl_pct_1w,  ps.pnl_pct_1m,  ps.pnl_pct_3m,
                       ps.pnl_pct_6m,  ps.pnl_pct_12m,
                       ps.status
                FROM paper_signals ps
                ORDER BY ps.screener_profile, ps.entry_date
            """)
            params = {}

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        # Raggruppa per profilo
        by_profile: dict[str, list[dict]] = {}
        for r in rows:
            p = r.screener_profile
            by_profile.setdefault(p, []).append(dict(r._mapping))

        summary: dict[str, Any] = {}
        for prof, signals in by_profile.items():
            n_total  = len(signals)
            n_closed = sum(1 for s in signals if s["status"] == "closed_12m")

            windows: dict[str, dict] = {}
            for _, suffix in _WINDOWS:
                col    = f"pnl_pct_{suffix}"
                values = [
                    float(s[col]) for s in signals if s.get(col) is not None
                ]
                if not values:
                    windows[suffix] = {"avg_pnl_pct": None, "hit_rate": None, "n_data": 0}
                    continue

                avg      = sum(values) / len(values)
                hit_rate = sum(1 for v in values if v > 0) / len(values) * 100

                windows[suffix] = {
                    "avg_pnl_pct": round(avg, 4),
                    "hit_rate":    round(hit_rate, 1),
                    "n_data":      len(values),
                }

            summary[prof] = {
                "n_signals": n_total,
                "n_closed":  n_closed,
                "windows":   windows,
            }

        return summary


# ── Smoke test / entry point ──────────────────────────────────────────

def main(profile: str | None = None) -> None:
    engine = get_engine()
    calc   = PnLCalculator(engine)

    print("Aggiornamento P&L segnali open...")
    result = calc.update_pnl(profile=profile)
    print(f"  Segnali verificati : {result['signals_checked']}")
    print(f"  Segnali aggiornati : {result['signals_updated']}")
    print(f"  Nuovi closed_12m   : {result['newly_closed']}")

    if result["signals_checked"] == 0:
        print("\nNessun segnale open. Esegui prima:")
        print("  python -m app.analytics.screener")
        print("  python -m app.paper_trading.signal_logger")
        return

    print("\nPerformance summary:")
    summary = calc.get_performance_summary(profile=profile)

    if not summary:
        print("  Nessun dato disponibile.")
        return

    for prof, data in summary.items():
        print(f"\n  {prof}")
        print(f"    Totale segnali : {data['n_signals']}")
        print(f"    Chiusi (12m)   : {data['n_closed']}")
        for suffix in ("1w", "1m", "3m", "6m", "12m"):
            w = data["windows"][suffix]
            if w["n_data"] == 0:
                print(f"    {suffix:>3s} : -- (nessun dato ancora)")
            else:
                print(
                    f"    {suffix:>3s} : avg={w['avg_pnl_pct']:+.2f}%  "
                    f"hit={w['hit_rate']:.0f}%  n={w['n_data']}"
                )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Aggiorna P&L segnali paper trading")
    parser.add_argument(
        "--profile", type=str, default=None,
        help="Filtra per screener_profile (default: tutti)"
    )
    args = parser.parse_args()
    main(profile=args.profile)
