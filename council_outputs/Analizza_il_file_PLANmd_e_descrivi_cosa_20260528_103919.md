# Fase 1 — Data Layer Completo: Specifica Implementativa

## Indice
1. [Obiettivo](#obiettivo)
2. [Perimetro e prerequisiti](#perimetro-e-prerequisiti)
3. [File da creare](#file-da-creare)
4. [Modulo 1 — Fondamentali (EODHD/FMP)](#modulo-1--fondamentali-eodhdfmp)
5. [Modulo 2 — News RSS](#modulo-2--news-rss)
6. [Modulo 3 — Macro FRED](#modulo-3--macro-fred)
7. [Modulo 4 — Backfill storico prezzi (5 anni)](#modulo-4--backfill-storico-prezzi-5-anni)
8. [GitHub Actions workflows](#github-actions-workflows)
9. [Dipendenze](#dipendenze)
10. [Schema DB — estensioni Fase 1](#schema-db--estensioni-fase-1)
11. [Test e validazione](#test-e-validazione)
12. [Criteri di completamento Fase 1](#criteri-di-completamento-fase-1)

---

## Obiettivo

Al termine della Fase 1 il database Supabase contiene:

| Dataset | Copertura | Aggiornamento |
|---|---|---|
| **Fondamentali** | ~150 single-name + 25 ETF | Daily post-chiusura US |
| **Prezzi storici** | 5 anni su tutto l'universo | Backfill one-shot + daily delta |
| **News** | RSS aggregato da 8-10 feed | 2× al giorno (08:00 + 15:00 CET) |
| **Macro FRED** | ~20 serie chiave USA | Weekly (lunedì mattina) |

Questo layer alimenta in modo esclusivo la Fase 2 (screener + paper trading). Nessuna logica di scoring o segnale vive qui: solo ingestione, normalizzazione, persistenza.

**Principio guida:** ogni script è idempotente — rieseguirlo due volte non duplica righe, non solleva eccezioni, lascia il DB nello stesso stato.

---

## Perimetro e prerequisiti

### Cosa è già disponibile dalla Fase 0 e 0.5
- Schema DB completo su Supabase (`ticker_universe`, `prices_daily`, `fundamentals_snapshot`, `news_items`, `macro_indicators`, `benchmark_prices`, `paper_signals`, `paper_strategy_daily`)
- CSV universo: `sp100.csv`, `ftsemib.csv`, `wildcards.csv`, `sector_etfs.csv`, `baselines.csv`
- `backend/app/core/config.py` — gestione env vars
- `backend/app/core/db.py` — connessione SQLAlchemy + Supabase client
- GitHub Actions: `ingest_daily.yml` (scheletro), `update_baselines.yml` (attivo)

### Cosa NON fa la Fase 1
- ❌ Calcolo indicatori tecnici (RSI, MACD, MA) → Fase 2
- ❌ Screener e scoring → Fase 2
- ❌ Paper trading signals → Fase 2
- ❌ AI briefing → Fase 4
- ❌ Backfill macro Eurostat/ISTAT → Fase 6 (complessità sproporzionata ora)

---

## File da creare

```
backend/
├── app/
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── fundamentals.py          # NUOVO — Modulo 1
│   │   ├── news.py                  # NUOVO — Modulo 2
│   │   ├── macro.py                 # NUOVO — Modulo 3
│   │   ├── prices.py                # ESTESO — Modulo 4 (backfill + delta daily)
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── rate_limiter.py      # NUOVO — throttling API calls
│   │       ├── retry.py             # NUOVO — retry con backoff esponenziale
│   │       └── normalizer.py        # NUOVO — normalizzazione campi tra EODHD e FMP
│   ├── models/
│   │   ├── __init__.py
│   │   ├── ticker.py                # già da Fase 0
│   │   ├── prices.py                # già da Fase 0
│   │   ├── fundamentals.py          # NUOVO — SQLAlchemy model
│   │   ├── news.py                  # NUOVO — SQLAlchemy model
│   │   └── macro.py                 # NUOVO — SQLAlchemy model
│   └── core/
│       ├── config.py                # ESTESO — nuove env vars
│       └── db.py                    # invariato
├── scripts/
│   ├── backfill_prices_5y.py        # NUOVO — one-shot, eseguito una volta
│   ├── backfill_fundamentals.py     # NUOVO — one-shot snapshot iniziale
│   └── seed_fred_series.py          # NUOVO — one-shot: carica lista serie FRED
├── tests/
│   ├── ingestion/
│   │   ├── test_fundamentals.py     # NUOVO
│   │   ├── test_news.py             # NUOVO
│   │   └── test_macro.py            # NUOVO
│   └── conftest.py                  # fixtures DB test
└── .github/workflows/
    ├── ingest_daily.yml             # ESTESO — aggiunge fondamentali + news
    ├── ingest_news.yml              # NUOVO — 2× al giorno
    └── ingest_macro_weekly.yml      # NUOVO — lunedì mattina
```

---

## Modulo 1 — Fondamentali (EODHD/FMP)

### `backend/app/ingestion/fundamentals.py`

#### Responsabilità
Scarica e persiste uno snapshot dei fondamentali per ogni ticker attivo nell'universo. Gira una volta al giorno dopo la chiusura US (22:30 CET). Per i ticker EU gira il giorno dopo la chiusura locale.

#### Fonte dati: EODHD vs FMP — logica di fallback

```
Fonte primaria:  EODHD   (endpoint: /fundamentals/{TICKER}.{EXCHANGE})
Fonte fallback:  FMP     (endpoint: /v3/profile/{TICKER} + /v3/ratios-ttm/{TICKER})
Fonte tertiary:  yfinance (solo per US large cap in caso di outage)
```

Il modulo non decide quale fonte usare in modo hard-coded: legge `DATA_PROVIDER` da env (`eodhd` | `fmp`). Il fallback a yfinance è automatico su `HTTPError` o timeout dopo 3 retry.

#### Funzioni principali

```python
def fetch_fundamentals_eodhd(ticker: str, exchange: str) -> dict | None:
    """
    Chiama GET https://eodhd.com/api/fundamentals/{ticker}.{exchange}
    Parametri: api_token, fmt=json
    Restituisce dict grezzo EODHD o None su errore.
    Rate limit EODHD: 1000 req/giorno su piano base → throttle a 1 req/sec.
    """

def fetch_fundamentals_fmp(ticker: str) -> dict | None:
    """
    Chiama /v3/profile/{ticker} per dati aziendali + /v3/ratios-ttm/{ticker} per metriche TTM.
    Merge dei due dict restituito come dict unificato.
    Rate limit FMP Starter: 300 req/min → throttle a 4 req/sec.
    """

def normalize_fundamentals(raw: dict, source: str) -> FundamentalsRecord:
    """
    Mappa i campi eterogenei EODHD/FMP/yfinance su schema interno unificato.
    Schema target (FundamentalsRecord — dataclass):
      - ticker_id: int
      - snapshot_date: date
      - pe_ttm: float | None
      - pb: float | None
      - ps_ttm: float | None
      - ev_ebitda: float | None
      - roe_ttm: float | None
      - roic_ttm: float | None
      - gross_margin: float | None
      - operating_margin: float | None
      - net_margin: float | None
      - debt_to_equity: float | None
      - current_ratio: float | None
      - revenue_ttm: float | None
      - revenue_growth_yoy: float | None
      - earnings_growth_yoy: float | None
      - fcf_ttm: float | None
      - fcf_yield: float | None
      - dividend_yield: float | None
      - payout_ratio: float | None
      - shares_outstanding: int | None
      - market_cap: float | None
      - beta: float | None
      - next_earnings_date: date | None
      - source: str  # 'eodhd' | 'fmp' | 'yfinance'
      - raw_json: dict  # intero payload grezzo per debug
    Valori mancanti → None, mai 0 (0 è un valore valido per alcune metriche).
    """

def upsert_fundamentals(records: list[FundamentalsRecord], session: Session) -> int:
    """
    INSERT INTO fundamentals_snapshot ... ON CONFLICT (ticker_id, snapshot_date) DO UPDATE SET ...
    Restituisce numero di righe inserite/aggiornate.
    Non sovrascrive mai raw_json se il record esiste già con stessa source
    (evita perdita dati in caso di downgrade API).
    """

def run_fundamentals_ingestion(tickers: list[dict], provider: str = "eodhd") -> IngestionReport:
    """
    Entry point principale. Orchestratore:
    1. Carica lista ticker attivi da ticker_universe
    2. Per ogni ticker: fetch → normalize → collect
    3. Batch upsert (chunk da 50 per non saturare connessione)
    4. Logga report: n_success, n_failed, n_skipped, duration_sec, errors[]
    Restituisce IngestionReport (dataclass) per logging strutturato.
    """
```

#### Mapping campi EODHD → schema interno

| Campo EODHD | Campo interno | Note |
|---|---|---|
| `Valuation.TrailingPE` | `pe_ttm` | |
| `Valuation.PriceBookMRQ` | `pb` | |
| `Valuation.PriceSalesTTM` | `ps_ttm` | |
| `Valuation.EnterpriseValueEbitda` | `ev_ebitda` | |
| `Highlights.ReturnOnEquityTTM` | `roe_ttm` | |
| `Highlights.ReturnOnAssetsTTM` | `roic_ttm` | Proxy ROIC |
| `Highlights.GrossProfitTTM` / revenue | `gross_margin` | Calcolato |
| `Highlights.ProfitMargin` | `net_margin` | |
| `Financials.Balance_Sheet.debt_to_equity` | `debt_to_equity` | |
| `Highlights.DilutedEpsTTM` | — | Usato per calcoli interni |
| `Earnings.Next_Earnings_Date` | `next_earnings_date` | |

#### Mapping campi FMP → schema interno

| Campo FMP | Campo interno | Note |
|---|---|---|
| `peRatioTTM` | `pe_ttm` | da ratios-ttm |
| `priceToBookRatioTTM` | `pb` | |
| `priceToSalesRatioTTM` | `ps_ttm` | |
| `enterpriseValueMultipleTTM` | `ev_ebitda` | |
| `returnOnEquityTTM` | `roe_ttm` | |
| `returnOnCapitalEmployedTTM` | `roic_ttm` | |
| `grossProfitMarginTTM` | `gross_margin` | |
| `netProfitMarginTTM` | `net_margin` | |
| `debtEquityRatioTTM` | `debt_to_equity` | |
| `dividendYielTTM` | `dividend_yield` | typo FMP intenzionale |
| `freeCashFlowYieldTTM` | `fcf_yield` | |

---

## Modulo 2 — News RSS

### `backend/app/ingestion/news.py`

#### Responsabilità
Aggrega news da feed RSS pubblici, le normalizza, le associa ai ticker rilevanti tramite matching testuale, e le persiste. Non fa sentiment analysis (→ Fase 4, AI layer). Gira 2× al giorno.

#### Feed RSS configurati

```python
RSS_FEEDS = {
    # Generalist — mercati globali
    "reuters_markets":    "https://feeds.reuters.com/reuters/businessNews",
    "marketwatch":        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "investing_com":      "https://www.investing.com/rss/news.rss",
    "seeking_alpha":      "https://seekingalpha.com/market_currents.xml",

    # Macro / economia
    "ft_markets":         "https://www.ft.com/rss/home/us",
    "wsj_markets":        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",

    # Italia
    "sole24ore":          "https://www.ilsole24ore.com/rss/finanza-e-mercati.xml",
    "milano_finanza":     "https://www.milanofinanza.it/rss",

    # Macro USA specifico
    "fed_press":          "https://www.federalreserve.gov/feeds/press_all.xml",
}
```

> **Nota:** i feed Reuters e WSJ cambiano URL periodicamente. Il modulo ha un meccanismo di health-check: se un feed restituisce 0 articoli per 3 run consecutivi, logga un warning in `ingestion_logs` con `feed_name` e `last_success_at`. Nessun crash silenzioso.

#### Funzioni principali

```python
def fetch_feed(feed_name: str, url: str, timeout: int = 10) -> list[RawEntry]:
    """
    Usa feedparser.parse(url). Gestisce:
    - Feed non raggiungibile → log warning, return []
    - Feed malformato → log warning, return []
    - ETag/Last-Modified caching → evita re-download se feed non aggiornato
    Restituisce lista di RawEntry (dataclass: title, link, summary, published_raw, feed_name).
    """

def parse_published_date(raw_date: str) -> datetime | None:
    """
    Normalizza date RSS (formato RFC 2822, ISO 8601, e varianti) → datetime UTC.
    Restituisce None se non parsabile (non solleva eccezione).
    """

def match_tickers(title: str, summary: str, ticker_lookup: dict[str, int]) -> list[int]:
    """
    Associa un articolo ai ticker rilevanti tramite:
    1. Match esatto su simbolo ticker (es. "AAPL", "$AAPL") nel testo
    2. Match su company name (es. "Apple", "Apple Inc") — lookup da ticker_universe.name