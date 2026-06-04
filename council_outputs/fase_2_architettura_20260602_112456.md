# Architettura Fase 2 — Personal Bloomberg: Analytics + Paper Trading Engine

## Problema

Personal Bloomberg è un sistema multi-strategia con paper trading al centro. Fase 2 deve implementare il cuore analitico e il motore di tracciamento: due screener custom (Trend-Following su sector ETF per Speculative, Quality/GARP su single-name per Wealth) che generano segnali loggati in paper trading, con calcolo P&L vs benchmark multipli (VWCE, IWQU, SPY, Dual Momentum) e baseline passive parallele.

**Stato di partenza (fine Fase 0.5):**
- DB schema completo con `paper_signals`, `benchmark_prices`, `paper_strategy_daily`
- Tre baseline passive girano in paper: B&H VWCE, EW S&P 100, Dual Momentum Antonacci
- Universo ~150 single-name + 25 sector ETF + 5 baseline tickers
- Fondamentali e prezzi storici popolati via EODHD/FMP/yfinance

**Gap Fase 2:**
- Indicatori tecnici (MA20/50/200, RSI, MACD, ATR, Bollinger) su serie storiche
- Logica Trend-Following per sector ETF (long se `price > MA200 AND return_3m > 0`)
- Quality score (ROE, ROIC, debt/equity, FCF yield) e GARP score (PEG, revenue growth vs PE)
- Screener orchestrato che produce shortlist ranked e scrive in `screener_results`
- Signal logger: ogni segnale → `paper_signals` con entry_price, profile, generated_at
- P&L calculator: aggiorna exit_price_1w/1m/3m/6m/12m, calcola pnl_pct per finestra
- Benchmark compare: alpha giornaliero vs VWCE, IWQU, SPY, baseline passive
- Workflow GitHub Actions post-market (22:00 UTC): screener → log segnali → aggiorna P&L
- FastAPI endpoints: `/screener/{profile}`, `/paper/track-record`, `/paper/signals`

---

## Valutazione

### Architettura proposta

```
┌─────────────────────────────────────────────────────────┐
│ Layer 4: FastAPI Endpoints                              │
│ /screener/{profile}, /paper/track-record, /paper/signals│
├─────────────────────────────────────────────────────────┤
│ Layer 3: Analytics + Paper Trading                      │
│ ┌──────────────────┐  ┌──────────────────┐             │
│ │ Analytics        │  │ Paper Trading    │             │
│ │ ├─ technical.py  │  │ ├─ signal_logger │             │
│ │ ├─ trend.py      │  │ ├─ pnl_calc      │             │
│ │ ├─ fundamental.py│  │ └─ benchmark_cmp │             │
│ │ └─ screener.py   │  │                  │             │
│ └──────────────────┘  └──────────────────┘             │
├─────────────────────────────────────────────────────────┤
│ Layer 2: Storage (Postgres/Supabase)                    │
│ prices_daily, fundamentals_snapshot, screener_results, │
│ paper_signals, paper_strategy_daily, benchmark_prices  │
├─────────────────────────────────────────────────────────┤
│ Layer 1: GitHub Actions Scheduler                       │
│ compute_signals.yml (22:00 UTC post-market)             │
└─────────────────────────────────────────────────────────┘
```

### Componenti dettagliati

#### 1. **Analytics Layer** (`backend/app/analytics/`)

**`technical.py`** — Indicatori base su pandas-ta
```python
class TechnicalIndicators:
    """Calcola indicatori su serie storica già in DB."""
    
    @staticmethod
    def ma(prices: pd.Series, period: int) -> pd.Series:
        """SMA semplice."""
        return prices.rolling(period).mean()
    
    @staticmethod
    def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """RSI Wilder."""
        return ta.rsi(prices, length=period)
    
    @staticmethod
    def macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
        """MACD + signal + histogram."""
        macd_line = ta.macd(prices, fast=fast, slow=slow, signal=signal)
        return macd_line[f'MACD_{fast}_{slow}_{signal}'], macd_line[f'MACDs_{fast}_{slow}_{signal}']
    
    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average True Range."""
        return ta.atr(high, low, close, length=period)
    
    @staticmethod
    def bollinger_bands(prices: pd.Series, period: int = 20, std: float = 2.0) -> tuple:
        """BB: upper, middle, lower."""
        bb = ta.bbands(prices, length=period, std=std)
        return bb[f'BBU_{period}_{std}'], bb[f'BBM_{period}_{std}'], bb[f'BBL_{period}_{std}']
```

**`trend.py`** — Logica Trend-Following per sector ETF
```python
class TrendFollowingScreener:
    """Screener Speculative: long su sector ETF in trend."""
    
    def __init__(self, engine, lookback_days: int = 250):
        self.engine = engine
        self.lookback = lookback_days
    
    def score_etf(self, ticker_id: int) -> dict:
        """
        Calcola trend score per un ETF.
        Return: {
            'ticker_id': int,
            'price': float,
            'ma200': float,
            'ma50': float,
            'return_3m': float,
            'return_6m': float,
            'signal': 'long' | 'flat',
            'trend_score': 0-100,
        }
        """
        # Fetch ultimi 250 giorni di prezzi
        prices = self._fetch_prices(ticker_id, self.lookback)
        if len(prices) < 200:
            return {'signal': 'insufficient_data'}
        
        # Calcoli
        ma200 = TechnicalIndicators.ma(prices['close'], 200).iloc[-1]
        ma50 = TechnicalIndicators.ma(prices['close'], 50).iloc[-1]
        current_price = prices['close'].iloc[-1]
        
        # Return 3m, 6m
        ret_3m = (prices['close'].iloc[-1] / prices['close'].iloc[-63] - 1) * 100
        ret_6m = (prices['close'].iloc[-1] / prices['close'].iloc[-126] - 1) * 100
        
        # Segnale: long se price > MA200 AND return_3m > 0
        signal = 'long' if (current_price > ma200 and ret_3m > 0) else 'flat'
        
        # Trend score: composito di momentum + posizione rispetto MA
        trend_score = self._compute_trend_score(
            current_price, ma200, ma50, ret_3m, ret_6m
        )
        
        return {
            'ticker_id': ticker_id,
            'price': current_price,
            'ma200': ma200,
            'ma50': ma50,
            'return_3m': ret_3m,
            'return_6m': ret_6m,
            'signal': signal,
            'trend_score': trend_score,
        }
    
    def _compute_trend_score(self, price, ma200, ma50, ret_3m, ret_6m) -> float:
        """Combina segnali in score 0-100."""
        score = 0.0
        score += 20 if price > ma200 else 0  # prezzo sopra MA200
        score += 15 if price > ma50 else 0   # prezzo sopra MA50
        score += 30 * min(ret_3m / 10, 1.0) if ret_3m > 0 else 0  # momentum 3m (max 30)
        score += 20 * min(ret_6m / 15, 1.0) if ret_6m > 0 else 0  # momentum 6m (max 20)
        score += 15 if ret_6m > ret_3m else 0  # accelerazione
        return min(score, 100.0)
    
    def screen_all(self) -> list[dict]:
        """Scansiona tutti i sector ETF, ritorna lista ordinata per trend_score."""
        etf_ids = self._fetch_sector_etf_ids()
        results = []
        for etf_id in etf_ids:
            result = self.score_etf(etf_id)
            if result.get('signal') == 'long':
                results.append(result)
        
        # Ordina per trend_score desc
        return sorted(results, key=lambda x: x['trend_score'], reverse=True)
```

**`fundamental.py`** — Quality score e GARP score
```python
class FundamentalScreener:
    """Screener Wealth: Quality + GARP su single-name."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def quality_score(self, ticker_id: int) -> dict:
        """
        Quality score: ROE, ROIC, debt/equity, FCF yield.
        Normalizza ogni fattore 0-25, somma = 0-100.
        """
        # Fetch ultimi fondamentali
        fund = self._fetch_latest_fundamentals(ticker_id)
        if not fund:
            return {'quality_score': None}
        
        # Percentili di riferimento (calcolati su universo)
        p_roe = self._percentile_rank(fund['roe'], 'roe')          # 0-25
        p_roic = self._percentile_rank(fund['roic'], 'roic')       # 0-25
        p_de = 25 - self._percentile_rank(fund['debt_to_equity'], 'debt_to_equity')  # inverso
        p_fcf = self._percentile_rank(fund['fcf_yield'], 'fcf_yield')  # 0-25
        
        quality_score = p_roe + p_roic + p_de + p_fcf
        
        return {
            'ticker_id': ticker_id,
            'quality_score': quality_score,
            'roe_pct': p_roe,
            'roic_pct': p_roic,
            'de_pct': p_de,
            'fcf_pct': p_fcf,
        }
    
    def garp_score(self, ticker_id: int) -> dict:
        """
        GARP score: PEG ratio (PE / earnings growth), revenue growth vs PE.
        PEG < 1.0 ottimo, 1.0-2.0 buono.
        Revenue growth > PE significa crescita a prezzi ragionevoli.
        """
        fund = self._fetch_latest_fundamentals(ticker_id)
        if not fund or not fund.get('pe_ratio') or not fund.get('eps_growth_yoy'):
            return {'garp_score': None}
        
        pe = fund['pe_ratio']
        eps_growth = fund['eps_growth_yoy']
        rev_growth = fund['revenue_growth_yoy']
        
        # PEG ratio
        peg = pe / max(eps_growth, 0.1) if eps_growth > 0 else float('inf')
        
        # Score: PEG + revenue growth vs PE
        peg_score = max(0, 50 - peg * 10)  # PEG < 1 → 40-50 punti
        growth_score = 50 if rev_growth > pe / 100 else 25  # crescita > PE%
        
        garp_score = (peg_score + growth_score) / 2
        
        return {
            'ticker_id': ticker_id,
            'garp_score': garp_score,
            'peg_ratio': peg,
            'revenue_growth': rev_growth,
            'pe_ratio': pe,
        }
    
    def combined_score(self, ticker_id: int) -> dict:
        """Combina quality_score (60%) + garp_score (40%)."""
        q = self.quality_score(ticker_id)
        g = self.garp_score(ticker_id)
        
        if q.get('quality_score') is None or g.get('garp_score') is None:
            return {'combined_score': None}
        
        combined = q['quality_score'] * 0.6 + g['garp_score'] * 0.4
        
        return {
            'ticker_id': ticker_id,
            'combined_score': combined,
            'quality_score': q['quality_score'],
            'garp_score': g['garp_score'],
        }
```

**`screener.py`** — Orchestrazione e output
```python
class ScreenerOrchestrator:
    """Coordina i due screener, produce shortlist ranked, scrive in DB."""
    
    def __init__(self, engine):
        self.engine = engine
        self.trend = TrendFollowingScreener(engine)
        self.fundamental = FundamentalScreener(engine)
    
    def run_speculative(self) -> list[dict]:
        """Screener Trend-Following: sector ETF."""
        results = self.trend.screen_all()
        
        # Equal-weight dimensionamento tra ETF in trend
        n_long = len(results)
        if n_long == 0:
            logger.info("Speculative: nessun ETF in trend")
            return []
        
        weight_per_etf = 1.0 / n_long
        
        shortlist = [
            {
                'ticker_id': r['ticker_id'],
                'profile': 'speculative_trend_etf',
                'rank': i + 1,
                'score': r['trend_score'],
                'weight': weight_per_etf,
                'signal_details': {
                    'price': r['price'],
                    'ma200': r['ma200'],
                    'return_3m': r['return_3m'],
                },
            }
            for i, r in enumerate(results)
        ]
        
        logger.info(f"Speculative screener: {n_long} ETF in trend")
        return shortlist
    
    def run_wealth(self, top_n: int = 30) -> list[dict]:
        """Screener Quality/GARP: single-name, top 30."""
        # Carica tutti i ticker single-name
        ticker_ids = self._fetch_single_name_ids()
        
        scores = []
        for tid in ticker_ids:
            combined = self.fundamental.combined_score(tid)
            if combined.get('combined_score') is not None:
                scores.append({
                    'ticker_id': tid,
                    'score': combined['combined_score'],
                    'quality': combined['quality_score'],
                    'garp': combined['garp_score'],
                })
        
        # Top 30 per score
        scores.sort(key=lambda x: x['score'], reverse=True)
        shortlist = [
            {
                'ticker_id