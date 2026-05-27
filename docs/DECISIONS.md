# Architectural Decision Records — Personal Bloomberg

> ADR (Architecture Decision Record) = una riga per ogni decisione strutturale presa, con contesto, alternative valutate e razionale. Quando in futuro mi chiederò "perché abbiamo scelto X invece di Y", la risposta sta qui.

## Format

Ogni ADR ha questa struttura:

```
## ADR-NNN — Titolo breve
**Data:** YYYY-MM-DD
**Status:** Proposed | Accepted | Superseded by ADR-MMM
**Contesto:** cosa stavamo cercando di risolvere
**Decisione:** cosa abbiamo scelto
**Alternative valutate:** cosa abbiamo scartato e perché
**Conseguenze:** cosa cambia per chi userà o estenderà il sistema
```

---

## ADR-001 — Postgres su Supabase come storage unico
**Data:** 2026-05-13
**Status:** Accepted

**Contesto:** servono serie storiche prezzi, fondamentali, news, segnali paper, NAV strategie. Anche frontend potrebbe leggere lato client.

**Decisione:** singolo Postgres cloud su Supabase free tier (500MB). SQLAlchemy lato Python per i job batch, REST API Supabase lato frontend (read-only via RLS).

**Alternative valutate:**
- SQLite locale → niente cloud, niente accesso da deploy futuro. Scartato.
- TimescaleDB → ottimo per serie storiche, ma overkill per ~50k righe prezzi. Scartato in Fase 0, rivalutabile in Fase 6 con universo ~2000 ticker.
- DynamoDB / Mongo → no relazioni, mismatch col modello dati relazionale del progetto.
- Postgres self-hosted su Railway → costo aggiuntivo, e Supabase free tier basta per i primi 12 mesi.

**Conseguenze:** dipendenza da Supabase per disponibilità DB. In caso si saturi il free tier (500MB), upgrade a Pro ($25/mese) oppure migrazione a Postgres self-hosted. Dato il modello d'uso, saturazione probabile solo dopo Fase 6.

---

## ADR-002 — GitHub Actions come scheduler (NON APScheduler in container)
**Data:** 2026-05-13
**Status:** Accepted

**Contesto:** servono job ricorrenti: ingestione prezzi EOD, ricalcolo screener, update P&L paper, fetch news.

**Decisione:** GitHub Actions con cron. Workflow per ogni famiglia di job (`ingest_daily.yml`, `compute_signals.yml`, `update_baselines.yml`, `update_pnl.yml`).

**Alternative valutate:**
- APScheduler in container Render/Railway free tier → il container va in sleep dopo 15min di inattività, job notturni inaffidabili. Scartato.
- Cron locale su PC → richiede PC sempre acceso, niente storico esecuzioni, no log centralizzati. Scartato.
- AWS EventBridge / Lambda → overkill, lock-in AWS, complessità setup. Scartato.

**Conseguenze:** dipendenza da GitHub Actions (free tier: 2000 minuti/mese per repo privati, illimitato per repo pubblici). I segreti API vivono in GitHub Actions Secrets. Logs disponibili 90 giorni.

---

## ADR-003 — yfinance solo come fonte di test, NON primaria
**Data:** 2026-05-13
**Status:** Accepted

**Contesto:** yfinance scraper non ufficiale, qualità bassa su small/mid-cap EU/EM, throttling, occasionali break delle API Yahoo.

**Decisione:** yfinance è OK per Fase 0 (test della pipeline) e per backup. La fonte primaria in Fase 1+ sarà EODHD ($19.99/mese) o FMP Starter ($14/mese), da decidere dopo test side-by-side gratuiti.

**Alternative valutate:**
- Solo yfinance per risparmiare 15-20€/mese → falsa economia, scraper si rompe ogni 3-6 mesi storicamente. Scartato.
- Alpha Vantage free tier → 5 chiamate al minuto, inutilizzabile per 200 ticker. Scartato.
- IEX Cloud → focus US, debole su EU/EM. Scartato.

**Conseguenze:** budget infrastruttura ~20-35€/mese (fonte dati + Claude API a consumo). Decisione finale EODHD vs FMP rimandata a Fase 1, dopo test diretto sui ticker EU/IT che ci interessano.

---

## ADR-004 — Baseline passive in Fase 0.5, prima degli screener custom
**Data:** 2026-05-13
**Status:** Accepted

**Contesto:** rischio di costruire screener attivi senza un termine di paragone serio. Risultato: difficile sapere se il sistema ha skill o se ha avuto fortuna.

**Decisione:** 3 baseline passive (B&H VWCE, Equal-Weight S&P 100 ribilanciato trimestrale, Dual Momentum Antonacci) attivate in Fase 0.5, **prima** di scrivere qualsiasi screener custom. Girano in paper trading dal Day 1. Sono il muro di verità.

**Alternative valutate:**
- Singolo benchmark (es. solo MSCI World) → maschera factor tilt. Se il sistema custom ha solo tilt growth/quality, "batte MSCI World" non significa skill, significa esposizione fattoriale, replicabile a 0.30% TER. Scartato.
- Baseline costruite dopo gli screener → confronto a posteriori sporco, non onesto. Scartato.

**Conseguenze:** Fase 0.5 ritarda di 2-3 settimane l'avvio degli screener custom. Vantaggio: quando arrivano (Fase 2), il confronto è onesto dal Day 1. Inoltre se gli screener custom non battono almeno 2 su 3 baseline, la conclusione "compro la baseline e basta" è documentata e onestamente derivata.

---

## ADR-005 — Sleeve Speculative su sector ETF, NON momentum single-name
**Data:** 2026-05-13
**Status:** Accepted (Supersedes v2 del PLAN)

**Contesto:** v2 del PLAN prevedeva Momentum single-name long-only sull'universo S&P 100 + FTSE MIB. Analisi pre-Fase 0 ha mostrato 3 problemi:

1. Universo ridotto (~150 titoli) rende il top-decile = 15 titoli, di cui forse 5-7 momentum reali mensilmente. Sample size statisticamente debole.
2. Bid-ask spread e turnover costs su small/mid-cap EU/IT divorano alpha teorico, invisibili in paper.
3. Letteratura più solida su Trend-Following ETF (Faber 2007, AQR 2017) che su single-name long-only momentum a universo ridotto.

**Decisione:** sleeve Speculative implementa Trend-Following su universo di ~25 sector ETF (SPDR Select Sector USA + asset class TLT/GLD/DBC/EEM + iShares STOXX Europe sector top 8). Regole base v1: long se `price > MA(200)` AND `return(3m) > 0`, equal-weight tra ETF in trend.

**Alternative valutate:**
- Momentum single-name (piano v2) → vedi problemi sopra. Superseded.
- Dual Momentum solo (Antonacci) → già implementato come baseline. Sleeve speculative deve aggiungere valore vs la baseline più semplice, non duplicarla.

**Conseguenze:** universo dell'analisi tecnica si sdoppia in: ~25 sector ETF (sleeve Speculative) + ~150 single-name (sleeve Wealth). I CSV in `backend/universe/` riflettono questa separazione.

---

## ADR-006 — Doppio benchmark per sleeve Wealth (MSCI World + MSCI World Quality)
**Data:** 2026-05-13
**Status:** Accepted

**Contesto:** la sleeve Wealth è Quality+GARP. Se misurata solo vs MSCI World e battute il benchmark, non sai se hai stock picking skill o solo factor tilt.

**Decisione:** sleeve Wealth tracciata contro DUE benchmark:
1. `VWCE` (MSCI ACWI proxy) — benchmark di asset allocation
2. `IWQU` (MSCI World Quality) — benchmark fattoriale

**Razionale:** se il sistema batte VWCE ma NON IWQU, l'alpha è solo factor tilt — replicabile con un ETF quality a 0.30% TER. Nessuno paga (e nessuno costruisce) un sistema custom per quello. Skill vera = battere IWQU.

**Conseguenze:** ogni segnale Wealth in `paper_signals` viene confrontato a due benchmark (campo `metadata.benchmark_alpha` con entrambi gli alpha). La tabella `paper_strategy_daily` ha `benchmark_code` per il benchmark primario, e nel `metadata` JSONB i benchmark secondari.

---

## ADR-007 — AI Q&A via function calling, NON text-to-SQL libero
**Data:** 2026-05-13
**Status:** Accepted

**Contesto:** in Fase 4 vogliamo poter chiedere a Claude domande sui dati del portfolio in linguaggio naturale.

**Decisione:** espongo a Claude un set di 10-15 funzioni Python predefinite (es. `get_screener_shortlist`, `get_signal_history`, `get_strategy_performance`) via function calling. Claude sceglie funzione + parametri, il backend Python esegue la query SQL parametrizzata e ritorna i dati.

**Alternative valutate:**
- Text-to-SQL libero (LLM genera SQL, backend esegue) → rischio SQL injection, query costose non controllate, errori opachi, difficile auditare. Scartato.
- API REST aperta che Claude chiama via tool use → di fatto equivalente al function calling, ma con overhead HTTP. Scartato per semplicità.

**Conseguenze:** lo spazio delle domande possibili è limitato dalle funzioni esposte. Vantaggio: ogni risposta è auditable, debuggabile, e non c'è rischio di query distruttive. Aggiungere capability = aggiungere una funzione Python al set.

---

## ADR-008 — Backtester in Fase 5, NON in Fase 2
**Data:** 2026-05-13
**Status:** Accepted

**Contesto:** la tentazione naturale è scrivere il backtester storico per primo, validare le strategie su dati passati, poi attivarle in paper.

**Decisione:** il backtester arriva in Fase 5, come strumento secondario. Prima viene il paper trading forward (Fase 0.5 + Fase 2).

**Razionale:** i backtester mentono sempre un po' — look-ahead bias, survivorship bias, overfitting su finestre storiche. Il paper trading forward è verità: con i dati di oggi prendi decisioni di oggi, e domani vedi cosa è successo. Niente bias.

**Conseguenze:** per i primi 6 mesi non sapremo "come avrebbe performato la strategia X negli ultimi 10 anni". Sapremo "come sta performando ORA contro 3 baseline". È un trade-off accettato: onestà metrica > illusione di sapere.

---

## Template ADR (copia e incolla per nuove decisioni)

```
## ADR-NNN — Titolo breve
**Data:** 2026-MM-DD
**Status:** Proposed

**Contesto:**

**Decisione:**

**Alternative valutate:**

**Conseguenze:**
```
