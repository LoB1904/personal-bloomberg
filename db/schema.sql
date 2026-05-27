-- =====================================================================
-- Personal Bloomberg — Schema Postgres (Supabase)
-- Versione: v3.0 (Fase 0)
-- =====================================================================
-- Convenzioni:
--   * snake_case per tutto
--   * id BIGSERIAL primary key
--   * timestamp con timezone (TIMESTAMPTZ), UTC
--   * date senza timezone (DATE) per giorni di mercato
--   * NUMERIC(20, 8) per prezzi (regge crypto e FX), NUMERIC(20, 4) per ratio
--   * foreign keys ON DELETE su CASCADE solo dove ha senso semantico
--   * un indice su ogni FK + indici composti dove ci sono query frequenti
-- =====================================================================

-- Estensioni utili
CREATE EXTENSION IF NOT EXISTS pgcrypto;       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS btree_gin;      -- indici composti

-- =====================================================================
-- 1. ticker_universe — anagrafica strumenti
-- =====================================================================
CREATE TABLE IF NOT EXISTS ticker_universe (
    id              BIGSERIAL PRIMARY KEY,
    ticker          VARCHAR(32)  NOT NULL UNIQUE,    -- AAPL, VWCE.DE, BTC-USD, EURUSD=X
    name            VARCHAR(255) NOT NULL,
    exchange        VARCHAR(32),                     -- NASDAQ, NYSE, MIL, Xetra, LSE
    country         VARCHAR(2),                      -- ISO 3166 alpha-2
    currency        VARCHAR(8),                      -- USD, EUR, GBP, JPY, CHF
    sector          VARCHAR(64),
    industry        VARCHAR(128),
    asset_class     VARCHAR(32)  NOT NULL,           -- equity, etf, bond, commodity, fx, crypto
    universe_group  VARCHAR(32),                     -- sp100, ftsemib, sector_etf, baseline, wildcard
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    added_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    metadata        JSONB        DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS idx_ticker_universe_asset_class ON ticker_universe(asset_class);
CREATE INDEX IF NOT EXISTS idx_ticker_universe_universe_group ON ticker_universe(universe_group);
CREATE INDEX IF NOT EXISTS idx_ticker_universe_is_active ON ticker_universe(is_active);


-- =====================================================================
-- 2. prices_daily — serie storiche EOD
-- =====================================================================
CREATE TABLE IF NOT EXISTS prices_daily (
    id              BIGSERIAL PRIMARY KEY,
    ticker_id       BIGINT       NOT NULL REFERENCES ticker_universe(id) ON DELETE CASCADE,
    date            DATE         NOT NULL,
    open            NUMERIC(20, 8),
    high            NUMERIC(20, 8),
    low             NUMERIC(20, 8),
    close           NUMERIC(20, 8) NOT NULL,
    adj_close       NUMERIC(20, 8),                  -- close aggiustato per split + dividendi
    volume          BIGINT,
    source          VARCHAR(32)  NOT NULL,           -- yfinance, eodhd, fmp, ccxt
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(ticker_id, date)
);

CREATE INDEX IF NOT EXISTS idx_prices_daily_ticker_date ON prices_daily(ticker_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_prices_daily_date ON prices_daily(date DESC);


-- =====================================================================
-- 3. fundamentals_snapshot — fondamentali per ticker e data
-- =====================================================================
-- Una riga per ticker per data di snapshot (trimestrale o annuale).
-- I valori sono "as reported" alla data di snapshot.
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
    id                  BIGSERIAL PRIMARY KEY,
    ticker_id           BIGINT       NOT NULL REFERENCES ticker_universe(id) ON DELETE CASCADE,
    snapshot_date       DATE         NOT NULL,
    fiscal_period       VARCHAR(8),                  -- FY2025, Q1-2026
    -- Valuation
    market_cap          NUMERIC(24, 2),
    enterprise_value    NUMERIC(24, 2),
    pe_ratio            NUMERIC(20, 4),
    forward_pe          NUMERIC(20, 4),
    pb_ratio            NUMERIC(20, 4),
    ps_ratio            NUMERIC(20, 4),
    ev_ebitda           NUMERIC(20, 4),
    -- Quality
    roe                 NUMERIC(20, 4),
    roic                NUMERIC(20, 4),
    gross_margin        NUMERIC(20, 4),
    operating_margin    NUMERIC(20, 4),
    net_margin          NUMERIC(20, 4),
    -- Leverage / cash
    debt_to_equity      NUMERIC(20, 4),
    net_debt            NUMERIC(24, 2),
    cash_and_equiv      NUMERIC(24, 2),
    free_cash_flow      NUMERIC(24, 2),
    -- Growth
    revenue             NUMERIC(24, 2),
    revenue_growth_yoy  NUMERIC(20, 4),
    eps                 NUMERIC(20, 4),
    eps_growth_yoy      NUMERIC(20, 4),
    -- Dividends
    dividend_yield      NUMERIC(20, 4),
    payout_ratio        NUMERIC(20, 4),
    -- Meta
    source              VARCHAR(32) NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_json            JSONB,                       -- dump completo dalla source per audit
    UNIQUE(ticker_id, snapshot_date, fiscal_period)
);

CREATE INDEX IF NOT EXISTS idx_fund_ticker_date ON fundamentals_snapshot(ticker_id, snapshot_date DESC);


-- =====================================================================
-- 4. news_items — news aggregate da RSS
-- =====================================================================
CREATE TABLE IF NOT EXISTS news_items (
    id              BIGSERIAL PRIMARY KEY,
    ticker_id       BIGINT       REFERENCES ticker_universe(id) ON DELETE SET NULL,  -- NULL = news generica
    source          VARCHAR(64)  NOT NULL,           -- reuters, bloomberg, ft, marketwatch, sole24ore
    title           TEXT         NOT NULL,
    summary         TEXT,
    url             TEXT         NOT NULL,
    published_at    TIMESTAMPTZ  NOT NULL,
    sentiment_score NUMERIC(5, 4),                   -- [-1.0, +1.0] -- da AI in Fase 4
    sentiment_label VARCHAR(16),                     -- positive, negative, neutral
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(url)
);

CREATE INDEX IF NOT EXISTS idx_news_ticker_pub ON news_items(ticker_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news_items(published_at DESC);


-- =====================================================================
-- 5. macro_indicators — serie macroeconomiche (FRED, ISTAT, Eurostat)
-- =====================================================================
CREATE TABLE IF NOT EXISTS macro_indicators (
    id              BIGSERIAL PRIMARY KEY,
    indicator_code  VARCHAR(64)  NOT NULL,           -- DGS10, CPIAUCSL, FEDFUNDS, EU_INFLATION
    indicator_name  VARCHAR(255),
    date            DATE         NOT NULL,
    value           NUMERIC(20, 6) NOT NULL,
    source          VARCHAR(32)  NOT NULL,           -- fred, eurostat, istat
    frequency       VARCHAR(16),                     -- daily, monthly, quarterly, annual
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(indicator_code, date)
);

CREATE INDEX IF NOT EXISTS idx_macro_code_date ON macro_indicators(indicator_code, date DESC);


-- =====================================================================
-- 6. screener_results — shortlist storiche giornaliere
-- =====================================================================
-- Una riga per ticker per esecuzione di screener per data.
-- Permette di ricostruire: "quali titoli erano nella top-20 il 2026-03-15?"
CREATE TABLE IF NOT EXISTS screener_results (
    id              BIGSERIAL PRIMARY KEY,
    run_date        DATE         NOT NULL,
    screener_profile VARCHAR(64) NOT NULL,           -- trend_etf_v1, quality_garp_v1, baseline_ew_sp100, ...
    ticker_id       BIGINT       NOT NULL REFERENCES ticker_universe(id) ON DELETE CASCADE,
    rank            INTEGER      NOT NULL,
    score           NUMERIC(20, 6),                  -- score composito
    scores_json     JSONB,                           -- breakdown dei sotto-score (momentum, quality, valuation...)
    generated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(run_date, screener_profile, ticker_id)
);

CREATE INDEX IF NOT EXISTS idx_screener_date_profile ON screener_results(run_date DESC, screener_profile);
CREATE INDEX IF NOT EXISTS idx_screener_ticker ON screener_results(ticker_id);


-- =====================================================================
-- 7. paper_signals — CORE TABLE — segnali paper trading
-- =====================================================================
-- Ogni segnale generato dagli screener entra qui e viene tracciato a 1w/1m/3m/6m/12m.
CREATE TABLE IF NOT EXISTS paper_signals (
    id                  BIGSERIAL PRIMARY KEY,
    signal_uid          UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    ticker_id           BIGINT       NOT NULL REFERENCES ticker_universe(id) ON DELETE CASCADE,
    screener_profile    VARCHAR(64)  NOT NULL,       -- coerente con screener_results.screener_profile
    signal_type         VARCHAR(16)  NOT NULL,       -- entry, exit, rebalance
    direction           VARCHAR(8)   NOT NULL DEFAULT 'long',  -- long, short, flat
    generated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    entry_date          DATE         NOT NULL,
    entry_price         NUMERIC(20, 8) NOT NULL,
    -- exit tracking (popolato in batch da jobs ricorrenti)
    exit_date           DATE,
    exit_price          NUMERIC(20, 8),
    -- forward returns (popolati a milestone, ognuno con il prezzo close del giorno target)
    price_1w            NUMERIC(20, 8),
    price_1m            NUMERIC(20, 8),
    price_3m            NUMERIC(20, 8),
    price_6m            NUMERIC(20, 8),
    price_12m           NUMERIC(20, 8),
    pnl_pct_1w          NUMERIC(20, 6),              -- (price_1w / entry_price - 1) * 100
    pnl_pct_1m          NUMERIC(20, 6),
    pnl_pct_3m          NUMERIC(20, 6),
    pnl_pct_6m          NUMERIC(20, 6),
    pnl_pct_12m         NUMERIC(20, 6),
    status              VARCHAR(16)  NOT NULL DEFAULT 'open',  -- open, closed, expired
    metadata            JSONB        DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS idx_paper_signals_profile_date ON paper_signals(screener_profile, entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_paper_signals_ticker ON paper_signals(ticker_id);
CREATE INDEX IF NOT EXISTS idx_paper_signals_status ON paper_signals(status);


-- =====================================================================
-- 8. benchmark_prices — serie storiche dei benchmark
-- =====================================================================
-- Separato da prices_daily perché alcuni benchmark sono indici (non ticker quotati come ETF):
-- esempio: MSCI_WORLD (indice TR), SPX (S&P 500 index), e in aggiunta i ticker ETF (VWCE, IWQU, SPY...)
CREATE TABLE IF NOT EXISTS benchmark_prices (
    id                  BIGSERIAL PRIMARY KEY,
    benchmark_code      VARCHAR(32)  NOT NULL,       -- VWCE, IWDA, SPY, EFA, SHY, IWQU, MSCI_WORLD, SPX, FTSEMIB
    date                DATE         NOT NULL,
    close               NUMERIC(20, 8) NOT NULL,
    total_return_index  NUMERIC(20, 8),              -- TR index (con dividendi) se disponibile
    source              VARCHAR(32)  NOT NULL,
    ingested_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(benchmark_code, date)
);

CREATE INDEX IF NOT EXISTS idx_benchmark_code_date ON benchmark_prices(benchmark_code, date DESC);


-- =====================================================================
-- 9. paper_strategy_daily — CORE TABLE — daily NAV per strategia
-- =====================================================================
-- Una riga per strategia per giorno.
-- Strategy_code coerente con screener_profile delle baseline e degli screener custom.
CREATE TABLE IF NOT EXISTS paper_strategy_daily (
    id                  BIGSERIAL PRIMARY KEY,
    strategy_code       VARCHAR(64)  NOT NULL,       -- baseline_bh_vwce, baseline_ew_sp100, baseline_dual_momentum, trend_etf_v1, quality_garp_v1
    date                DATE         NOT NULL,
    portfolio_value     NUMERIC(20, 4) NOT NULL,     -- NAV totale (cash + posizioni mark-to-market)
    cash_value          NUMERIC(20, 4) NOT NULL,
    invested_value      NUMERIC(20, 4) NOT NULL,
    cash_pct            NUMERIC(8, 4),               -- % cash (calcolata)
    num_positions       INTEGER      NOT NULL DEFAULT 0,
    daily_return_pct    NUMERIC(20, 6),              -- variazione vs giorno precedente
    total_return_pct    NUMERIC(20, 6),              -- da inception
    drawdown_pct        NUMERIC(20, 6),              -- vs peak storico (negativo)
    benchmark_code      VARCHAR(32),                 -- benchmark di riferimento per questa strategia
    alpha_pct           NUMERIC(20, 6),              -- excess return cumulato vs benchmark
    metadata            JSONB        DEFAULT '{}'::JSONB,
    computed_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_code, date)
);

CREATE INDEX IF NOT EXISTS idx_paper_strategy_code_date ON paper_strategy_daily(strategy_code, date DESC);
CREATE INDEX IF NOT EXISTS idx_paper_strategy_date ON paper_strategy_daily(date DESC);


-- =====================================================================
-- 10. alerts — alert configurati e storico fire
-- =====================================================================
CREATE TABLE IF NOT EXISTS alerts (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    ticker_id       BIGINT       REFERENCES ticker_universe(id) ON DELETE CASCADE,
    condition_type  VARCHAR(32)  NOT NULL,           -- price_above, price_below, rsi_below, breakout, custom
    condition_json  JSONB        NOT NULL,           -- soglie + parametri
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    notification    VARCHAR(32)  NOT NULL DEFAULT 'log',  -- log, email, telegram
    last_fired_at   TIMESTAMPTZ,
    fire_count      INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts(ticker_id);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active);


-- =====================================================================
-- 11. portfolio — posizioni reali o paper personali (long term)
-- =====================================================================
CREATE TABLE IF NOT EXISTS portfolio (
    id              BIGSERIAL PRIMARY KEY,
    portfolio_name  VARCHAR(64)  NOT NULL,           -- 'real_ibkr', 'paper_wealth', 'paper_speculative'
    ticker_id       BIGINT       NOT NULL REFERENCES ticker_universe(id),
    quantity        NUMERIC(20, 8) NOT NULL,
    avg_entry_price NUMERIC(20, 8) NOT NULL,
    entry_date      DATE         NOT NULL,
    exit_date       DATE,
    exit_price      NUMERIC(20, 8),
    status          VARCHAR(16)  NOT NULL DEFAULT 'open',
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_name_status ON portfolio(portfolio_name, status);
CREATE INDEX IF NOT EXISTS idx_portfolio_ticker ON portfolio(ticker_id);


-- =====================================================================
-- Trigger: updated_at automatico
-- =====================================================================
CREATE OR REPLACE FUNCTION trg_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS alerts_updated_at ON alerts;
CREATE TRIGGER alerts_updated_at
    BEFORE UPDATE ON alerts
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

DROP TRIGGER IF EXISTS portfolio_updated_at ON portfolio;
CREATE TRIGGER portfolio_updated_at
    BEFORE UPDATE ON portfolio
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();


-- =====================================================================
-- View comode (lettura)
-- =====================================================================

-- Ultimo prezzo close per ogni ticker
CREATE OR REPLACE VIEW v_latest_prices AS
SELECT DISTINCT ON (p.ticker_id)
    t.ticker, t.name, t.asset_class,
    p.date AS last_date, p.close, p.adj_close, p.volume
FROM prices_daily p
JOIN ticker_universe t ON t.id = p.ticker_id
ORDER BY p.ticker_id, p.date DESC;

-- Shortlist piu' recente per ogni screener
CREATE OR REPLACE VIEW v_latest_shortlist AS
SELECT s.run_date, s.screener_profile, s.rank, s.score, s.scores_json,
       t.ticker, t.name, t.sector, t.asset_class
FROM screener_results s
JOIN ticker_universe t ON t.id = s.ticker_id
WHERE s.run_date = (
    SELECT MAX(run_date) FROM screener_results s2 WHERE s2.screener_profile = s.screener_profile
)
ORDER BY s.screener_profile, s.rank;

-- Performance riepilogo per strategia (ultima data disponibile)
CREATE OR REPLACE VIEW v_strategy_summary AS
SELECT DISTINCT ON (strategy_code)
    strategy_code, date AS as_of_date,
    portfolio_value, total_return_pct, drawdown_pct,
    benchmark_code, alpha_pct, num_positions
FROM paper_strategy_daily
ORDER BY strategy_code, date DESC;


-- =====================================================================
-- FINE SCHEMA — 2026-05 / v3.0
-- =====================================================================
