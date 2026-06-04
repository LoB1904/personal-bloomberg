"""
Fase 2 — Indicatori tecnici su serie storiche.

Implementazione pure-pandas: matematicamente identica a pandas-ta ma senza
dipendenze esterne oltre a pandas (pandas-ta non supporta Python 3.10).

Ogni metodo prende pd.Series e ritorna pd.Series (o tuple di Series per
indicatori multi-output come MACD e Bollinger).

Uso:
    from app.analytics.technical import TechnicalIndicators as TI

    ma200            = TI.ma(close, 200)
    rsi              = TI.rsi(close)
    macd, signal     = TI.macd(close)
    upper, mid, lower = TI.bollinger_bands(close)
    atr              = TI.atr(high, low, close)
"""
from __future__ import annotations

import pandas as pd


class TechnicalIndicators:
    """Calcola indicatori tecnici su serie storiche già in DB."""

    @staticmethod
    def ma(prices: pd.Series, period: int) -> pd.Series:
        """SMA semplice. Primi (period-1) valori sono NaN."""
        return prices.rolling(period).mean()

    @staticmethod
    def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """
        RSI con smoothing di Wilder (identico a pandas-ta e TradingView).
        Valori in [0, 100].
        """
        delta    = prices.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
        rs       = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(
        prices: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[pd.Series, pd.Series]:
        """
        MACD line e signal line (EMA del MACD).
        Ritorna (macd_line, signal_line).
        """
        ema_fast    = prices.ewm(span=fast, adjust=False).mean()
        ema_slow    = prices.ewm(span=slow, adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return macd_line, signal_line

    @staticmethod
    def atr(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """
        Average True Range con smoothing di Wilder.
        Misura la volatilità in punti assoluti.
        """
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                (high - low),
                (high - prev_close).abs(),
                (low  - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1.0 / period, adjust=False).mean()

    @staticmethod
    def bollinger_bands(
        prices: pd.Series,
        period: int = 20,
        std: float = 2.0,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bande di Bollinger.
        Ritorna (upper, middle, lower).
        """
        middle = prices.rolling(period).mean()
        sigma  = prices.rolling(period).std(ddof=0)
        upper  = middle + std * sigma
        lower  = middle - std * sigma
        return upper, middle, lower


# ── Smoke test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import numpy as np

    N   = 300
    rng = np.random.default_rng(42)
    prices = pd.Series(100 + rng.standard_normal(N).cumsum(), name="close")
    high   = prices + rng.uniform(0.5, 2.0, N)
    low    = prices - rng.uniform(0.5, 2.0, N)

    # MA200
    ma200 = TechnicalIndicators.ma(prices, 200)
    assert len(ma200) == N,                      f"MA200: atteso {N}, ottenuto {len(ma200)}"
    assert ma200.iloc[:199].isna().all(),         "MA200: prime 199 righe devono essere NaN"
    assert ma200.iloc[199:].notna().all(),        "MA200: da riga 200 non deve avere NaN"
    print(f"MA200       OK -- len={len(ma200)}, ultimo={ma200.iloc[-1]:.4f}")

    # RSI
    rsi = TechnicalIndicators.rsi(prices)
    assert len(rsi) == N
    assert rsi.dropna().between(0, 100).all(),    "RSI: valori devono essere in [0, 100]"
    print(f"RSI         OK -- len={len(rsi)}, ultimo={rsi.iloc[-1]:.2f}")

    # MACD
    macd_line, signal_line = TechnicalIndicators.macd(prices)
    assert len(macd_line) == N
    assert len(signal_line) == N
    print(f"MACD        OK -- len={len(macd_line)}, macd={macd_line.iloc[-1]:.4f}, signal={signal_line.iloc[-1]:.4f}")

    # ATR
    atr = TechnicalIndicators.atr(high, low, prices)
    assert len(atr) == N
    assert (atr.dropna() >= 0).all(),             "ATR: valori devono essere >= 0"
    print(f"ATR         OK -- len={len(atr)}, ultimo={atr.iloc[-1]:.4f}")

    # Bollinger Bands
    upper, mid, lower = TechnicalIndicators.bollinger_bands(prices)
    assert len(upper) == N
    assert (upper.dropna() >= mid.dropna()).all(), "BB: upper >= middle"
    assert (mid.dropna() >= lower.dropna()).all(), "BB: middle >= lower"
    print(f"Bollinger   OK -- len={len(upper)}, upper={upper.iloc[-1]:.4f}, lower={lower.iloc[-1]:.4f}")

    print("\nAll OK -- TechnicalIndicators operativo.")
