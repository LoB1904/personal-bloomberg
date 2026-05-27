# Personal Bloomberg — Project Plan

> Personal financial intelligence platform: real-time market data, fundamental and technical analysis, AI-powered insights — costruito interamente con strumenti free/open-source. **Track record paper trading vs benchmark al centro del progetto. Baseline passive come muro di verità.**

**Owner:** Lorenzo
**Started:** Maggio 2026
**Status:** Pre-Fase 0 (design phase completata, plan v3 con baseline passive e doppio benchmark)
**Repository:** `personal-bloomberg` (da creare)
**Versione:** v3

---

## 1. Vision

Costruire uno strumento personale tipo Bloomberg Terminal, che funzioni come **laboratorio multi-strategy con track record misurato contro baseline passive serie**: nessuna strategia preconfezionata, ma un sistema modulare dove ogni stile di investimento è un modulo che si attiva, si combina, e soprattutto si misura in paper trading forward contro **benchmark multipli e contro strategie passive replicabili a basso costo**.

Il sistema opera su **due orizzonti temporali**, accessibili tramite toggle nella stessa app:

- **Modalità Speculative** — trading speculativo a orizzonte mensile su universo settoriale/ETF
- **Modalità Wealth** — investimento di medio periodo a 5 anni su single name quality

Ogni segnale generato dagli screener entra automaticamente nel sistema di paper trading. Dopo 6 mesi di funzionamento avrò una risposta empirica e onesta a queste domande:

1. Il mio sistema batte un ETF MSCI World comprato a occhi chiusi?
2. Il mio sistema batte un equal-weight S&P 100 ribilanciato trimestralmente?
3. Il mio sistema batte la Dual Momentum di Antonacci (3 righe di codice)?
4. Per la sleeve Wealth: batte MSCI World Quality (ETF factor-specific)?

Se la risposta alle prime 3 è no, la conclusione è chiara: compro le baseline e basta. Questa conclusione, se misurata onestamente, **è un successo del progetto**, non un fallimento.

## 2. Filosofia di lavoro

### Misurazione prima di tutto, contro più benchmark
Il valore del progetto si gioca su una metrica: **performance paper vs benchmark multipli, tracciata onestamente**. Non un solo benchmark generico (MSCI World) ma una batteria di baseline che escludono i falsi alpha da semplice factor tilt.

### Screening, non watchlist
Il sistema non monitora 30 titoli pre-selezionati. Scansiona un universo ampio e ogni mattina produce una **shortlist dinamica** dei titoli più appetibili secondo i criteri attivi. È così che lavorano i fondi multi-strategy.

### Due universi sovrapposti
- **Universo investibile** — aggiornato 1 volta a settimana con prezzi storici e fondamentali
- **Universo focus (~30-80 titoli)** — generato giornalmente dallo screener, è qui che si concentra l'analisi profonda (news, briefing AI, report)

### Due stili per iniziare, scelti per robustezza statistica

Per i primi 6 mesi il sistema implementa **due stili soli**, scelti perché complementari e perché ho framework e letteratura solidi:

1. **Quality + GARP a 5Y** (modalità Wealth, single-name) — riusa la skill GROWTH COMPASS, framework già rodato. Benchmark contro MSCI World Quality (IWQU) per isolare lo stock picking skill dal factor tilt.

2. **Trend-Following su sector ETF a 1-3 mesi** (modalità Speculative) — sostituisce il Momentum single-name del piano v2. Motivazioni:
   - Universo ridotto in Fase 0 (~150 ticker) rende il momentum single-name long-only matematicamente debole (top 10% = 15 titoli, di cui forse 5-7 veri "momentum" mensilmente)
   - Bid-ask spread e turnover costs su small/mid-cap divorano alpha teorico, invisibili in paper
   - I sector ETF (SPDR Select Sector, iShares STOXX Europe sector) hanno:
     - Spread minimi (sono ETF liquidi)
     - Drawdown più gestibili (diversificazione interna)
     - Segnali più puliti, meno rumore idiosincratico
     - Letteratura solida: Faber 2007 *"A Quantitative Approach to Tactical Asset Allocation"*, Hurst-Ooi-Pedersen AQR 2017 *"A Century of Evidence on Trend-Following Investing"*

Gli altri stili (mean reversion, breakout, deep value, dividend growth, megatrend) si aggiungono **solo dopo** che questi due hanno almeno 3 mesi di paper track record documentato.

### Baseline passive prima degli screener custom
Prima di costruire qualsiasi screener attivo, il sistema implementa **tre strategie passive di riferimento** che girano in paper trading dal Day 1 (Fase 0.5). Sono il muro contro cui sbatterà ogni strategia attiva. Dettagli in sezione 7.

### Budget realistico, non zero a tutti i costi
Infrastruttura il più possibile gratuita, ma **se 15-20€/mese su una fonte dati seria fanno la differenza tra "sistema affidabile" e "scraper che si rompe ogni 3 mesi", quei soldi si spendono**. Lo strumento serve a prendere decisioni su soldi miei: l'avarizia sulle fonti dati è falsa economia.

### Realismo sul "real-time"
A budget contenuto non esiste tick-by-tick sulle azioni. Si lavora su **dati daily**, con scheduler che gira dopo la chiusura di Wall Street. Per swing trading mensile e investimento 5Y è non solo sufficiente: è superiore (riduce il rumore intraday).

## 3. Architettura a 5 layer

```
┌─────────────────────────────────────────────────────┐
│  Layer 5 — Frontend React (Scalable-like)           │
│  Speculative | Wealth | Track Record | AI bar       │
├─────────────────────────────────────────────────────┤
│  Layer 4 — AI (Claude API)                          │
│  Briefing, sentiment, Q&A, report, commentary perf  │
├─────────────────────────────────────────────────────┤
│  Layer 3 — Analytics + Paper Trading + Baselines    │
│  Indicatori, scoring, screener, baselines, P&L      │
├─────────────────────────────────────────────────────┤
│  Layer 2 — Storage (Supabase / Postgres)            │
│  Serie storiche, fondamentali, news, signals, bench │
├─────────────────────────────────────────────────────┤
│  Layer 1 — Data ingestion (Python + GitHub Actions) │
│  EODHD/FMP, yfinance, FRED, RSS, ccxt               │
└─────────────────────────────────────────────────────┘
```

Architettura **stratificata e disaccoppiata**: ogni layer può essere modificato senza riscrivere gli altri.

## 4. Stack tecnico definitivo

| Componente | Tecnologia | Costo |
|---|---|---|
| **Backend** | Python 3.11 + FastAPI + SQLAlchemy | 0€ |
| **Scheduler** | GitHub Actions (cron gratuito e affidabile) | 0€ |
| **Database** | Supabase (Postgres cloud, free tier 500MB) | 0€ |
| **Fonte dati primaria** | EODHD ($19.99/mese) **oppure** FMP Starter ($14/mese) | ~15-20€/mese |
| **Data libraries secondarie** | yfinance, ccxt, pandas-ta, feedparser, fredapi | 0€ |
| **AI layer** | Anthropic SDK (Claude Sonnet 4.7 + Haiku 4.5) | ~5-15€/mese a consumo |
| **Frontend** | React 18 + Vite + Tailwind CSS + shadcn/ui | 0€ |
| **Charts** | TradingView Lightweight Charts | 0€ |
| **Dev env** | VS Code + Claude Code + GitHub | 0€ |
| **Deploy** | Vercel (frontend) + Railway/Render (backend) | 0€ free tier |

**Totale infrastruttura: ~20-35€/mese** (fonte dati + AI usage).

> **Nota su scheduler**: APScheduler in container Render free tier va in sleep dopo 15min di inattività → job notturni inaffidabili. **GitHub Actions schedulato** è la scelta corretta per i job di ingestione daily. FastAPI resta solo per servire query in tempo reale al frontend.

## 5. Data sources

| Fonte | Cosa fornisce | Note |
|---|---|---|
| **EODHD / FMP** *(primaria, paid)* | Fondamentali globali, prezzi EOD, earnings | Workhorse. Coverage seria su EU e EM. |
| **yfinance** *(secondaria)* | Prezzi US large cap, dati rapidi di test | Backup/test, non fonte primaria |
| **ccxt + Binance API** | Crypto real-time multi-exchange | Real-time vero (no delay) |
| **Frankfurter / ExchangeRate-API** | FX rates (BCE source) | Aggiornamento giornaliero |
| **FRED API** | Macro USA (tassi, inflazione, employment) | Federal Reserve, illimitato |
| **Eurostat + ISTAT** | Macro Europa e Italia | Open data |
| **RSS feeds** | News: Reuters, Bloomberg, FT, MarketWatch, Sole24Ore, Investing | Illimitato — solo per briefing, mai per signal |

**Limitazioni note e accettate:**
- No earnings call transcripts real-time (workaround: leggere press release post-earnings con AI)
- Sentiment da RSS è rumoroso: usato solo nel briefing AI, mai come segnale di trading
- Coverage US ottima, EU buona con EODHD/FMP, EM variabile su small cap

## 6. Universo investibile

### Fase 0-3: ridotto e gestibile (~150 ticker + baseline)

**Universo per screener custom (~150 titoli):**
- 🇺🇸 S&P 100 (~100)
- 🇮🇹 FTSE MIB (~40)
- 🌍 Wildcard personali (~30 ticker mix EU + EM + crypto top 5)

**Universo per sleeve Speculative — sector ETF (~25 ETF):**
- 🇺🇸 SPDR Select Sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLRE, XLC) — 11 ETF
- 🇪🇺 iShares STOXX Europe sector ETFs (top 8 settori per AUM)
- 🌐 Asset class ETF (TLT bond lunghi USA, GLD oro, DBC commodity broad, EEM emerging markets) — 4-6 ETF

**Universo per baseline passive (vedi sezione 7):**
- VWCE (o IWDA come proxy pre-2019), SPY, EFA, SHY, IWQU (MSCI World Quality)

**Perché ridotto all'inizio**: la pipeline end-to-end (ingestione → screener → paper signal → frontend → AI briefing) deve funzionare su un sottoinsieme prima di scalare. Si scala dopo, in Fase 6.

### Fase 6+: espansione (~2000-2500 ticker)
| Mercato | Coverage | # titoli stimati |
|---|---|---|
| 🇺🇸 USA | S&P 500 + S&P 400 MidCap | ~900 |
| 🇪🇺 Europa | STOXX 600 | ~600 |
| 🇮🇹 Italia | FTSE MIB + Mid Cap | ~50 |
| 🇯🇵 Giappone | Nikkei 225 | ~225 |
| 🌏 Emerging | Hang Seng + EM top liquidity | ~200 |
| 📊 ETF | Top per AUM | ~50 |
| 🪙 Crypto | Top 30 per market cap | ~30 |
| 💱 FX + Commodities | Major pairs + commodities futures | ~15 |

## 7. Baseline passive (Fase 0.5 — NUOVA)

### Obiettivo
Costruire **tre strategie passive di riferimento** che girano in paper trading dal Day 1, *prima* di qualsiasi screener custom. Sono il muro di verità.

### Baseline #1 — Buy & Hold MSCI ACWI (il benchmark vero)
- **Cosa fa**: compri VWCE giorno 1, mai modificato, dividendi reinvestiti
- **Ticker**: `VWCE.DE` (Xetra), fallback `IWDA.AS` per storico pre-2019
- **Perché**: rappresenta la soluzione passiva di default — "cosa avrei ottenuto non facendo niente"
- **Tracciamento**: serie storica in `benchmark_prices` + posizione paper in `paper_strategy_daily`

### Baseline #2 — Equal-Weight S&P 100, ribilancio trimestrale
- **Cosa fa**: 100k paper diviso equamente sui 100 titoli S&P 100. Ribilancio ultimo venerdì di mar/giu/set/dic
- **Universo**: i 100 ticker S&P 100 già in DB per gli screener custom
- **Perché**: rimuove la distorsione cap-weighted dominata da Mag7. Plyakha-Uppal-Vilkov 2014: equal-weight ha sovraperformato cap-weighted di 1.5-2% annualizzato storicamente
- **Costi simulati**: 0.10% per trade a ribilanciamento (realistico IBKR)
- **Tracciamento**: `screener_profile = 'baseline_ew_sp100'` in `paper_signals`

### Baseline #3 — Dual Momentum di Antonacci (la baseline "smart")
- **Cosa fa**: ogni fine mese:
  1. Calcola rendimento 12m di SPY, EFA, SHY (cash proxy)
  2. Se `return(SPY, 12m) > return(SHY, 12m)`: scegli tra SPY ed EFA quello con 12m return più alto, vai 100%
  3. Altrimenti: vai 100% SHY (cash)
- **Universo**: 3 ticker (SPY, EFA, SHY)
- **Perché**: Antonacci 2014 backtest 1974-2013 → CAGR 17.4% vs 11.4% S&P 500, MaxDD -17% vs -51%. **Tre righe di codice**. Se il sistema custom non batte questa, ha perso
- **Costi simulati**: 0.05% per switch (1 trade/mese max)
- **Tracciamento**: `screener_profile = 'baseline_dual_momentum'`

### Doppio benchmark per la sleeve Wealth
La sleeve Quality+GARP si misura contro **due benchmark**, non uno solo:
1. **MSCI World (VWCE / IWDA)** → benchmark di asset allocation: ti dice se hai battuto la soluzione passiva
2. **MSCI World Quality (IWQU.L)** → benchmark fattoriale: ti dice se hai **stock picking skill**, non solo factor exposure

Se la sleeve Wealth batte MSCI World ma non MSCI World Quality, l'alpha è solo factor tilt — replicabile a 0.30% TER con un ETF, nessuno paga un sistema custom per fare quello.

### Cosa misurare per ogni baseline e ogni futuro screener
Su ogni strategia (baseline + screener custom) si calcola e logga giornalmente:

| Metrica | Cosa significa |
|---|---|
| **Total return %** | Performance cumulata |
| **CAGR** | Performance annualizzata (da 90 giorni in poi) |
| **Volatility (ann.)** | Std dev daily × √252 |
| **Sharpe ratio** | (CAGR - risk_free) / vol. Risk free = Euribor 3m |
| **Max drawdown** | Peggior caduta peak-to-trough |
| **Calmar ratio** | CAGR / \|MaxDD\|. Importante per drawdown asimmetrici |
| **Win rate mensile** | % mesi positivi |
| **Alpha vs benchmark** | Excess return vs benchmark di riferimento |

## 8. Layout e design

**Riferimento visivo:** Scalable Capital
- Dark mode di default, eleganza europea
- Tipografia respirata (Inter)
- Accenti verde/rosso sui dati di performance
- Minimalismo informativo (no fronzoli, no glow, no neon)
- Densità calibrata

**Struttura UI:**
- Sidebar nera sinistra con navigation
- Toggle Speculative/Wealth in alto
- AI command bar sempre presente
- Dashboard centrale: shortlist del giorno + heatmap + grafici
- **Pagina "Track Record" obbligatoria**: performance live di tutte le strategie (baseline + custom) vs benchmark, con tabella metriche e curve sovrapposte
- Pagina dettaglio titolo: chart TradingView + fondamentali + segnali + news

## 9. Le due modalità

### 🎯 Speculative (mensile) — Trend-Following su sector ETF
- Shortlist tattica generata da screener trend-following su universo ~25 sector ETF
- Regole base v1: long su ETF se `price > MA(200)` AND `return(3m) > 0`, dimensionamento equal-weight tra ETF in trend
- Grafici TradingView protagonisti, indicatori sovrapponibili
- Tabella segnali del giorno: trend score, breakout, eventi
- Heatmap settoriale e geografica
- Backtester (Fase 5) per validare strategie storicamente
- Alert quando un ETF entra/esce dal trend
- **Track record live: ogni segnale paper-traded, P&L vs S&P 500 + Dual Momentum visibile in dashboard**

### 📈 Wealth (5 anni) — Quality + GARP single-name
- Shortlist accumulo generata da screener quality + GARP su universo single-name (~150 titoli)
- Scoring fondamentale alla GROWTH COMPASS (riuso skill esistente)
- Vista qualitativa: moat, management, megatrend, ESG
- Confronto vs peer settoriale
- Calendario earnings + dividendi
- Allocazione per asset class, geografia, settore, fattore
- **Track record live: paper-traded, doppio benchmark MSCI World + MSCI World Quality**

## 10. AI Layer (Claude)

Funzionalità attivate:
- ✅ **Briefing mattutino** automatico sui candidati della shortlist (Haiku per velocità)
- ✅ **Analisi earnings** — sentiment + key takeaways da press release
- ✅ **Q&A su dati portfolio** — via function calling su set di 10-15 funzioni Python predefinite (più sicuro e debuggabile del text-to-SQL puro)
- ✅ **Report GROWTH COMPASS** automatici on-demand (Sonnet per profondità)
- ✅ **Alert intelligenti** — non solo "RSI<30" ma "zona di acquisto multifattore"
- ✅ **Commentary settimanale sulle performance paper** (Haiku): "questa settimana lo screener X ha generato N segnali, M positivi, alpha vs benchmark Y%, vs MSCI World Quality Z%, driver principali..."

**Filosofia AI:** Claude non analizza l'intero universo. Solo i top 20-30 dalla shortlist quotidiana. Massimo valore, costi controllati.

**Sicurezza Q&A:** invece di text-to-SQL libero su Supabase, espongo un set ristretto di funzioni Python via function calling. Claude sceglie funzione + parametri, il backend esegue. Più sicuro, più prevedibile, auditable.

## 11. Roadmap riequilibrata in 8 fasi

| Fase | Cosa | Sessioni | Deliverable |
|---|---|---|---|
| **Fase 0** | Setup repo, Supabase, schema DB completo (incluse `paper_signals`, `benchmark_prices`, `paper_strategy_daily`), universo ~150 ticker + 25 ETF + 5 baseline, GitHub Actions primo workflow ingestione | 3 | DB popolato, query SQL funzionanti |
| **Fase 0.5** | **Baseline passive**: backfill 10y storico VWCE/SPY/EFA/SHY/IWQU, implementazione 3 strategie baseline (B&H VWCE, EW S&P 100, Dual Momentum), forward tracking live via GitHub Actions, primi numeri di riferimento storici | 2 | 3 baseline strategies girano in paper, metriche storiche disponibili |
| **Fase 1** | Data layer completo: fondamentali (EODHD/FMP), news RSS, macro FRED. Backfill 5y storico su universo single-name | 3-4 | DB con fondamentali + news fresche ogni giorno |
| **Fase 2** | **Analytics + Paper Trading engine custom**. Due screener: Trend-Following sector ETF (Speculative) + Quality/GARP single-name (Wealth). Ogni segnale → `paper_signals`, P&L vs benchmark multipli | 5-6 | API restituisce shortlist + track record live, screener custom in paper accanto a baseline |
| **Fase 3** | Frontend MVP Scalable-style: dashboard + dettaglio titolo + **pagina Track Record** (baseline + custom sovrapposte) | 5-6 | Web app navigabile in localhost |
| **Fase 4** | AI layer integrato: briefing, Q&A via function calling, report automatici, commentary settimanale performance | 3-4 | Briefing mattutino + AI bar funzionante |
| **Fase 5** | Backtester storico (validazione retrospettiva degli screener), alert engine, polish, deploy Vercel/Railway | 4-5 | Web app accessibile da browser |
| **Fase 6** | Espansione universo da 150 a 1500-2500 ticker | 2-3 | Sistema scalato |

**Tempo totale realistico: 26-30 settimane** (1.5-2h ogni 2-3 giorni, considerando full-time, viaggi NYC/Indonesia/Cambogia, altre attività).

> **Backtester arriva in Fase 5, NON in Fase 2.** Il backtester mente sempre un po' (look-ahead bias, survivorship, overfitting). Il paper trading forward è verità. Prima la verità, poi la simulazione storica come strumento secondario di validazione.

> **Fase 0.5 è strategicamente critica.** A fine Fase 0.5 hai già 1-2 mesi di paper forward sulle baseline, quindi quando in Fase 2 attivi gli screener custom, il confronto è onesto dal primo giorno: confronti strategie attive contro passive tracciate in parallelo, non contro un benchmark "appiccicato dopo".

## 12. Struttura del repository

```
personal-bloomberg/
├── backend/
│   ├── app/
│   │   ├── api/              # endpoint FastAPI
│   │   ├── ingestion/        # script ingestione dati
│   │   │   ├── prices.py     # EODHD/FMP, ccxt
│   │   │   ├── fundamentals.py
│   │   │   ├── news.py       # RSS aggregator
│   │   │   ├── macro.py      # FRED, Eurostat
│   │   │   └── benchmarks.py # S&P 500, MSCI World, MSCI World Quality, sector ETF
│   │   ├── analytics/        # indicatori, scoring, screener
│   │   │   ├── technical.py  # RSI, MACD, MA, Bollinger, ATR
│   │   │   ├── fundamental.py # quality_score, garp_score
│   │   │   ├── trend.py      # trend-following su ETF (NUOVO)
│   │   │   ├── screener.py   # IL CUORE
│   │   │   └── backtest.py   # Fase 5
│   │   ├── baselines/        # BASELINE PASSIVE (NUOVO — Fase 0.5)
│   │   │   ├── buy_hold.py   # B&H VWCE
│   │   │   ├── equal_weight.py # EW S&P 100 ribilanciato
│   │   │   └── dual_momentum.py # Antonacci
│   │   ├── paper_trading/    # PAPER TRADING ENGINE
│   │   │   ├── signal_logger.py
│   │   │   ├── pnl_calculator.py
│   │   │   └── benchmark_compare.py # alpha vs MSCI World, MSCI World Quality, baseline
│   │   ├── ai/               # client Claude, prompt templates
│   │   │   ├── briefing.py
│   │   │   ├── reports.py
│   │   │   ├── qa.py         # function calling
│   │   │   └── performance_commentary.py
│   │   ├── models/           # SQLAlchemy models
│   │   └── core/             # config, db
│   ├── tests/
│   ├── universe/             # CSV liste ticker master
│   │   ├── sp100.csv
│   │   ├── ftsemib.csv
│   │   ├── wildcards.csv
│   │   ├── sector_etfs.csv   # NUOVO
│   │   └── baselines.csv     # NUOVO
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Ticker.tsx
│   │   │   ├── Screener.tsx
│   │   │   ├── TrackRecord.tsx   # baseline + custom sovrapposte
│   │   │   └── Portfolio.tsx
│   │   ├── components/
│   │   │   ├── Chart.tsx
│   │   │   ├── Watchlist.tsx
│   │   │   ├── AIBar.tsx
│   │   │   ├── HeatMap.tsx
│   │   │   ├── PerformanceChart.tsx  # multi-strategy comparison
│   │   │   └── BaselinePanel.tsx     # NUOVO — riassunto live 3 baseline
│   │   ├── lib/              # api client, hooks
│   │   └── styles/
│   └── package.json
├── .github/workflows/        # GitHub Actions
│   ├── ingest_daily.yml
│   ├── compute_signals.yml
│   ├── update_baselines.yml  # NUOVO — Fase 0.5
│   └── update_pnl.yml
├── scripts/                  # one-off (backfill iniziale)
├── docs/
│   ├── PLAN.md               # questo file
│   ├── SCHEMA.md             # documentazione schema DB
│   ├── DECISIONS.md          # ADR — architectural decision records
│   └── PROMPTS.md            # prompt templates per Claude
└── README.md
```

## 13. Schema database (high-level)

Tabelle principali:

- **ticker_universe** — anagrafica titoli (symbol, name, exchange, country, sector, industry, asset_class, is_active)
- **prices_daily** — serie storiche (ticker_id, date, open, high, low, close, volume, adj_close)
- **fundamentals_snapshot** — fondamentali (ticker_id, snapshot_date, pe, pb, roe, roic, debt_to_equity, fcf, revenue_growth, ...)
- **news_items** — news (id, ticker_id, source, title, summary, published_at, sentiment_score)
- **macro_indicators** — serie macro (indicator_code, date, value)
- **screener_results** — shortlist storiche (date, profile, ticker_id, scores_json, rank)
- **paper_signals** *(core)* — signal_id, ticker_id, screener_profile, generated_at, entry_price, exit_price_1w/1m/3m/6m/12m, status, pnl_pct_*
- **benchmark_prices** *(core)* — benchmark_code (VWCE, IWQU, SPX, MSCI_WORLD, FTSEMIB), date, close, total_return_index, source
- **paper_strategy_daily** *(NUOVO — Fase 0.5)* — strategy_code, date, portfolio_value, cash_pct, num_positions, daily_return_pct, total_return_pct, drawdown_pct
- **alerts** — alert configurati
- **portfolio** — posizioni reali/paper personali

## 14. Decisioni chiave già prese

| Decisione | Scelta | Motivazione |
|---|---|---|
| Mercati | Globale (USA + EU + IT + EM + crypto + FX + commodities) — scalato in Fase 6 | Diversificazione massima, pipeline prima va resa solida |
| Universo iniziale | ~150 single-name + 25 sector ETF + 5 baseline tickers | Pipeline end-to-end prima di scalare |
| **Baseline passive prima degli screener** | **Fase 0.5 dedicata: B&H VWCE, EW S&P 100, Dual Momentum Antonacci** | **Muro di verità contro cui misurare ogni futuro screener. Risparmia mesi di illusioni** |
| **Sleeve Speculative** | **Trend-Following su sector ETF, NON Momentum single-name** | Universo ridotto rende momentum single-name statisticamente debole; sector ETF hanno meno rumore, costi ridotti, letteratura solida (Faber 2007, AQR 2017) |
| **Doppio benchmark Wealth** | **MSCI World + MSCI World Quality (IWQU)** | Distingue alpha da stock picking vs alpha da factor tilt (quest'ultimo replicabile con ETF a 0.30% TER) |
| Stili supportati v1 | Solo 2: Trend-Following ETF + Quality/GARP single-name | Altri stili solo dopo 3 mesi di track record. No tuning senza evidenza |
| Paper trading automatico | Core feature: ogni segnale loggato e tracciato vs benchmark multipli | Unico modo per sapere se il sistema funziona |
| Budget | ~20-35€/mese (fonte dati + AI), non 0€ | Affidabilità > vincolo "free at all costs" |
| Fonte dati primaria | EODHD o FMP Starter (paid), yfinance secondaria | yfinance scraper non ufficiale, qualità bassa su small/mid cap EU/EM |
| Hosting | Web app cloud (Vercel + Railway) | Accesso da ovunque, mobile included |
| Scheduler | GitHub Actions (NON APScheduler in container free tier) | Free tier va in sleep, job notturni inaffidabili |
| Build vs buy | Tutto custom Python/React | Massimo controllo, valore formativo |
| AI Q&A | Function calling su funzioni predefinite, NON text-to-SQL libero | Più sicuro, prevedibile, auditable |
| Database | Supabase (cloud da subito) | Zero setup, accessibile dal deploy futuro |
| Dev environment | Claude Code in VS Code | Già setup, workflow conosciuto |
| Riferimento UI | Scalable Capital | Dark mode elegante, europeo, leggibile |

## 15. Punti aperti / da decidere strada facendo

- [ ] Scelta finale EODHD vs FMP Starter (testare gratuitamente prima di pagare)
- [ ] Lista esatta dei wildcard ticker e dei sector ETF EU (CSV da costruire in Fase 0)
- [ ] Soglie default screener Trend-Following (es. MA200 vs MA150, lookback momentum 3m vs 6m) — calibrazione empirica in Fase 2
- [ ] Pesi degli score nella shortlist (parametrizzabili in UI)
- [ ] Integrazione broker reale (Interactive Brokers API gratis) — solo dopo 6+ mesi di paper track record positivo vs **tutte e 3 le baseline**
- [ ] Trading reale capitale piccolo — solo dopo 12+ mesi di paper track record positivo vs baseline
- [ ] Modello ML predittivo opzionale — Fase 7+

## 16. Come ripartire (prima sessione Claude Code)

1. Aprire VS Code in cartella `personal-bloomberg/` (vuota)
2. Lanciare Claude Code dal terminale integrato (`claude`)
3. Trascinare questo `PLAN.md` nel contesto della sessione
4. Dire: *"Leggi PLAN.md v3 con attenzione. Iniziamo Fase 0: setup repo (struttura cartelle come sezione 12), account Supabase, schema DB iniziale completo (incluse paper_signals, benchmark_prices, paper_strategy_daily), CSV universe per S&P 100 + FTSE MIB + wildcards + sector ETFs + baselines, primo script di ingestione daily su 20 ticker di test via GitHub Actions. Procedi step by step chiedendomi conferma sui passi che richiedono account o decisioni esterne."*
5. Claude Code crea struttura, file iniziali, guida creazione account Supabase, configura primo workflow GitHub Actions

## 17. Criteri di successo a 12 mesi

Tre obiettivi paralleli, tutti misurabili. A 12 mesi dal kickoff (target: maggio 2027) il progetto è un successo se raggiunge **almeno 2 su 3** di:

### (a) Performance onestamente misurata — il sistema sa cosa fa
- Almeno **6 mesi continui di paper trading live** documentati su entrambi gli screener custom
- Performance vs **3 baseline + 2 benchmark fattoriali** calcolate e visibili
- Metriche di sistema documentate: hit rate, Sharpe ratio paper, max drawdown, Calmar, win/loss ratio, alpha vs ogni baseline
- **Soglia minima accettabile**: non sottoperformare la *migliore* tra le 3 baseline di più del 3% annualizzato. Se sottoperformo di più, la conclusione è chiara: compro la migliore baseline e basta. **Questa conclusione è un successo se misurata onestamente**

### (b) Competenza — ho imparato lo stack
- Padronanza dimostrabile di: FastAPI, React + TypeScript, SQLAlchemy + Postgres, Claude API integration (function calling, MCP), GitHub Actions
- Capacità di estendere il sistema autonomamente (aggiungere un nuovo screener in <1 settimana)
- Codice pubblicabile su GitHub con qualità ragionevole (tests, README, docstring sui moduli core)

### (c) Posizionamento — materiale narrativo AI-native BA
- Repository pubblico documentato (README serio, screenshots, architettura)
- **Almeno un articolo lungo** (LinkedIn long-form o blog) che racconta il progetto: cosa ho costruito, cosa ho imparato sui limiti dei dati free/cheap, performance onestamente misurate **contro baseline serie**
- Demo video di 3 minuti
- Materiale concreto da portare in colloqui, conversazioni con manager, candidature

**Nota di realismo sull'esito (a):** statisticamente è probabile che il sistema non batta tutte e 3 le baseline contemporaneamente — il 70-80% dei gestori attivi professionali non ci riesce, e io userò dati cheap su infrastruttura costruita nel tempo libero. Un esito tipo *"il sistema custom batte B&H VWCE di 1% annualizzato ma sottoperforma Dual Momentum di 2%"* è una conclusione robusta, materiale di altissimo valore per (c), e mi salva dal mettere soldi reali su un sistema che non funziona.

**L'onestà metrica — supportata da baseline multiple e benchmark fattoriali — è la cosa che differenzia questo progetto da un giocattolo.**

---

**Documento vivo.** Aggiornare ad ogni fase completata e ad ogni decisione architetturale importante (in tal caso creare anche un ADR in `docs/DECISIONS.md`).

**Changelog:**
- **v1** — Piano originale, 7 stili, single benchmark
- **v2** — Riequilibrato: paper trading al centro, 2 stili soli (Momentum + Quality), universo ridotto, budget realistico
- **v3** — Aggiunta Fase 0.5 baseline passive (B&H VWCE + EW S&P 100 + Dual Momentum Antonacci); sleeve Speculative cambiata da Momentum single-name a Trend-Following su sector ETF; doppio benchmark per sleeve Wealth (MSCI World + MSCI World Quality); criteri di successo riformulati con baseline come soglia di riferimento
