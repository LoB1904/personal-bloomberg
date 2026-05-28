"""
Fase 1 — Ingestione indicatori macro.

Fonti:
  FRED API (Federal Reserve):
    DFF       — Fed Funds Rate (daily)
    CPIAUCSL  — CPI USA (monthly)
    UNRATE    — Unemployment Rate (monthly)
    GDP       — US GDP (quarterly)
    T10Y2Y    — Yield Curve 10y-2y spread (daily)

  Eurostat (REST API pubblica, no API key):
    HICP      — Harmonised Index of Consumer Prices Europa

Upsert su macro_indicators con conflict su (indicator_code, date).

Uso:
    python -m app.ingestion.macro
    python -m app.ingestion.macro --days 3650
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
from datetime import date, timedelta

import requests
import urllib3
import pandas as pd
from sqlalchemy import text
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.db import get_engine, upsert_dataframe
from app.core.config import settings

urllib3.disable_warnings()
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

logger = logging.getLogger(__name__)

# ── Configurazione serie FRED ─────────────────────────────────────────

FRED_SERIES: dict[str, dict] = {
    "DFF": {
        "description": "Fed Funds Rate (effective)",
        "frequency":   "daily",
        "source":      "fred",
    },
    "CPIAUCSL": {
        "description": "CPI USA All Items (seasonally adjusted)",
        "frequency":   "monthly",
        "source":      "fred",
    },
    "UNRATE": {
        "description": "US Unemployment Rate",
        "frequency":   "monthly",
        "source":      "fred",
    },
    "GDP": {
        "description": "US Real GDP",
        "frequency":   "quarterly",
        "source":      "fred",
    },
    "T10Y2Y": {
        "description": "10-Year Treasury minus 2-Year Treasury (yield curve)",
        "frequency":   "daily",
        "source":      "fred",
    },
}

_SESSION = requests.Session()
_SESSION.verify = False
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


# ── FRED Client ───────────────────────────────────────────────────────

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fetch_fred_series(
    series_id: str,
    api_key: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """
    Chiama FRED API e ritorna DataFrame con colonne [indicator_code, date, value, source, frequency].
    """
    params = {
        "series_id":         series_id,
        "api_key":           api_key,
        "file_type":         "json",
        "observation_start": start.isoformat(),
        "observation_end":   end.isoformat(),
        "sort_order":        "asc",
    }
    try:
        r = _SESSION.get(_FRED_BASE, params=params, timeout=20)
        if r.status_code == 429:
            logger.warning(f"FRED rate limit su {series_id}")
            return pd.DataFrame()
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"FRED {series_id}: {e}")
        return pd.DataFrame()

    observations = data.get("observations", [])
    rows = []
    meta = FRED_SERIES.get(series_id, {})
    for obs in observations:
        val_str = obs.get("value", ".")
        if val_str == "." or not val_str:
            continue   # FRED usa "." per valori mancanti
        try:
            val = float(val_str)
        except ValueError:
            continue
        rows.append({
            "indicator_code": series_id,
            "date":           obs["date"],
            "value":          val,
            "source":         "fred",
            "frequency":      meta.get("frequency"),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        logger.info(f"FRED {series_id}: {len(df)} osservazioni")
    return df


def fetch_fred(engine, api_key: str, days: int = 3650) -> int:
    """Fetcha tutte le serie FRED e fa upsert in macro_indicators."""
    end   = date.today()
    start = end - timedelta(days=days)
    logger.info(f"FRED: {len(FRED_SERIES)} serie | {start} → {end}")

    all_dfs: list[pd.DataFrame] = []
    for series_id in FRED_SERIES:
        df = _fetch_fred_series(series_id, api_key, start, end)
        if not df.empty:
            all_dfs.append(df)

    if not all_dfs:
        return 0

    combined = pd.concat(all_dfs, ignore_index=True)
    n = upsert_dataframe(engine, combined, "macro_indicators", ["indicator_code", "date"])
    logger.info(f"FRED DONE — {n} righe in macro_indicators")
    return n


# ── Eurostat Client ───────────────────────────────────────────────────

# Dataset HICP mensile EA20 (Eurozona 20 paesi)
_EUROSTAT_HICP_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    "prc_hicp_manr"
    "?format=JSON"
    "&geo=EA20"        # Eurozona 20
    "&coicop=CP00"     # All items
    "&unit=RCH_A"      # Annual rate of change
    "&lang=en"
)


@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _get_eurostat_raw() -> dict:
    r = _SESSION.get(_EUROSTAT_HICP_URL, timeout=30)
    r.raise_for_status()
    return r.json()


def _fetch_eurostat_hicp() -> pd.DataFrame:
    """
    Scarica HICP mensile Eurozona da Eurostat REST API (no API key).
    Ritorna DataFrame con [indicator_code, date, value, source, frequency].
    """
    try:
        data = _get_eurostat_raw()
    except Exception as e:
        logger.error(f"Eurostat HICP: {e}")
        return pd.DataFrame()

    # Struttura risposta Eurostat JSON-stat:
    # data["dimension"]["time"]["category"]["index"] → {periodo: index}
    # data["value"] → {str(index): valore}
    try:
        time_index: dict = data["dimension"]["time"]["category"]["index"]
        values_map: dict = data["value"]
    except (KeyError, TypeError) as e:
        logger.error(f"Eurostat HICP: struttura inattesa ({e}) — resp keys: {list(data.keys())}")
        return pd.DataFrame()

    rows = []
    for period, idx in time_index.items():
        val = values_map.get(str(idx))
        if val is None:
            continue
        # Periodo formato "2024-03" → date 2024-03-01
        try:
            dt = date.fromisoformat(period + "-01")
        except ValueError:
            continue
        rows.append({
            "indicator_code": "HICP_EA20",
            "date":           dt,
            "value":          float(val),
            "source":         "eurostat",
            "frequency":      "monthly",
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        logger.info(f"Eurostat HICP: {len(df)} osservazioni")
    return df


def fetch_eurostat(engine) -> int:
    """Fetcha HICP Eurostat e fa upsert in macro_indicators."""
    df = _fetch_eurostat_hicp()
    if df.empty:
        return 0
    n = upsert_dataframe(engine, df, "macro_indicators", ["indicator_code", "date"])
    logger.info(f"Eurostat DONE — {n} righe in macro_indicators")
    return n


# ── Main ─────────────────────────────────────────────────────────────

def fetch_macro(engine=None, days: int = 3650) -> int:
    """
    Fetcha tutti gli indicatori macro (FRED + Eurostat).
    Ritorna totale righe upsertate.
    """
    if engine is None:
        engine = get_engine()

    fred_key = getattr(settings, "fred_api_key", None) or os.getenv("FRED_API_KEY", "")
    total = 0

    if fred_key:
        total += fetch_fred(engine, fred_key, days)
    else:
        logger.warning(
            "FRED_API_KEY non configurata — skip FRED. "
            "Ottieni la chiave gratuita su https://fred.stlouisfed.org/docs/api/api_key.html"
        )

    total += fetch_eurostat(engine)

    logger.info(f"MACRO DONE — {total} righe totali in macro_indicators")
    return total


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Fetch macro indicators (FRED + Eurostat)")
    parser.add_argument("--days", type=int, default=3650,
                        help="Giorni di storico da scaricare (default 3650 = ~10 anni)")
    args = parser.parse_args()
    fetch_macro(days=args.days)
    return 0


if __name__ == "__main__":
    # Smoke test: verifica Eurostat (no API key) e controlla che FRED_API_KEY sia configurata
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    fred_key = os.getenv("FRED_API_KEY", "")
    if fred_key:
        print(f"FRED_API_KEY: configurata ({fred_key[:8]}...)")
    else:
        print("FRED_API_KEY: NON configurata — aggiungi FRED_API_KEY=xxx in .env")
        print("  Ottieni gratis su: https://fred.stlouisfed.org/docs/api/api_key.html")

    print("Test Eurostat HICP (no API key)...")
    df = _fetch_eurostat_hicp()
    if not df.empty:
        print(f"OK — {len(df)} osservazioni. Ultima: {df['date'].max()} = {df.loc[df['date'].idxmax(), 'value']}%")
    else:
        print("KO — Eurostat non raggiungibile (proxy?)")
    sys.exit(0)
