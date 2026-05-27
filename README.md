# Personal Bloomberg

Piattaforma personale di intelligence finanziaria: dati di mercato EOD, screener multi-strategy, paper trading forward, baseline passive di riferimento e AI layer (Claude).

**Owner:** Lorenzo
**Status:** Fase 0 — Setup
**Piano completo:** [docs/PLAN.md](docs/PLAN.md)

## Cos'è in breve

Sistema modulare a 5 layer (ingestione → storage Postgres → analytics+paper trading → AI → frontend React) che:

1. Ingerisce dati EOD su un universo di ~150 single-name + 25 sector ETF + 5 baseline ticker.
2. Genera **shortlist giornaliere** da due screener: Trend-Following su sector ETF (sleeve Speculative) e Quality+GARP single-name (sleeve Wealth).
3. Logga ogni segnale in paper trading e ne traccia P&L vs **5 benchmark** (MSCI World, MSCI World Quality, B&H VWCE, Equal-Weight S&P 100, Dual Momentum Antonacci).
4. A 6+ mesi di tracking, risponde onestamente alla domanda: il sistema custom batte le baseline passive?

## Stack

| Layer | Tecnologia |
|---|---|
| Backend | Python 3.11 + FastAPI + SQLAlchemy |
| DB | Supabase (Postgres cloud) |
| Scheduler | GitHub Actions (cron) |
| Data primaria | EODHD o FMP Starter (da decidere) |
| Data secondarie | yfinance, ccxt, FRED, RSS |
| AI | Anthropic SDK (Claude Sonnet 4.7 + Haiku 4.5) |
| Frontend | React 18 + Vite + Tailwind + shadcn/ui |
| Charts | TradingView Lightweight Charts |

## Struttura repo

```
personal-bloomberg/
├── backend/
│   ├── app/
│   │   ├── api/             # endpoint FastAPI (Fase 2+)
│   │   ├── ingestion/       # script ingestione (prices, fundamentals, news)
│   │   ├── analytics/       # indicatori, screener (Fase 2)
│   │   ├── baselines/       # 3 strategie passive (Fase 0.5)
│   │   ├── paper_trading/   # signal logger + P&L
│   │   ├── ai/              # client Claude (Fase 4)
│   │   ├── models/          # SQLAlchemy models
│   │   └── core/            # config, db
│   ├── tests/
│   ├── universe/            # CSV liste ticker master
│   └── requirements.txt
├── frontend/                # React app (Fase 3)
├── db/
│   └── schema.sql           # schema Supabase
├── .github/workflows/       # GitHub Actions
├── docs/
│   ├── PLAN.md
│   ├── SCHEMA.md
│   └── DECISIONS.md
└── scripts/                 # one-off (backfill, migrations)
```

## Quick start (Fase 0 — setup locale)

```bash
# 1. Clone o entra in cartella
cd personal-bloomberg/backend

# 2. Crea virtual env
python -m venv .venv
.venv\Scripts\activate           # Windows PowerShell
# source .venv/Scripts/activate  # Git Bash

# 3. Installa dipendenze
pip install -r requirements.txt

# 4. Copia .env.example in .env e compila
cp .env.example .env
# Apri .env e metti SUPABASE_URL + SUPABASE_KEY (vedi docs/SCHEMA.md)

# 5. Test ingestione 20 ticker (yfinance, no API key)
python -m app.ingestion.test_ingest
```

## Roadmap

Vedi [docs/PLAN.md](docs/PLAN.md) sezione 11 per le 8 fasi.

- [x] Fase 0 — Setup repo + struttura cartelle + schema DB + CSV universe
- [ ] Fase 0.5 — Backfill 10y + 3 baseline passive in paper
- [ ] Fase 1 — Data layer completo (fondamentali + news + macro)
- [ ] Fase 2 — Analytics + Paper Trading engine + 2 screener custom
- [ ] Fase 3 — Frontend React MVP + pagina Track Record
- [ ] Fase 4 — AI layer (briefing, Q&A, commentary)
- [ ] Fase 5 — Backtester + alert + deploy
- [ ] Fase 6 — Espansione universo a 2000+ ticker
