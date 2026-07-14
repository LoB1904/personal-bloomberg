"""
Carica i CSV di backend/universe/ nella tabella ticker_universe.

Idempotente: se gira due volte, fa UPDATE non duplica (ON CONFLICT (ticker)).

Uso:
    python -m app.ingestion.load_universe
"""
from pathlib import Path
import logging
import sys

import pandas as pd

from app.core.config import BACKEND_DIR
from app.core.db import get_engine, upsert_dataframe

logger = logging.getLogger(__name__)

UNIVERSE_DIR = BACKEND_DIR / "universe"

# Mappa: file CSV -> universe_group, asset_class di default
UNIVERSE_FILES = {
    "baselines.csv":   {"universe_group": "baseline",   "asset_class_default": "etf"},
    "sector_etfs.csv": {"universe_group": "sector_etf", "asset_class_default": "etf"},
    "sp100.csv":       {"universe_group": "sp100",      "asset_class_default": "equity"},
    "sp500.csv":       {"universe_group": "sp500",      "asset_class_default": "equity"},
    "ftsemib.csv":     {"universe_group": "ftsemib",    "asset_class_default": "equity"},
    "wildcards.csv":   {"universe_group": "wildcard",   "asset_class_default": "equity"},
}


def _normalize_row(row: dict, universe_group: str, asset_class_default: str) -> dict:
    """Normalizza una riga CSV nel formato della tabella ticker_universe."""
    ticker = str(row.get("ticker", "")).strip()
    if not ticker:
        return {}

    # asset_class esplicito nel CSV (wildcards, baselines) oppure default per gruppo
    asset_class = str(row.get("asset_class", asset_class_default)).strip() or asset_class_default

    # Currency derivata dall'exchange/ticker suffix
    exchange = str(row.get("exchange", "") or row.get("region", "") or "").strip()
    currency_map = {
        "NASDAQ": "USD", "NYSE": "USD",
        "MIL": "EUR", "Xetra": "EUR", "DE": "EUR", "AS": "EUR", "PA": "EUR",
        "L": "GBP", "LSE": "GBP",
        "SW": "CHF",
        "HK": "HKD", "KS": "KRW",
    }
    currency = currency_map.get(exchange.upper(), None)
    # Fallback su suffix ticker
    if not currency:
        if ticker.endswith(".MI"): currency = "EUR"
        elif ticker.endswith(".DE") or ticker.endswith(".AS") or ticker.endswith(".PA"): currency = "EUR"
        elif ticker.endswith(".L"): currency = "GBP"
        elif ticker.endswith(".SW"): currency = "CHF"
        elif "-USD" in ticker: currency = "USD"      # crypto pairs
        elif "=X" in ticker: currency = None         # FX pair
        elif "=F" in ticker: currency = "USD"        # futures USD-quoted
        else: currency = "USD"

    return {
        "ticker": ticker,
        "name": str(row.get("name", "") or "").strip()[:255] or ticker,
        "exchange": exchange[:32] or None,
        "country": None,
        "currency": currency,
        "sector": (str(row.get("sector", "") or "").strip() or None),
        "industry": None,
        "asset_class": asset_class,
        "universe_group": universe_group,
        "is_active": True,
    }


def load_universe_file(filename: str, config: dict) -> pd.DataFrame:
    """Legge un CSV e ritorna il DataFrame normalizzato per upsert."""
    path = UNIVERSE_DIR / filename
    if not path.exists():
        logger.warning(f"CSV mancante: {path}")
        return pd.DataFrame()

    raw = pd.read_csv(path)
    rows = [
        _normalize_row(row.to_dict(), config["universe_group"], config["asset_class_default"])
        for _, row in raw.iterrows()
    ]
    rows = [r for r in rows if r]  # skip righe vuote
    df = pd.DataFrame(rows)
    logger.info(f"{filename}: {len(df)} ticker normalizzati")
    return df


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    engine = get_engine()
    total = 0

    for filename, config in UNIVERSE_FILES.items():
        df = load_universe_file(filename, config)
        if df.empty:
            continue
        n = upsert_dataframe(
            engine=engine,
            df=df,
            table="ticker_universe",
            conflict_cols=["ticker"],
        )
        total += n

    logger.info(f"DONE — totale ticker upsertati: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
