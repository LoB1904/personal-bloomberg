# Architettura Fase 2 — Personal Bloomberg: Componenti Mancanti

## Introduzione

Questo documento fornisce **codice completo e funzionante** per tutti i componenti troncati e mancanti della Fase 2. Ogni sezione è auto-contenuta, testabile, e integrata con lo schema DB definito in `SCHEMA.md`.

**Stato di partenza:** Fine Fase 0.5
- DB schema completo con `paper_signals`, `benchmark_prices`, `paper_strategy_daily`, `screener_results`
- Tre baseline passive girano in paper
- Fondamentali e prezzi storici popolati

**Deliverable Fase 2:**
- ✅ `screener.py` — completamento metodi `run_wealth()`, `save_to_db()`, `run_all()`
- ✅ `paper_trading/signal_logger.py` — completo
- ✅ `paper_trading/pnl_calculator.py` — completo
- ✅ `paper_trading/benchmark_compare.py` — completo
- ✅ GitHub Actions `compute_signals.yml` — completo
- ✅ FastAPI endpoints — completo
- ✅ Schema DDL — completo

---

## 1. Schema Database (DDL)

**File:** `backend/schema.sql`

```sql
-- Tabella universo ticker
CREATE TABLE IF NOT EXISTS ticker_universe (
    ticker_id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(255),
    exchange VARCHAR(10),
    country VARCHAR(2),
    sector VARCHAR(50),
    industry VARCHAR(100),
    asset_class VARCHAR(20),  -- 'stock', 'etf', 'crypto'
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabella prezzi daily
CREATE TABLE IF NOT EXISTS prices_daily (
    price_id SERIAL PRIMARY KEY,
    ticker_id INTEGER NOT NULL REFERENCES ticker_universe(ticker_id),
    date DATE NOT NULL,
    open DECIMAL(12, 4),
    high DECIMAL(12, 4),
    low DECIMAL(12, 4),
    close DECIMAL(12, 4) NOT NULL,
    volume BIGINT,
    adj_close DECIMAL(12, 4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker_id, date)
);

-- Tabella fondamentali snapshot
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
    fund_id SERIAL PRIMARY KEY,
    ticker_id INTEGER NOT NULL REFERENCES ticker_universe(ticker_id),
    snapshot_date DATE NOT NULL,
    pe_ratio DECIMAL(10, 2),
    pb_ratio DECIMAL(10, 2),
    roe_pct DECIMAL(8, 2),
    roic_pct DECIMAL(8, 2),
    debt_to_equity DECIMAL(8, 2),
    fcf_yield_pct DECIMAL(8, 2),
    revenue_growth_yoy_pct DECIMAL(8, 2),
    eps_growth_yoy_pct DECIMAL(8, 2),
    dividend_yield_pct DECIMAL(8, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker_id, snapshot_date)
);

-- Tabella screener results
CREATE TABLE IF NOT EXISTS screener_results (
    result_id SERIAL PRIMARY KEY,
    screener_profile VARCHAR(50) NOT NULL,  -- 'speculative_trend_etf', 'wealth_quality_garp'
    execution_date DATE NOT NULL,
    execution_time TIMESTAMP NOT NULL,
    ticker_id INTEGER NOT NULL REFERENCES ticker_universe(ticker_id),
    rank INTEGER NOT NULL,
    score DECIMAL(8, 2) NOT NULL,
    weight DECIMAL(8, 4),
    signal_details JSONB,  -- dettagli tecnici/fondamentali
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(screener_profile, execution_date, ticker_id)
);

-- Tabella segnali paper trading
CREATE TABLE IF NOT EXISTS paper_signals (
    signal_id SERIAL PRIMARY KEY,
    ticker_id INTEGER NOT NULL REFERENCES ticker_universe(ticker_id),
    screener_profile VARCHAR(50) NOT NULL,
    generated_at TIMESTAMP NOT NULL,
    entry_price DECIMAL(12, 4) NOT NULL,
    entry_date DATE NOT NULL,
    weight DECIMAL(8, 4) DEFAULT 1.0,
    signal_details JSONB,
    
    -- Exit prices per finestra temporale
    exit_price_1w DECIMAL(12, 4),
    exit_price_1m DECIMAL(12, 4),
    exit_price_3m DECIMAL(12, 4),
    exit_price_6m DECIMAL(12, 4),
    exit_price_12m DECIMAL(12, 4),
    
    -- P&L per finestra
    pnl_pct_1w DECIMAL(8, 4),
    pnl_pct_1m DECIMAL(8, 4),
    pnl_pct_3m DECIMAL(8, 4),
    pnl_pct_6m DECIMAL(8, 4),
    pnl_pct_12m DECIMAL(8, 4),
    
    status VARCHAR(20) DEFAULT 'open',  -- 'open', 'closed_1w', 'closed_1m', etc
    closed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabella prezzi benchmark
CREATE TABLE IF NOT EXISTS benchmark_prices (
    bench_id SERIAL PRIMARY KEY,
    benchmark_code VARCHAR(50) NOT NULL,  -- 'VWCE', 'IWQU', 'SPY', 'DUAL_MOMENTUM', 'EW_SP100'
    date DATE NOT NULL,
    close DECIMAL(12, 4) NOT NULL,
    total_return_index DECIMAL(12, 4),  -- con dividendi reinvestiti
    source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(benchmark_code, date)
);

-- Tabella paper strategy daily (per baseline e screener)
CREATE TABLE IF NOT EXISTS paper_strategy_daily (
    strategy_id SERIAL PRIMARY KEY,
    strategy_code VARCHAR(50) NOT NULL,  -- 'baseline_buy_hold_vwce', 'baseline_ew_sp100', 'baseline_dual_momentum', 'screener_speculative', 'screener_wealth'
    date DATE NOT NULL,
    portfolio_value DECIMAL(15, 2) NOT NULL,
    cash_pct DECIMAL(8, 4),
    num_positions INTEGER,
    daily_return_pct DECIMAL(8, 4),
    total_return_pct DECIMAL(8, 4),
    drawdown_pct DECIMAL(8, 4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(strategy_code, date)
);

-- Indici per performance
CREATE INDEX idx_prices_ticker_date ON prices_daily(ticker_id, date DESC);
CREATE INDEX idx_screener_results_date ON screener_results(execution_date DESC, screener_profile);
CREATE INDEX idx_paper_signals_ticker ON paper_signals(ticker_id);
CREATE INDEX idx_paper_signals_generated ON paper_signals(generated_at DESC);
CREATE INDEX idx_benchmark_prices_date ON benchmark_prices(benchmark_code, date DESC);
CREATE INDEX idx_paper_strategy_date ON paper_strategy_daily(strategy_code, date DESC);
```

---

## 2. Completamento `screener.py`

**File:** `backend/app/analytics/screener.py`

```python
"""
Screener orchestrator per Fase 2.
Coordina Trend-Following (Speculative) e Quality/GARP (Wealth).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from decimal import Decimal

import pandas as pd
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import Session

from app.models import (
    TickerUniverse,
    PricesDaily,
    FundamentalsSnapshot,
    ScreenerResults,
    PaperSignals,
)
from app.analytics.technical import TechnicalIndicators
from app.analytics.trend import TrendFollowingScreener
from app.analytics.fundamental import FundamentalScreener

logger = logging.getLogger(__name__)


class ScreenerOrchestrator:
    """Coordina i due screener, produce shortlist ranked, scrive in DB."""

    def __init__(self, engine):
        self.engine = engine
        self.trend_screener = TrendFollowingScreener(engine)
        self.fundamental_screener = FundamentalScreener(engine)

    # ========== SPECULATIVE (TREND-FOLLOWING) ==========

    def run_speculative(
        self,
        execution_date: Optional[datetime] = None,
        min_trend_score: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Screener Trend-Following su sector ETF.
        Ritorna lista di ETF in trend con dimensionamento equal-weight.

        Args:
            execution_date: Data di esecuzione (default: oggi)
            min_trend_score: Soglia minima trend_score per inclusion

        Returns:
            {
                'profile': 'speculative_trend_etf',
                'execution_date': date,
                'execution_time': datetime,
                'shortlist': [
                    {
                        'ticker_id': int,
                        'symbol': str,
                        'rank': int,
                        'score': float,
                        'weight': float,
                        'signal_details': dict,
                    }
                ],
                'n_signals': int,
                'status': 'success' | 'error',
                'error_msg': str | None,
            }
        """
        if execution_date is None:
            execution_date = datetime.utcnow()

        try:
            # Scansiona tutti gli ETF settoriali
            etf_results = self.trend_screener.screen_all(execution_date)

            # Filtra per trend_score minimo
            filtered = [
                r for r in etf_results if r.get("trend_score", 0) >= min_trend_score
            ]

            if not filtered:
                logger.info(
                    f"Speculative [{execution_date.date()}]: no ETF above trend_score {min_trend_score}"
                )
                return {
                    "profile": "speculative_trend_etf",
                    "execution_date": execution_date.date(),
                    "execution_time": execution_date,
                    "shortlist": [],
                    "n_signals": 0,
                    "status": "success",
                    "error_msg": None,
                }

            # Equal-weight dimensionamento
            n_long = len(filtered)
            weight_per_etf = Decimal(1.0) / Decimal(n_long)

            shortlist = []
            for i, result in enumerate(filtered):
                shortlist.append(
                    {
                        "ticker_id": result["ticker_id"],
                        "symbol": result["symbol"],
                        "rank": i + 1,
                        "score": float(result["trend_score"]),
                        "weight": float(weight_per_etf),
                        "signal_details": {
                            "price": float(result["price"]),
                            "ma200": float(result["ma200"]),
                            "ma50": float(result["ma50"]),
                            "return_3m": float(result["return_3m"]),
                            "return_6m": float(result["return_6m"]),
                            "signal": result["signal"],
                        },
                    }
                )

            logger.info(
                f"Speculative [{execution_date.date()}]: {n_long} ETF in trend, "
                f"avg trend_score {sum(r['score'] for r in shortlist) / n_long:.1f}"
            )

            return {
                "profile": "speculative_trend_etf",
                "execution_date": execution_date.date(),
                "execution_time": execution_date,
                "shortlist": shortlist,
                "n_signals": n_long,
                "status": "success",
                "error_msg": None,
            }

        except Exception as e:
            logger.error(f"Speculative screener failed: {e}", exc_info=True)
            return {
                "profile": "speculative_trend_etf",
                "execution_date": execution_date.date() if execution_date else None,
                "execution_time": execution_date,
                "shortlist": [],
                "n_signals": 0,
                "status": "error",
                "error_msg": str(e),
            }

    # ========== WEALTH (QUALITY + GARP) ==========

    def run_wealth(
        self,
        execution_date: Optional[datetime] = None,
        top_n: int = 30,
        min_combined_score: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Screener Quality + GARP su single-name.
        Ritorna top N ticker per combined_score.

        Args:
            execution_date: Data di esecuzione (default: oggi)
            top_n: Numero massimo di ticker da ritornare
            min_combined_score: Soglia minima combined_score per inclusion

        Returns:
            {
                'profile': 'wealth_quality_garp',
                'execution_date': date,
                'execution_time': datetime,
                'shortlist': [
                    {
                        'ticker_id': int,
                        'symbol': str,
                        'rank': int,
                        'score': float,
                        'weight': float,
                        'signal_details': dict,
                    }
                ],
                'n_signals': int,
                'status': 'success' | 'error',
                'error_msg': str | None,
            }
        """
        if execution_date is None:
            execution_date = datetime.utcnow()

        if top_n <= 0:
            return {
                "profile": "wealth_quality_garp",
                "execution_date": execution_date.date(),
                "execution_time": execution_date,
                "shortlist": [],
                "n_signals": 0,
                "status": "error",
                "error_msg": "top_n must be > 0",
            }

        try:
            # Carica tutti i ticker single-name (non ETF, non crypto per Fase 2)
            single_name_ids = self._fetch_single_name_ids()

            if not single_name_ids:
                logger.warning("No single-name tickers found in universe")
                return {
                    "profile": "wealth_quality_garp",
                    "execution_date": execution_date.date(),
                    "execution_time": execution_date,
                    "shortlist": [],
                    "n_signals": 0,
                    "status": "success",
                    "error_msg": None,
                }

            # Calcola score per ogni ticker
            scores = []
            for ticker_id in single_name_ids:
                try:
                    combined = self.fundamental_screener.combined_score(ticker_id)
                    if combined.get("combined_score") is not None:
                        ticker = self._fetch_ticker(ticker_id)
                        if ticker:
                            scores.append(
                                {
                                    "ticker_id": ticker_id,
                                    "symbol": ticker.symbol,
                                    "score": combined["combined_score"],
                                    "quality_score": combined["quality_score"],
                                    "garp_score": combined["garp_score"],
                                }
                            )
                except Exception as e:
                    logger.debug(f"Skipping ticker {ticker_id}: {e}")
                    continue

            if not scores: