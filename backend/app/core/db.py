"""
Connessione SQLAlchemy centralizzata + helper di upsert per Postgres.

Uso tipico:
    from app.core.db import get_engine, upsert_dataframe
    engine = get_engine()
    upsert_dataframe(engine, df, table="prices_daily", conflict_cols=["ticker_id", "date"])
"""
from typing import Sequence
import logging

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import settings

logger = logging.getLogger(__name__)


_engine: Engine | None = None


def get_engine() -> Engine:
    """Singleton engine, lazy-init. Pool size piccolo: i job sono batch, non long-running."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_size=2,
            max_overflow=2,
            pool_pre_ping=True,    # verifica connessione prima dell'uso (Supabase chiude socket inattivi)
            future=True,
        )
    return _engine


def test_connection() -> bool:
    """Test rapido: la connessione regge?"""
    try:
        with get_engine().connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            return result == 1
    except Exception as e:
        logger.error(f"Connessione DB fallita: {e}")
        return False


def upsert_dataframe(
    engine: Engine,
    df: pd.DataFrame,
    table: str,
    conflict_cols: Sequence[str],
    update_cols: Sequence[str] | None = None,
) -> int:
    """
    Upsert (INSERT ... ON CONFLICT DO UPDATE) di un DataFrame in una tabella Postgres.

    Args:
        engine: SQLAlchemy engine
        df: dati da inserire
        table: nome tabella destinazione
        conflict_cols: colonne UNIQUE che identificano una riga (es. ['ticker_id', 'date'])
        update_cols: colonne da aggiornare in caso di conflitto. Se None, aggiorna tutto tranne conflict_cols.

    Returns: numero di righe processate.

    Nota: per dataset grandi (>10k righe) considerare COPY + temp table. Per ora ON CONFLICT basta.
    """
    if df.empty:
        return 0

    all_cols = list(df.columns)
    if update_cols is None:
        update_cols = [c for c in all_cols if c not in conflict_cols]

    cols_sql = ", ".join(all_cols)
    placeholders = ", ".join(f":{c}" for c in all_cols)
    conflict_sql = ", ".join(conflict_cols)
    update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = text(f"""
        INSERT INTO {table} ({cols_sql})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_sql})
        DO UPDATE SET {update_sql}
    """)

    records = df.to_dict(orient="records")
    with engine.begin() as conn:
        conn.execute(sql, records)

    logger.info(f"Upsert {len(records)} righe in {table}")
    return len(records)


if __name__ == "__main__":
    # Smoke test: python -m app.core.db
    logging.basicConfig(level=logging.INFO)
    print(f"Database URL: {settings.database_url[:50]}...")
    if test_connection():
        print("OK - connessione a Postgres funzionante")
    else:
        print("KO - connessione fallita, controlla DATABASE_URL in .env")
