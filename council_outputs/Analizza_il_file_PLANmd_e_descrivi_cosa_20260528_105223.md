# Fase 1 — Data Layer Completo: Specifica di Implementazione

**Progetto:** Personal Bloomberg
**Fase:** 1 di 8
**Dipendenze:** Fase 0 completata (DB schema, universo CSV, GitHub Actions base), Fase 0.5 completata (baseline passive attive)
**Sessioni stimate:** 3-4
**Deliverable finale:** DB popolato con fondamentali + news fresche ogni giorno, macro FRED aggiornata, backfill 5 anni completato

---

## 1. Obiettivo

Costruire il **layer di ingestione dati completo** che alimenta tutti i moduli successivi (screener, paper trading, AI briefing). Al termine della Fase 1 il sistema deve:

1. Scaricare e normalizzare **fondamentali** (P/E, P/B, ROE, ROIC, FCF, revenue growth, debt) per ~150 ticker + 25 ETF dall'API primaria (EODHD o FMP)
2. Aggregare **news RSS** da 8+ feed finanziari, deduplicarle e associarle ai ticker
3. Scaricare **indicatori macro FRED** (tassi, inflazione, employment, yield curve) con aggiornamento automatico
4. Eseguire il **backfill di 5 anni** di prezzi e fondamentali sull'universo completo
5. Girare tutto in modo **affidabile ogni giorno** via GitHub Actions senza intervento manuale

### Perimetro IN scope

| Modulo | Cosa fa | File |
|---|---|---|
| **Fondamentali** | Fetch + parse + upsert su `fundamentals_snapshot` | `fundamentals.py`, `eodhd_client.py`, `fmp_client.py` |
| **News RSS** | Fetch feed + parse + dedup + upsert su `news_items` | `news.py` |
| **Macro FRED** | Fetch serie FRED + upsert su `macro_indicators` | `macro.py` |
| **Prezzi aggiornati** | Aggiornamento daily `prices_daily` (EODHD/FMP) | `prices.py` (aggiornato) |
| **Validazione** | Controlli range, completezza, anomalie prima dell'upsert | `validators.py` |
| **Base client** | HTTP, retry, rate limit, auth — riusato da tutti | `base_client.py` |
| **Backfill** | Script one-shot per 5Y di storico | `scripts/backfill_phase1.py` |
| **Workflow CI** | GitHub Actions daily ingestione completa | `ingest_daily.yml` (aggiornato) |

### Perimetro OUT scope (Fase 2+)

- Screener e scoring (Fase 2)
- Indicatori tecnici calcolati (RSI, MACD, MA — Fase 2)
- AI briefing (Fase 4)
- Backtester (Fase 5)
- Dati crypto real-time (ccxt — Fase 2)
- Dati Eurostat/ISTAT (complessità aggiuntiva, bassa priorità per Fase 1)

---

## 2. Struttura file da creare

```
backend/
├── app/
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── base_client.py          # HTTP client base — riusato da tutti i moduli
│   │   ├── eodhd_client.py         # Client EODHD (fonte primaria)
│   │   ├── fmp_client.py           # Client FMP (fallback)
│   │   ├── fundamentals.py         # Orchestratore fondamentali (usa eodhd + fmp)
│   │   ├── news.py                 # RSS aggregator
│   │   ├── macro.py                # FRED client + ingestione
│   │   ├── prices.py               # Aggiornamento prezzi daily (già parziale da Fase 0)
│   │   └── validators.py           # Validazione dati prima dell'upsert
│   └── models/
│       └── fundamentals.py         # Dataclass FundamentalsSnapshot (già in Fase 0)
│
scripts/
│   ├── backfill_phase1.py          # Backfill 5Y fondamentali + prezzi
│   └── verify_phase1.py            # Script verifica: conta righe, controlla gap
│
tests/
│   ├── test_base_client.py
│   ├── test_eodhd_client.py
│   ├── test_fmp_client.py
│   ├── test_fundamentals.py
│   ├── test_news.py
│   ├── test_macro.py
│   └── test_validators.py
│
.github/workflows/
│   └── ingest_daily.yml            # Aggiornato: aggiunge fondamentali + news + macro
│
requirements.txt                    # Aggiornato con nuove dipendenze
```

---

## 3. Modulo 0 — `base_client.py`

Fondamenta riusate da tutti i client HTTP. Gestisce auth, retry, rate limiting, logging.

```python
# backend/app/ingestion/base_client.py

"""
HTTP base client per tutti i moduli di ingestione.

Responsabilità:
- Autenticazione (query param o header)
- Rate limiting thread-safe
- Retry con backoff esponenziale su errori transitori (network + HTTP 429/5xx)
- Logging strutturato di ogni chiamata
- Timeout configurabile

NON gestisce:
- Parsing della risposta (delegato ai client specifici)
- Logica di business (delegata agli orchestratori)
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class AuthStyle(Enum):
    QUERY_PARAM = "query_param"   # EODHD: ?api_token=...
    HEADER_BEARER = "header"      # FMP: Authorization: Bearer ...


class RateLimiter:
    """
    Rate limiter thread-safe per chiamate API.

    Garantisce che non vengano effettuate più di `calls_per_minute`
    chiamate nell'arco di un minuto, anche con ThreadPoolExecutor.
    """

    def __init__(self, calls_per_minute: int) -> None:
        self.min_interval: float = 60.0 / calls_per_minute
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                logger.debug("RateLimiter: sleep %.2fs", sleep_time)
                time.sleep(sleep_time)
            self._last_call = time.monotonic()


def _is_retryable_http_error(exc: BaseException) -> bool:
    """
    Restituisce True per errori HTTP transitori che vale la pena ritentare.
    429 Too Many Requests, 500/502/503/504 server errors.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


class BaseAPIClient:
    """
    Client HTTP base con retry, rate limiting e logging.

    Parametri:
        base_url:          URL base dell'API (es. "https://eodhd.com/api")
        api_key:           Chiave API
        auth_style:        Come passare la chiave (query param o header)
        auth_param_name:   Nome del parametro (es. "api_token" per EODHD)
        calls_per_minute:  Limite rate (default 60)
        timeout:           Timeout HTTP in secondi (default 30)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        auth_style: AuthStyle = AuthStyle.QUERY_PARAM,
        auth_param_name: str = "api_token",
        calls_per_minute: int = 60,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.auth_style = auth_style
        self.auth_param_name = auth_param_name
        self.rate_limiter = RateLimiter(calls_per_minute)
        self.timeout = timeout

    def _build_headers(self) -> dict[str, str]:
        if self.auth_style == AuthStyle.HEADER_BEARER:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def _build_params(self, params: dict | None) -> dict:
        base = {"fmt": "json"}
        if params:
            base.update(params)
        if self.auth_style == AuthStyle.QUERY_PARAM:
            base[self.auth_param_name] = self.api_key
        return base

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=(
            retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError))
            | retry_if_exception(predicate=_is_retryable_http_error)
        ),
        reraise=True,
    )
    def get(self, endpoint: str, params: dict | None = None) -> Any:
        """
        Esegue una GET con retry automatico su errori transitori.

        Gestisce:
        - Timeout di rete (httpx.TimeoutException)
        - Errori di connessione (httpx.NetworkError)
        - HTTP 429, 500, 502, 503, 504 (con backoff esponenziale 5s → 60s)

        NON gestisce (rilancia immediatamente):
        - HTTP 401/403 (credenziali errate — non ritentare)
        - HTTP 404 (ticker non trovato — non ritentare)
        """
        self.rate_limiter.wait()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        logger.debug("GET %s params=%s", url, {k: v for k, v in (params or {}).items() if k != self.auth_param_name})

        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                url,
                params=self._build_params(params),
                headers=self._build_headers(),
            )

        if response.status_code == 404:
            logger.warning("404 Not Found: %s", url)
            return None  # caller gestisce None come "ticker non trovato"

        if response.status_code in (401, 403):
            raise PermissionError(
                f"Auth error {response.status_code} su {url}. "
                "Verificare API key e piano sottoscritto."
            )

        response.raise_for_status()

        logger.debug("Response %d — %s (%.0f bytes)", response.status_code, url, len(response.content))
        return response.json()
```

---

## 4. Modulo 1A — `eodhd_client.py`

Client specifico per EODHD. Gestisce la mappatura exchange → suffisso EODHD e il parsing del JSON fondamentali.

```python
# backend/app/ingestion/eodhd_client.py

"""
Client EODHD per fondamentali e prezzi EOD.

Documentazione API: https://eodhd.com/financial-apis/

Endpoint usati:
- /fundamentals/{TICKER}.{EXCHANGE}  — fondamentali completi
- /eod/{TICKER}.{EXCHANGE}           — prezzi EOD storici
- /real-time/{TICKER}.{EXCHANGE}     — prezzo last (usato per verifica)

Rate limit piano Basic ($19.99/mese): 100.000 API calls/giorno, 1000/minuto
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .base_client import AuthStyle, BaseAPIClient

logger = logging.getLogger(__name__)

# Mappatura exchange interno → suffisso EODHD
# Fonte: https://eodhd.com/financial-apis/list-supported-exchanges/
EXCHANGE_MAP: dict[str, str] = {
    # USA
    "NASDAQ": "US",
    "NYSE": "US",
    "NYSEARCA": "US",       # ETF (SPY, GLD, TLT...)
    "BATS": "US",
    # Europa
    "XETRA": "F",           # Frankfurt (non "XETRA" — errore comune)
    "MIL": "MI",            # Milano — FTSE MIB
    "LSE": "LSE",           # London
    "EURONEXT_PA": "PA",    # Parigi
    "EURONEXT_AM": "AS",    # Amsterdam (ASML, ADYEN...)
    "EURONEXT_BR": "BR",    # Bruxelles
    "SWX": "SW",            # Svizzera
    "BME": "MC",            # Madrid
    # Asia
    "TSE": "T",             # Tokyo
    "HKEX": "HK",           # Hong Kong
}


@dataclass
class EODHDPrice:
    """Singola riga prezzi EOD da EODHD."""
    date: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float
    volume: int


@dataclass
class EODHDFundamentals:
    """
    Fondamentali grezzi da EODHD, prima della normalizzazione.
    Contiene solo i campi che useremo — il JSON completo è 50-200KB.
    """
    symbol: str
    exchange: str
    # Valuation
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    ps_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None
    # Profitability
    roe: Optional[float] = None
    roa: Optional[float] = None
    roic: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    # Growth (trailing)
    revenue_ttm: Optional[float] = None
    revenue_growth_yoy: Optional[float] = None
    earnings_growth_yoy: Optional[float] = None
    # Balance sheet
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    # Cash flow
    free_cash_flow: Optional[float] = None
    fcf_yield: Optional[float] = None
    # Dividends
    dividend_yield: Optional[float] = None
    payout_ratio: Optional[float] = None
    # Market data
    market_cap: Optional[float] = None
    beta: Optional[float] = None
    # Metadata
    sector: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)


class EODHDClient(BaseAPIClient):
    """
    Client EO