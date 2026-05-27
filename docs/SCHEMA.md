# Schema database — Personal Bloomberg

> Documento esplicativo dello schema Postgres che gira su Supabase. Lo schema vero e versionato è in [`db/schema.sql`](../db/schema.sql). Quando modifichi `schema.sql`, aggiorna anche questo file.

## Filosofia generale

- Tutto in un singolo Postgres su Supabase free tier (500MB). Quando arriviamo a saturare lo upgradiamo o passiamo a Railway/Render.
- **Una tabella = un concetto.** Niente "tabellone unico" che mischia segnali e prezzi e fondamentali. Se ti viene voglia di farlo, fermati.
- **Una riga = un fatto immutabile.** I prezzi del giorno X non si aggiornano: si re-ingeriscono in caso di errore (ON CONFLICT UPDATE).
- **UPSERT come default.** I job di ingestione devono essere idempotenti — se gira due volte non duplica.

## Analogia Excel/Power BI

Se sei abituato a Power BI: pensa allo schema come al **modello dati** di un dataset PBI.

- `ticker_universe` = tabella anagrafica (dimensione) — come "Anagrafica Cliente"
- `prices_daily` = tabella fatti — come "Vendite per Data"
- `fundamentals_snapshot` = altra tabella fatti, più rara (trimestrale) — come "Saldo Cliente Trimestrale"
- `paper_signals` = tabella eventi — come "Operazioni Anticipo Factoring"
- Relazioni tramite `ticker_id` — come `ID Cliente` che lega anagrafica e operazioni

Power BI farebbe `SUMX(Vendite, [Importo])`. Postgres fa `SELECT SUM(close * volume) FROM prices_daily WHERE ticker_id = ...`. Stesso pattern, lingua diversa.

## Le 11 tabelle in breve

| Tabella | Cardinalità stimata a 12 mesi | A cosa serve |
|---|---|---|
| `ticker_universe` | ~200 righe | Anagrafica dei ticker tracciati |
| `prices_daily` | ~50.000 righe | Prezzi EOD giornalieri (200 ticker × 250 giorni) |
| `fundamentals_snapshot` | ~800 righe | Fondamentali (~150 single-name × 4-5 quarter) |
| `news_items` | 30.000-100.000 | News RSS aggregati (filtrare per data) |
| `macro_indicators` | ~5.000 | Tassi, inflazione, employment FRED/Eurostat |
| `screener_results` | ~10.000-50.000 | Shortlist storiche giornaliere |
| `paper_signals` | ~500-2.000 | Ogni segnale generato in paper trading |
| `benchmark_prices` | ~3.000 | Prezzi dei 6-10 benchmark di riferimento |
| `paper_strategy_daily` | ~2.000-5.000 | NAV giornaliero di ogni strategia |
| `alerts` | ~30-100 | Alert configurati |
| `portfolio` | ~30-100 | Posizioni paper/real personali |

Totale stimato: ~120.000 righe. Supabase free tier (500MB) regge tranquillamente.

## Le 3 tabelle critiche (sezione 7 del PLAN)

### `paper_signals` — il cuore del paper trading

Ogni shortlist genera segnali. Ogni segnale entra qui con il `entry_price` del giorno. Poi un job batch (GitHub Actions) popola i `price_1w`, `price_1m`, `price_3m`, `price_6m`, `price_12m` alle milestone, e calcola i relativi `pnl_pct_*`.

**Perché 5 milestone (1w, 1m, 3m, 6m, 12m)?** Per capire su quale orizzonte ogni screener ha skill. Un Trend-Following su ETF dovrebbe lavorare a 1-3 mesi. Un Quality+GARP a 12 mesi+. Misurando a tutte e 5 le milestone vediamo se lo screener effettivamente lavora sull'orizzonte teorico.

**Analogia factoring:** è come tenere uno storico delle pratiche di anticipo: ogni operazione ha data ingresso, prezzo (capitale anticipato), e poi tracci esito (recupero) a 30/60/90/180 giorni. Le statistiche aggregate dicono se il portafoglio sta funzionando.

### `benchmark_prices` — il muro di verità

Serie storica dei prezzi (e total return index) dei benchmark. Separata da `prices_daily` perché alcuni benchmark sono **indici** (es. MSCI World Total Return), non ticker quotati come ETF.

**Codici benchmark in uso:**
- `VWCE` — Vanguard FTSE All-World ETF (proxy MSCI ACWI per B&H baseline)
- `IWDA` — iShares Core MSCI World ETF (fallback per storico pre-2019)
- `SPY` — SPDR S&P 500 (leg US della Dual Momentum)
- `EFA` — iShares MSCI EAFE (leg international)
- `SHY` — iShares 1-3Y Treasuries (cash proxy Dual Momentum)
- `IWQU` — iShares MSCI World Quality (benchmark fattoriale sleeve Wealth)
- `MSCI_WORLD` — indice puro MSCI World (se disponibile via fonte primaria)
- `SPX` — indice S&P 500 puro

### `paper_strategy_daily` — il NAV di ogni strategia

Una riga per strategia per giorno con `portfolio_value` (NAV mark-to-market), `total_return_pct` da inception, `drawdown_pct` vs peak storico, `alpha_pct` vs benchmark di riferimento.

**Strategy code naming convention:**
- `baseline_bh_vwce` — Buy & Hold VWCE
- `baseline_ew_sp100` — Equal-Weight S&P 100, ribilancio trimestrale
- `baseline_dual_momentum` — Dual Momentum Antonacci
- `trend_etf_v1` — Trend-Following su sector ETF (sleeve Speculative)
- `quality_garp_v1` — Quality + GARP single-name (sleeve Wealth)

La pagina "Track Record" del frontend (Fase 3) è essenzialmente uno `SELECT * FROM paper_strategy_daily ORDER BY date` con grafici sovrapposti.

## Pattern di query frequenti

### Ultimo prezzo per un ticker
```sql
SELECT close FROM prices_daily
WHERE ticker_id = (SELECT id FROM ticker_universe WHERE ticker = 'AAPL')
ORDER BY date DESC LIMIT 1;
```
Oppure usa la view `v_latest_prices`.

### Shortlist di oggi per uno screener
```sql
SELECT t.ticker, s.rank, s.score
FROM screener_results s JOIN ticker_universe t ON t.id = s.ticker_id
WHERE s.screener_profile = 'trend_etf_v1'
  AND s.run_date = CURRENT_DATE
ORDER BY s.rank;
```

### Hit rate di uno screener a 3 mesi
```sql
SELECT
    screener_profile,
    COUNT(*) AS signals,
    AVG(pnl_pct_3m) AS avg_return_3m,
    SUM(CASE WHEN pnl_pct_3m > 0 THEN 1 ELSE 0 END)::FLOAT / COUNT(*) AS hit_rate
FROM paper_signals
WHERE pnl_pct_3m IS NOT NULL
GROUP BY screener_profile;
```

### Alpha cumulato di una strategia vs benchmark
```sql
SELECT date, total_return_pct, alpha_pct
FROM paper_strategy_daily
WHERE strategy_code = 'trend_etf_v1'
ORDER BY date;
```

## Setup su Supabase

1. Crea progetto su [supabase.com](https://supabase.com) → New Project (regione: Frankfurt EU-Central, free tier).
2. Una volta pronto, vai in **SQL Editor**.
3. Apri [`db/schema.sql`](../db/schema.sql), copia tutto, incolla in SQL Editor e clicca Run.
4. Vai in **Table Editor** e verifica che le 11 tabelle siano comparse.
5. Vai in **Project Settings > API**: copia `URL` e `anon public key` in `backend/.env` (vedi `.env.example`).
6. Vai in **Project Settings > Database > Connection string > URI**: copia in `DATABASE_URL` di `.env` (usa il "Transaction pooler", port 6543).

## Row-Level Security (RLS)

Supabase abilita RLS di default. Per uso personale + ingestione via service_role key, RLS non blocca le query lato backend (la service_role key bypassa RLS). Quando arriviamo a frontend pubblico in Fase 5/deploy, abilitiamo policy specifiche per il read-only sull'anon key.

Per ora **non disabilitare RLS** — è una buona abitudine. Le query batch dal backend useranno la service_role key.

## Migrations

Per ora `schema.sql` è la fonte di verità completa. Se in futuro lo schema evolve, creiamo `db/migrations/0002_*.sql`, `0003_*.sql` ecc. e li applichiamo in ordine. Per la Fase 0 basta un singolo file.
