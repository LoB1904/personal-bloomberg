"""
Fase 2 — Trend Following Screener per sector ETF (profilo Speculative).

Logica segnale:
  LONG se price > MA200 AND return_3m > 0
  FLAT altrimenti

Trend score composito 0-100:
  +20  prezzo sopra MA200
  +15  prezzo sopra MA50
  +30  momentum 3m (proporzionale, max a +10%)
  +20  momentum 6m (proporzionale, max a +15%)
  +15  accelerazione (return_6m > return_3m — trend in rafforzamento)

Uso:
    python -m app.analytics.trend
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine
from app.analytics.technical import TechnicalIndicators

logger = logging.getLogger(__name__)

# Soglia minima di righe prezzi per calcolare MA200 affidabilmente
_MIN_ROWS = 200


class TrendFollowingScreener:
    """Screener Speculative: calcola trend score per ogni sector ETF."""

    def __init__(self, engine=None, lookback_days: int = 250):
        self.engine = engine or get_engine()
        self.lookback = lookback_days

    # ── Fetch dati ────────────────────────────────────────────────────

    def _fetch_prices(self, ticker_id: int, lookback_days: int) -> pd.DataFrame:
        """
        Carica gli ultimi lookback_days giorni di prezzi da prices_daily.
        Ritorna DataFrame con indice date e colonne: open, high, low, close, adj_close.
        """
        sql = text("""
            SELECT date, open, high, low, close, adj_close
            FROM prices_daily
            WHERE ticker_id = :tid
            ORDER BY date DESC
            LIMIT :lim
        """)
        with self.engine.connect() as conn:
            result = conn.execute(sql, {"tid": ticker_id, "lim": lookback_days})
            rows   = result.fetchall()
            cols   = list(result.keys())

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=cols)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")

        for col in ("open", "high", "low", "close", "adj_close"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _fetch_sector_etf_ids(self) -> list[dict]:
        """
        Ritorna lista di {id, ticker, name} per tutti i sector ETF attivi.
        Usa universe_group = 'sector_etf' — campo reale nel DB.
        """
        sql = text("""
            SELECT id, ticker, name
            FROM ticker_universe
            WHERE universe_group = 'sector_etf'
              AND is_active = TRUE
            ORDER BY id
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [{"id": r.id, "ticker": r.ticker, "name": r.name} for r in rows]

    # ── Calcolo score ─────────────────────────────────────────────────

    def _compute_trend_score(
        self,
        price: float,
        ma200: float | None,
        ma50:  float | None,
        ret_3m: float,
        ret_6m: float,
    ) -> float:
        """Trend score composito 0-100."""
        score = 0.0

        if ma200 is not None and not pd.isna(ma200):
            score += 20.0 if price > ma200 else 0.0

        if ma50 is not None and not pd.isna(ma50):
            score += 15.0 if price > ma50 else 0.0

        if ret_3m > 0:
            score += 30.0 * min(ret_3m / 10.0, 1.0)   # max 30 a +10%

        if ret_6m > 0:
            score += 20.0 * min(ret_6m / 15.0, 1.0)   # max 20 a +15%

        if ret_6m > ret_3m:
            score += 15.0   # trend in accelerazione

        return min(score, 100.0)

    def score_etf(self, ticker_id: int) -> dict[str, Any]:
        """
        Calcola trend score per un singolo ETF.

        Ritorna dict con:
            ticker_id, price, ma200, ma50,
            return_3m, return_6m, signal, trend_score
        Oppure {"signal": "insufficient_data"} se dati insufficienti.
        """
        df = self._fetch_prices(ticker_id, self.lookback)

        if df.empty or len(df) < _MIN_ROWS:
            logger.debug(f"ticker_id={ticker_id}: dati insufficienti ({len(df)} righe)")
            return {"ticker_id": ticker_id, "signal": "insufficient_data", "trend_score": 0.0}

        close = df["close"].dropna()

        if len(close) < _MIN_ROWS:
            return {"ticker_id": ticker_id, "signal": "insufficient_data", "trend_score": 0.0}

        current_price = float(close.iloc[-1])
        ma200_val = TechnicalIndicators.ma(close, 200).iloc[-1]
        ma50_val  = TechnicalIndicators.ma(close, 50).iloc[-1]

        # Return 3m (~63 trading days) e 6m (~126 trading days)
        ret_3m = (
            (close.iloc[-1] / close.iloc[-63] - 1) * 100
            if len(close) >= 63 else 0.0
        )
        ret_6m = (
            (close.iloc[-1] / close.iloc[-126] - 1) * 100
            if len(close) >= 126 else 0.0
        )

        ma200_f = float(ma200_val) if not pd.isna(ma200_val) else None
        ma50_f  = float(ma50_val)  if not pd.isna(ma50_val)  else None

        signal = (
            "long"
            if (ma200_f is not None and current_price > ma200_f and ret_3m > 0)
            else "flat"
        )

        trend_score = self._compute_trend_score(
            current_price, ma200_f, ma50_f, ret_3m, ret_6m
        )

        return {
            "ticker_id":   ticker_id,
            "price":       current_price,
            "ma200":       ma200_f,
            "ma50":        ma50_f,
            "return_3m":   round(ret_3m, 4),
            "return_6m":   round(ret_6m, 4),
            "signal":      signal,
            "trend_score": round(trend_score, 2),
        }

    def screen_all(self) -> list[dict[str, Any]]:
        """
        Scansiona tutti i sector ETF attivi.
        Ritorna lista di ETF con signal='long', ordinata per trend_score decrescente.
        """
        etfs    = self._fetch_sector_etf_ids()
        results = []

        logger.info(f"TrendScreener: analisi {len(etfs)} sector ETF")

        for etf in etfs:
            result = self.score_etf(etf["id"])
            result["ticker"] = etf["ticker"]
            result["name"]   = etf["name"]

            if result["signal"] == "long":
                results.append(result)
            else:
                logger.debug(
                    f"{etf['ticker']}: {result['signal']} "
                    f"(score={result['trend_score']:.1f})"
                )

        results.sort(key=lambda x: x["trend_score"], reverse=True)
        logger.info(f"TrendScreener: {len(results)}/{len(etfs)} ETF in trend LONG")
        return results


# ── Smoke test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    engine  = get_engine()
    screener = TrendFollowingScreener(engine)

    # Test su un singolo ETF (XLK — Technology, id=7)
    etfs = screener._fetch_sector_etf_ids()
    if not etfs:
        print("KO -- Nessun sector ETF nel DB (universe_group='sector_etf')")
        sys.exit(1)

    first = etfs[0]
    print(f"Test su {first['ticker']} (id={first['id']}) — {first['name']}")

    result = screener.score_etf(first["id"])

    if result["signal"] == "insufficient_data":
        print(f"KO -- Dati insufficienti per {first['ticker']}")
        sys.exit(1)

    print(f"  Prezzo attuale : {result['price']:.2f}")
    print(f"  MA200          : {result['ma200']:.2f}")
    print(f"  MA50           : {result['ma50']:.2f}")
    print(f"  Return 3m      : {result['return_3m']:+.2f}%")
    print(f"  Return 6m      : {result['return_6m']:+.2f}%")
    print(f"  Segnale        : {result['signal'].upper()}")
    print(f"  Trend score    : {result['trend_score']:.1f}/100")

    # Screen completo
    print("\nScreen tutti i sector ETF...")
    long_etfs = screener.screen_all()
    print(f"\nETF in LONG: {len(long_etfs)}/{len(etfs)}")
    for r in long_etfs:
        print(f"  [{r['trend_score']:5.1f}] {r['ticker']:6s} {r['return_3m']:+.1f}% 3m  {r['name']}")

    print("\nAll OK -- TrendFollowingScreener operativo.")
