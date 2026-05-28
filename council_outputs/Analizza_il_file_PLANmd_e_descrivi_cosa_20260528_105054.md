# Fase 1 — Data Layer Completo: Specifica di Implementazione
**Personal Bloomberg | Documento tecnico v2.1**

---

## 1. Obiettivo della Fase 1

Costruire il **data layer fondativo** del sistema: tre pipeline di ingestione indipendenti (fondamentali, news RSS, macro FRED) che girano via GitHub Actions dopo la chiusura di Wall Street, popolano Supabase con dati puliti e validati, e sono pronte a essere consumate dagli screener in Fase 2.

**Input:** Universo ~150 ticker + 25 ETF già in DB dalla Fase 0 (tabelle `ticker_universe`, `prices_daily` già popolate).

**Output atteso a fine Fase 1:**
- `fundamentals_snapshot` — fondamentali aggiornati daily per ogni ticker attivo
- `news_items` — news fresche ogni giorno da 8+ feed RSS, associate ai ticker
- `macro_indicators` — serie FRED aggiornate con gestione del publication lag

**Criteri di completamento:**
- GitHub Actions esegue le tre pipeline senza errori per 5 giorni consecutivi
- Nessun valore `NULL` su campi critici per lo screener (`pe_ttm`, `pb`, `roe_ttm`, `revenue_growth_yoy`) senza log esplicito del motivo
- Nessun duplicato in `news_items` (verificato via query su `content_hash`)
- Tutti i valori percentuali in scala `[0,1]` non `[0,100]` (verificato via `SELECT MAX(gross_margin) FROM fundamentals_snapshot`)

---

## 2. File da creare

```
backend/
├── app/
│   ├── core/
│   │   ├── http_client.py          # Client HTTP con retry + rate limiting thread-safe
│   │   ├── validation.py           # Validazione e normalizzazione dati in ingresso
│   │   └── db.py                   # (già esistente da Fase 0 — solo referenziato)
│   └── ingestion/
│       ├── fundamentals.py         # Pipeline fondamentali EODHD/FMP
│       ├── news.py                 # Aggregatore RSS multi-feed con ticker matching
│       └── macro.py                # Ingestione serie FRED + Eurostat
├── tests/
│   ├── test_fundamentals.py
│   ├── test_news.py
│   ├── test_macro.py
│   └── test_validation.py
└── scripts/
    └── backfill_fundamentals.py    # Backfill 5 anni storico (one-shot)

.github/workflows/
└── ingest_daily.yml                # Aggiornato con i tre job Fase 1
```

**Schema DB — tabelle aggiunte in Fase 1** (le tabelle `ticker_universe` e `prices_daily` esistono già):

```sql
-- Già definite in Fase 0, qui si popolano per la prima volta:
-- fundamentals_snapshot
-- news_items
-- macro_indicators
```

---

## 3. Schema DB — Specifiche complete

### 3.1 `fundamentals_snapshot`

```sql
CREATE TABLE fundamentals_snapshot (
    id                    BIGSERIAL PRIMARY KEY,
    ticker_id             INTEGER NOT NULL REFERENCES ticker_universe(id),
    snapshot_date         DATE NOT NULL,

    -- Valuation
    market_cap            BIGINT,           -- in USD assoluti
    enterprise_value      BIGINT,
    pe_ttm                NUMERIC(12,4),    -- negativo = azienda in perdita (legittimo)
    pe_forward            NUMERIC(12,4),
    pb                    NUMERIC(12,4),
    ps_ttm                NUMERIC(12,4),
    ev_ebitda             NUMERIC(12,4),
    ev_revenue            NUMERIC(12,4),

    -- Profitability (scala [0,1] — NON percentuale ×100)
    gross_margin          NUMERIC(8,6),     -- es. 0.6543 = 65.43%
    operating_margin      NUMERIC(8,6),
    net_margin            NUMERIC(8,6),
    roe_ttm               NUMERIC(8,6),
    roa_ttm               NUMERIC(8,6),
    roic                  NUMERIC(8,6),     -- calcolato in Python, vedi sezione 4b

    -- Growth (scala [0,1] — variazione YoY)
    revenue_growth_yoy    NUMERIC(8,6),     -- es. 0.1234 = +12.34% YoY
    earnings_growth_yoy   NUMERIC(8,6),     -- calcolato in Python, vedi sezione 4b
    fcf_growth_yoy        NUMERIC(8,6),

    -- Financial health
    debt_to_equity        NUMERIC(10,4),    -- ratio puro, non percentuale
    current_ratio         NUMERIC(8,4),
    interest_coverage     NUMERIC(10,4),
    net_debt              BIGINT,           -- in USD assoluti

    -- Cash flow
    fcf_ttm               BIGINT,           -- Free Cash Flow TTM in USD
    fcf_yield             NUMERIC(8,6),     -- FCF / Market Cap, scala [0,1]
    capex_to_revenue      NUMERIC(8,6),

    -- Income statement (raw, per calcoli derivati)
    revenue_ttm           BIGINT,
    operating_income_ttm  BIGINT,
    net_income_ttm        BIGINT,
    ebitda_ttm            BIGINT,

    -- Dividends
    dividend_yield        NUMERIC(8,6),     -- scala [0,1], mai negativo
    dividend_per_share    NUMERIC(10,4),
    payout_ratio          NUMERIC(8,6),     -- scala [0,1]

    -- Shares
    shares_outstanding    BIGINT,
    shares_float          BIGINT,

    -- Metadata
    source                VARCHAR(20) NOT NULL,  -- 'eodhd' | 'fmp' | 'yfinance'
    raw_json              JSONB,                  -- payload grezzo per debug/reprocessing
    fetched_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_fundamentals_ticker_date UNIQUE (ticker_id, snapshot_date)
);

CREATE INDEX idx_fundamentals_ticker_date ON fundamentals_snapshot(ticker_id, snapshot_date DESC);
CREATE INDEX idx_fundamentals_date ON fundamentals_snapshot(snapshot_date);
```

### 3.2 `news_items`

```sql
CREATE TABLE news_items (
    id              BIGSERIAL PRIMARY KEY,
    content_hash    CHAR(16) NOT NULL,      -- SHA256[:16] di lower(title)||published_at::date
    url             VARCHAR(1000),          -- informativo, non unique (URL con tracking params)
    url_normalized  VARCHAR(1000),          -- URL senza query string

    -- Contenuto
    title           VARCHAR(500) NOT NULL,
    summary         TEXT,
    source          VARCHAR(100) NOT NULL,  -- 'reuters', 'ft', 'marketwatch', ecc.
    feed_url        VARCHAR(500),           -- URL del feed RSS sorgente

    -- Temporale
    published_at    TIMESTAMPTZ NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Associazione ticker (molti-a-molti via tabella bridge)
    -- I ticker associati stanno in news_ticker_links

    -- AI / sentiment (popolati in Fase 4)
    sentiment_score NUMERIC(4,3),           -- [-1, 1]
    ai_summary      TEXT,

    CONSTRAINT uq_news_content UNIQUE (content_hash)
);

-- Tabella bridge news ↔ ticker (un articolo può menzionare più ticker)
CREATE TABLE news_ticker_links (
    news_id         BIGINT NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
    ticker_id       INTEGER NOT NULL REFERENCES ticker_universe(id),
    match_method    VARCHAR(20) NOT NULL,   -- 'symbol_exact', 'name_fuzzy', 'sector_tag'
    confidence      NUMERIC(4,3),           -- [0,1]
    PRIMARY KEY (news_id, ticker_id)
);

CREATE INDEX idx_news_published ON news_items(published_at DESC);
CREATE INDEX idx_news_source ON news_items(source);
CREATE INDEX idx_news_ticker_links ON news_ticker_links(ticker_id, news_id DESC);
```

**Nota sul `content_hash`:** calcolato in Python prima dell'upsert come `hashlib.sha256(f"{title.lower().strip()}{published_at.date()}".encode()).hexdigest()[:16]`. Questo gestisce sia i duplicati da URL con tracking params, sia gli aggiornamenti di articoli che cambiano URL ma mantengono titolo e data.

### 3.3 `macro_indicators`

```sql
CREATE TABLE macro_indicators (
    id                  BIGSERIAL PRIMARY KEY,
    indicator_code      VARCHAR(50) NOT NULL,   -- es. 'FEDFUNDS', 'CPIAUCSL'
    indicator_name      VARCHAR(200),
    source              VARCHAR(20) NOT NULL,   -- 'fred', 'eurostat', 'istat'
    frequency           VARCHAR(10) NOT NULL,   -- 'daily', 'monthly', 'quarterly'

    -- Dati
    observation_date    DATE NOT NULL,          -- data a cui si riferisce il valore
    value               NUMERIC(20,6),
    unit                VARCHAR(50),            -- 'percent', 'index', 'billions_usd'

    -- Gestione publication lag
    release_date        DATE,                   -- quando FRED ha pubblicato questo dato
    is_revised          BOOLEAN DEFAULT FALSE,  -- TRUE se il valore è stato revisionato

    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_macro_indicator_date UNIQUE (indicator_code, observation_date)
);

CREATE INDEX idx_macro_code_date ON macro_indicators(indicator_code, observation_date DESC);
```

---

## 4. File core — Implementazione

### 4.1 `core/http_client.py`

**Responsabilità:** Client HTTP riusabile con retry esponenziale e rate limiting thread-safe. Usato da tutti e tre i moduli di ingestione.

```python
"""
core/http_client.py
Client HTTP con retry esponenziale e rate limiting thread-safe.
Usato da fundamentals.py, news.py, macro.py.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Rate limiter thread-safe.
    Garantisce un minimo di `min_interval` secondi tra chiamate consecutive,
    anche con ThreadPoolExecutor in Fase 2+.
    """

    def __init__(self, calls_per_second: float) -> None:
        self.min_interval: float = 1.0 / calls_per_second
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


class APIClient:
    """
    Client HTTP generico con:
    - Rate limiting thread-safe
    - Retry esponenziale su 429 e 5xx
    - Timeout configurabile
    - Logging strutturato
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        calls_per_second: float = 2.0,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.rate_limiter = RateLimiter(calls_per_second)
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        GET con retry automatico.
        Lancia HTTPStatusError su errori non recuperabili (4xx escluso 429).
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if params is None:
            params = {}
        if self.api_key:
            params["api_token"] = self.api_key  # EODHD convention; FMP usa "apikey"

        self.rate_limiter.wait()
        return self._get_with_retry(url, params)

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get_with_retry(self, url: str, params: dict) -> dict[str, Any]:
        response = self._client.get(url, params=params)

        # 429 Too Many Requests: rispetta Retry-After header se presente
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "60"))
            logger.warning(f"Rate limited. Attendo {retry_after}s prima di riprovare.")
            time.sleep(retry_after)
            raise httpx.HTTPStatusError(
                "429 Rate Limited", request=response.request, response=response
            )

        # 5xx: rilancia per trigger del retry
        if response.status_code >= 500:
            response.raise_for_status()

        # 4xx non-429: errore definitivo, non ritentare
        if response.status_code >= 400:
            logger.error(
                f"Errore definitivo {response.status_code} per {url}: {response.text[:200]}"
            )
            response.raise_for_status()

        return response.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "APIClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# Factory functions — una per fonte dati
def get_eodhd_client(api_key: str) -> APIClient:
    return APIClient(
        base_url="https://eodhd.com/api",
        api_key=api_key,
        calls_per_second=2.0,   # EODHD: 100k calls/day, ~1.15/sec safe limit
    )


def get_fmp_client(api_key: str) -> APIClient:
    return APIClient(
        base_url="https://financialmodelingprep.com/api/v3",
        api_key=api_key,
        calls_per_second=1.5,   # FMP Starter: 300 calls/min = 5/sec, ma conservativo
    )


def get_fred_client() -> APIClient:
    """FRED non richiede API key per la maggior parte degli endpoint."""
    return APIClient(
        base_url="https://api.stlouisfed.org/fred",
        calls_per_second=5.0,   # FRED: limite generoso, 120 req/min
    )
```

---

### 4.2 `core/validation.py`

**Responsabilità:** Validazione e normalizzazione dei dati grezzi prima dell'ups