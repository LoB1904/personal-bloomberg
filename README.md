# Personal Bloomberg — Quantitative Investment Platform

Piattaforma quantitativa personale per screening, analisi e tracking di strategie di investimento.
Dati reali via yfinance, DB su Supabase, strategie backtestaste su 10 anni di storico.

**Owner:** Lorenzo | **Status:** Fase 0.5 completata | **Piano completo:** [docs/PLAN.md](docs/PLAN.md)

---

## Architecture

Il sistema è organizzato in fasi incrementali. Ogni fase produce output misurabili prima di avanzare.

| Fase | Stato | Contenuto |
|---|---|---|
| **0.5 — Baseline Strategies** | ✅ Completata | DB su Supabase, universo 200+ ticker, 3 strategie passive con 10 anni di NAV storico in `paper_strategy_daily` |
| **1 — Data Layer** | Prossima | Fondamentali (EODHD/FMP), news RSS, macro FRED. Backfill 5 anni su tutto l'universo |
| **2 — Screener Custom** | Pianificata | Trend-Following su sector ETF (sleeve Speculative) + Quality/GARP single-name (sleeve Wealth). Ogni segnale → `paper_signals`, P&L tracciato vs baseline |
| **3 — Frontend React** | Pianificata | Dashboard Scalable-style: shortlist giornaliera, heatmap, pagina Track Record (baseline + custom sovrapposte) |
| **4 — Claude API** | Pianificata | Briefing mattutino automatico, Q&A via function calling su dati portfolio, commentary settimanale performance |

---

## Baseline Results

Numeri reali su dati Yahoo Finance, capitale iniziale 100.000. Questi sono i benchmark da battere in Fase 2.

| Strategia | Periodo | CAGR | Sharpe | Max DD | NAV finale |
|---|---|---|---|---|---|
| Buy & Hold VWCE.DE | ~7 anni | 12.64% | 0.56 | -33.41% | 225.593 |
| Dual Momentum Antonacci | ~9 anni | 10.01% | 0.41 | -33.72% | 235.743 |
| Equal-Weight S&P100 | ~10 anni | 17.05% | 0.77 | -34.05% | 481.656 |

> These are the benchmarks to beat in Phase 2 custom screeners — on Sharpe and MaxDD, not just raw return.

**Nota metodologica:** Dual Momentum e EW S&P100 hanno periodi più lunghi perché VWCE.DE è quotato dal 2019 su Yahoo Finance. Il MaxDD quasi identico (~-33%) su tutte e 3 le strategie riflette i drawdown del 2020 (Covid) e 2022 (rialzo tassi): nessuna baseline ha protetto il capitale in modo significativo in quei periodi.

---

## Stack

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.10+ |
| ORM / DB | SQLAlchemy 2.0 + psycopg2 |
| Database | Supabase (PostgreSQL cloud) |
| Scheduler | GitHub Actions (cron) |
| Dati di mercato | yfinance (Fase 0) → EODHD / FMP (Fase 1+) |
| Data wrangling | pandas 2.2, numpy 2.1 |
| Config | pydantic-settings, python-dotenv |
| API REST | FastAPI (Fase 2+) |
| Frontend | React 18 + Vite + Tailwind (Fase 3) |
| AI | Claude API — Sonnet 4.6 + Haiku 4.5 (Fase 4) |

---

## Universe

| Gruppo | Ticker | Note |
|---|---|---|
| S&P 100 | 103 ticker | AAPL, MSFT, NVDA, JPM, AMZN… |
| FTSE MIB | ~40 ticker | ISP.MI, ENI.MI, ENEL.MI, UCG.MI… |
| Sector ETF | 16 ticker | XLK, XLF, XLE, XLV, TLT, GLD, EEM… |
| Wildcard | ~30 ticker | Mix EU, EM, factor ETF |
| Benchmark ETF | 5 ticker | VWCE.DE, SPY, EFA, SHY, IWQU.L |

---

## DB Schema

11 tabelle, 3 view, trigger automatici per `updated_at`.

| Tabella | Contenuto |
|---|---|
| `ticker_universe` | Anagrafica strumenti (ticker, exchange, sector, asset_class) |
| `prices_daily` | Serie storiche OHLCV + adj_close (255k+ righe) |
| `benchmark_prices` | Prezzi 5 benchmark ETF con total return index |
| `paper_strategy_daily` | NAV giornaliero per ogni strategia (baseline + custom) |
| `paper_signals` | Segnali paper trading: entry, exit, P&L forward |
| `fundamentals_snapshot` | PE, PB, ROE, ROIC, FCF per snapshot date |
| `news_items` | News RSS aggregate con sentiment score |
| `macro_indicators` | Serie macro FRED (tassi, inflazione, VIX) |
| `screener_results` | Shortlist giornaliere con rank e score per screener |
| `alerts` | Alert configurabili con condition_json |
| `portfolio` | Posizioni reali o paper long-term |

Schema completo: [db/schema.sql](db/schema.sql)

---

## Quick Start

```bash
cd personal-bloomberg/backend
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell

pip install -r requirements.txt

# Compila .env con credenziali Supabase
cp .env.example .env

# Carica universo ticker (200+ ticker da CSV)
python -m app.ingestion.load_universe

# Fetch 10 anni benchmark + S&P100, calcola 3 baseline
python -m app.baselines.run_baselines --days 3650
```

---

## Author Note

> Built to learn quantitative finance and AI engineering.
> Every architectural decision is documented in [/docs](docs/).

