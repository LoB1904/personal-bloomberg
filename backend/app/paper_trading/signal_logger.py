"""
Fase 2 — Signal Logger per paper trading.

Legge i risultati dello screener da screener_results, fetcha il prezzo
di entrata da prices_daily, e scrive nuovi segnali in paper_signals.

Deduplicazione manuale su (ticker_id, screener_profile, entry_date):
  nessun UNIQUE constraint su quella tripletta nel DB, quindi la gestione
  è in Python — se un segnale per quella combinazione esiste già, viene saltato.

Uso:
    python -m app.paper_trading.signal_logger
    python -m app.paper_trading.signal_logger --date 2026-06-04
"""
from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe

logger = logging.getLogger(__name__)


class SignalLogger:
    """Logga segnali dallo screener in paper_signals con entry price reale."""

    def __init__(self, engine=None):
        self.engine = engine or get_engine()

    # ── Fetch helpers ─────────────────────────────────────────────────

    def _fetch_entry_price(self, ticker_id: int, entry_date: date) -> float | None:
        """
        Prezzo di chiusura del giorno di entrata (o il più recente disponibile <= entry_date).
        Ritorna None se nessun prezzo disponibile.
        """
        sql = text("""
            SELECT close
            FROM prices_daily
            WHERE ticker_id = :tid
              AND date <= :d
            ORDER BY date DESC
            LIMIT 1
        """)
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"tid": ticker_id, "d": entry_date}).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def _fetch_screener_results(self, run_date: date) -> list[dict]:
        """Legge i risultati screener per un dato giorno da screener_results."""
        sql = text("""
            SELECT sr.ticker_id,
                   sr.screener_profile,
                   sr.rank,
                   sr.score,
                   sr.scores_json,
                   t.ticker
            FROM screener_results sr
            JOIN ticker_universe t ON t.id = sr.ticker_id
            WHERE sr.run_date = :d
            ORDER BY sr.screener_profile, sr.rank
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"d": run_date}).fetchall()
        return [dict(r._mapping) for r in rows]

    def _existing_signal_keys(self, entry_date: date) -> set[tuple[int, str]]:
        """
        Ritorna l'insieme di (ticker_id, screener_profile) già presenti
        in paper_signals per entry_date — usato per la deduplicazione.
        """
        sql = text("""
            SELECT ticker_id, screener_profile
            FROM paper_signals
            WHERE entry_date = :d
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"d": entry_date}).fetchall()
        return {(r.ticker_id, r.screener_profile) for r in rows}

    # ── Log signals ───────────────────────────────────────────────────

    def log_signals(self, run_date: date | None = None) -> int:
        """
        Legge screener_results per run_date, fetcha entry_price, scrive in paper_signals.
        Salta segnali già presenti per quella (ticker_id, screener_profile, entry_date).
        Salta segnali senza entry_price disponibile (warning, non crash).

        Args:
            run_date: data dei risultati screener da loggare (default: oggi)

        Returns: numero di nuovi segnali inseriti.
        """
        if run_date is None:
            run_date = date.today()

        screener_rows = self._fetch_screener_results(run_date)
        if not screener_rows:
            logger.info(f"log_signals [{run_date}]: nessun risultato screener — skip")
            return 0

        existing = self._existing_signal_keys(run_date)
        now      = datetime.now(timezone.utc)
        new_rows: list[dict[str, Any]] = []

        for row in screener_rows:
            key = (row["ticker_id"], row["screener_profile"])

            if key in existing:
                logger.debug(
                    f"Skip duplicato: {row['ticker']} "
                    f"{row['screener_profile']} {run_date}"
                )
                continue

            entry_price = self._fetch_entry_price(row["ticker_id"], run_date)
            if entry_price is None:
                logger.warning(
                    f"entry_price non disponibile per {row['ticker']} "
                    f"al {run_date} — segnale saltato"
                )
                continue

            # scores_json può arrivare come dict (JSONB nativo) o come stringa
            scores = row.get("scores_json") or {}
            if isinstance(scores, str):
                try:
                    scores = json.loads(scores)
                except Exception:
                    scores = {}

            new_rows.append({
                "signal_uid":       str(uuid.uuid4()),
                "ticker_id":        row["ticker_id"],
                "screener_profile": row["screener_profile"],
                "signal_type":      "screener",
                "direction":        "long",
                "generated_at":     now,
                "entry_date":       run_date,
                "entry_price":      entry_price,
                "status":           "open",
                "metadata":         json.dumps({
                    "rank":  row["rank"],
                    "score": float(row["score"]),
                    **scores,
                }),
            })

        if not new_rows:
            logger.info(f"log_signals [{run_date}]: nessun nuovo segnale")
            return 0

        df = pd.DataFrame(new_rows)
        # Conflict su signal_uid (UUID fresco = no conflitti reali,
        # ma garantisce idempotenza se log_signals viene chiamato due volte
        # con gli stessi UUID — non accade in pratica)
        n = upsert_dataframe(
            self.engine, df, "paper_signals",
            conflict_cols=["signal_uid"],
        )
        logger.info(
            f"log_signals [{run_date}]: {n} nuovi segnali in paper_signals"
        )
        return n

    # ── Read open signals ─────────────────────────────────────────────

    def get_open_signals(self, profile: str | None = None) -> list[dict[str, Any]]:
        """
        Ritorna tutti i segnali con status='open', opzionalmente filtrati per profilo.

        Args:
            profile: 'speculative_trend_etf' | 'wealth_quality_garp' | None (tutti)

        Returns: lista di dict con tutti i campi paper_signals + ticker symbol.
        """
        base_sql = """
            SELECT
                ps.id, ps.signal_uid, ps.ticker_id, t.ticker,
                ps.screener_profile, ps.signal_type, ps.direction,
                ps.generated_at, ps.entry_date, ps.entry_price,
                ps.status, ps.metadata
            FROM paper_signals ps
            JOIN ticker_universe t ON t.id = ps.ticker_id
            WHERE ps.status = 'open'
        """
        if profile:
            sql    = text(base_sql + " AND ps.screener_profile = :profile ORDER BY ps.entry_date DESC, ps.ticker_id")
            params = {"profile": profile}
        else:
            sql    = text(base_sql + " ORDER BY ps.entry_date DESC, ps.screener_profile, ps.ticker_id")
            params = {}

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [dict(r._mapping) for r in rows]


# ── Smoke test / entry point ──────────────────────────────────────────

def main(run_date: date | None = None) -> None:
    engine = get_engine()
    logger_obj = SignalLogger(engine)

    if run_date is None:
        run_date = date.today()

    # Verifica che screener_results abbia dati per la data
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        count = conn.execute(
            _text("SELECT COUNT(*) FROM screener_results WHERE run_date = :d"),
            {"d": run_date},
        ).scalar()

    if count == 0:
        print(f"Nessun risultato screener per {run_date}.")
        print("Esegui prima: python -m app.analytics.screener")
        return

    print(f"Trovati {count} risultati screener per {run_date} — loggo segnali...")
    n_new = logger_obj.log_signals(run_date)
    print(f"Nuovi segnali loggati: {n_new}")

    # Segnali open per profilo
    all_open = logger_obj.get_open_signals()
    by_profile: dict[str, list] = {}
    for s in all_open:
        by_profile.setdefault(s["screener_profile"], []).append(s)

    print(f"\nSegnali open totali: {len(all_open)}")
    for profile, signals in sorted(by_profile.items()):
        print(f"  {profile}: {len(signals)} segnali")
        for s in signals:
            print(
                f"    {s['ticker']:8s} entry={float(s['entry_price']):.2f}"
                f" @ {s['entry_date']}"
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Log screener signals to paper_signals")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=None,
        help="Data run screener (default: oggi, formato YYYY-MM-DD)",
    )
    args = parser.parse_args()
    main(run_date=args.date)
