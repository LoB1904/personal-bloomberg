# Architettura Frontend React — Fase 3

## 1. Struttura cartelle `frontend/`

```
frontend/
├── public/
│   ├── favicon.ico
│   └── index.html
├── src/
│   ├── pages/
│   │   ├── Dashboard.tsx          # Shortlist + heatmap + perf overview
│   │   ├── Ticker.tsx             # Dettaglio titolo + chart + fondamentali
│   │   ├── Screener.tsx           # Parametri screener + risultati filtrabili
│   │   ├── TrackRecord.tsx        # ⭐ CORE: baseline + custom overlay
│   │   └── Portfolio.tsx          # Posizioni paper + allocazione
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx        # Nav + toggle Speculative/Wealth
│   │   │   ├── Header.tsx         # Mode toggle + AI bar
│   │   │   └── Layout.tsx         # Root wrapper
│   │   ├── charts/
│   │   │   ├── TradingViewChart.tsx    # TradingView Lightweight Charts
│   │   │   ├── PerformanceChart.tsx    # Multi-strategy overlay (Recharts)
│   │   │   ├── HeatMap.tsx             # Settori/geografie (Recharts)
│   │   │   └── DrawdownChart.tsx       # Max DD timeline
│   │   ├── tables/
│   │   │   ├── ShortlistTable.tsx      # Screener results
│   │   │   ├── SignalsTable.tsx        # Paper signals con P&L
│   │   │   ├── BaselineMetricsTable.tsx # Baseline summary
│   │   │   └── PortfolioTable.tsx      # Holdings
│   │   ├── cards/
│   │   │   ├── MetricCard.tsx          # KPI card (CAGR, Sharpe, etc.)
│   │   │   ├── StrategyCard.tsx        # Baseline/screener summary
│   │   │   └── TickerCard.tsx          # Mini ticker preview
│   │   ├── forms/
│   │   │   ├── ScreenerFilters.tsx     # Parametri screener
│   │   │   └── DateRangePicker.tsx     # Range selector
│   │   ├── dialogs/
│   │   │   ├── TickerDetailModal.tsx   # Popup dettaglio
│   │   │   └── AIBriefingModal.tsx     # AI insights popup
│   │   ├── ai/
│   │   │   ├── AIBar.tsx               # Command bar sempre visibile
│   │   │   ├── AIResponse.tsx          # Rendering risposta Claude
│   │   │   └── AILoading.tsx           # Skeleton durante AI call
│   │   └── shared/
│   │       ├── Badge.tsx               # Status badge (verde/rosso)
│   │       ├── Tooltip.tsx             # Info tooltip
│   │       ├── LoadingSpinner.tsx      # Loader
│   │       └── ErrorBoundary.tsx       # Error handling
│   ├── hooks/
│   │   ├── useApi.ts                   # Wrapper fetch con retry
│   │   ├── useScreener.ts              # Hook screener state
│   │   ├── useTrackRecord.ts           # Hook track record data
│   │   ├── useLocalStorage.ts          # Persist preferences
│   │   └── useAI.ts                    # Hook Claude integration
│   ├── lib/
│   │   ├── api.ts                      # API client (axios)
│   │   ├── types.ts                    # TypeScript interfaces
│   │   ├── constants.ts                # Config, colori, testi
│   │   ├── format.ts                   # Formatters (valuta, %)
│   │   └── math.ts                     # Utility calcoli
│   ├── styles/
│   │   ├── globals.css                 # Tailwind + custom CSS
│   │   ├── theme.ts                    # Tailwind config override
│   │   └── variables.css               # CSS variables (colori)
│   ├── App.tsx                         # Root component + routing
│   └── main.tsx                        # Entry point Vite
├── .env.example
├── .env.local                          # (git-ignored)
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.js
├── package.json
├── package-lock.json
└── README.md
```

---

## 2. Stack tecnico esatto con versioni

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.24.0",
    "vite": "^5.2.0",
    "@vitejs/plugin-react": "^4.2.1",
    "typescript": "^5.3.3",
    "@typescript-eslint/eslint-plugin": "^6.15.0",
    "@typescript-eslint/parser": "^6.15.0",
    "tailwindcss": "^3.4.1",
    "autoprefixer": "^10.4.16",
    "postcss": "^8.4.32",
    "@shadcn/ui": "^0.8.0",
    "clsx": "^2.0.0",
    "class-variance-authority": "^0.7.0",
    "radix-ui/react-dialog": "^1.1.1",
    "radix-ui/react-dropdown-menu": "^2.1.1",
    "radix-ui/react-popover": "^1.1.1",
    "radix-ui/react-tabs": "^1.0.4",
    "recharts": "^2.10.3",
    "lightweight-charts": "^4.1.0",
    "axios": "^1.6.5",
    "date-fns": "^2.30.0",
    "zustand": "^4.4.7",
    "react-hot-toast": "^2.4.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@types/node": "^20.10.0",
    "eslint": "^8.55.0",
    "eslint-config-prettier": "^9.1.0",
    "prettier": "^3.1.1"
  }
}
```

**Versioni critiche:**
- **React 18.3.1** — Latest stable
- **Vite 5.2.0** — Build tool moderno, dev server veloce
- **Tailwind 3.4.1** — Utility-first CSS
- **shadcn/ui 0.8.0** — Componenti headless Radix + Tailwind (dark mode built-in)
- **TradingView Lightweight Charts 4.1.0** — Charting library leggera
- **Recharts 2.10.3** — Performance multi-line charts
- **Zustand 4.4.7** — State management minimalista
- **Axios 1.6.5** — HTTP client con retry built-in
- **date-fns 2.30.0** — Date utilities (no moment.js bloat)

**Dark mode:** shadcn/ui supporta dark mode natively via Tailwind. Configurare `tailwind.config.js` con `darkMode: 'class'` e aggiungere `dark:` prefix su classi.

---

## 3. Le 5 pagine da implementare

### 3.1 Dashboard (Shortlist + Heatmap + Perf Overview)

**Scopo:** Punto di ingresso giornaliero. Visualizza la shortlist del giorno, performance live delle strategie, heatmap settoriale.

**Layout:**
```
┌─ Header: toggle Speculative/Wealth + AI bar ─────────────┐
├─────────────────────────────────────────────────────────┤
│ Sidebar (dark) │ Main content                            │
│                │                                         │
│ - Dashboard    │ ┌─ Shortlist oggi (Speculative/Wealth)─┐│
│ - Ticker       │ │ Rank | Ticker | Score | Signal       ││
│ - Screener     │ │  1   | MSFT   | 8.7   | Trend break  ││
│ - Track Record │ │  2   | AAPL   | 8.5   | Quality high ││
│ - Portfolio    │ │  3   | NVDA   | 8.2   | Momentum +   ││
│                │ └────────────────────────────────────┘│
│ [Settings]     │                                         │
│                │ ┌─ Performance snapshot (7d/30d/YTD) ──┐│
│                │ │ Speculative: +2.3% | Wealth: +1.1%  ││
│                │ │ MSCI World:  +1.8% | Dual Momentum: -0.5% ││
│                │ └────────────────────────────────────┘│
│                │                                         │
│                │ ┌─ Heatmap settoriale ──────────────┐│
│                │ │ [Tech] [Finance] [Health] [Energy]  ││
│                │ │ +2.1%  +0.8%     +1.5%   -1.2%     ││
│                │ └────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

**Componenti:**
- `ShortlistTable` — Tabella screener con click → Ticker detail
- `PerformanceChart` — Line chart 4 strategie (Speculative custom, Wealth custom, MSCI World, Dual Momentum) overlay
- `HeatMap` — Heatmap colori verde/rosso per settori
- `MetricCard` — KPI card (CAGR, Sharpe, max DD) per ogni strategia
- API calls:
  - `GET /screener/{profile}` → shortlist
  - `GET /paper/track-record` → metriche aggregate

**Dark mode styling:**
```tsx
<div className="dark bg-slate-950 text-slate-50">
  <h1 className="text-2xl font-bold text-slate-100">Dashboard</h1>
  <div className="grid grid-cols-4 gap-4">
    <MetricCard label="CAGR" value="12.5%" color="green" />
    <MetricCard label="Sharpe" value="1.23" color="blue" />
    <MetricCard label="Max DD" value="-8.2%" color="red" />
  </div>
</div>
```

---

### 3.2 Ticker Detail

**Scopo:** Analisi profonda di un singolo titolo. Chart TradingView + fondamentali + segnali storici + news.

**Layout:**
```
┌─ Header: MSFT | NASDAQ ──────────────────────────────────┐
├─────────────────────────────────────────────────────────┤
│                                                          │
│ ┌─ TradingView Chart (1h / daily / weekly) ────────────┐│
│ │ [Chart area — candlestick + MA200 + volume]          ││
│ │ Prezzo: $425.30 | Change: +1.2% | Vol: 45.2M        ││
│ └──────────────────────────────────────────────────────┘│
│                                                          │
│ ┌─ Fundamentals (grid 3 col) ──────────────────────────┐│
│ │ P/E: 28.5 | P/B: 15.2 | ROE: 32.1%                   ││
│ │ Debt/Eq: 0.15 | FCF: $65B | Div Yield: 0.7%         ││
│ │ Revenue Growth: 12.3% | EPS Growth: 15.2%           ││
│ └──────────────────────────────────────────────────────┘│
│                                                          │
│ ┌─ Signals (tab: historical + active) ─────────────────┐│
│ │ Date       | Screener  | Price | Status | P&L        ││
│ │ 2025-01-15 | Wealth    | 410   | open   | +3.7%      ││
│ │ 2024-12-20 | Specul.   | 395   | closed | +7.2%      ││
│ └──────────────────────────────────────────────────────┘│
│                                                          │
│ ┌─ News (ultimi 5) ────────────────────────────────────┐│
│ │ [2025-01-20] Microsoft beats Q1 earnings expectations ││
│ │ [2025-01-18] Cloud revenue accelerates, guidance up  ││
│ └──────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

**Componenti:**
- `TradingViewChart` — Lightweight Charts (candlestick + MA200 + volume)
- `FundamentalGrid` — 2-3 righe di KPI fondamentali
- `SignalsTable` — Storici segnali paper su questo ticker
- `NewsPanel` — Ultimi 5 news da RSS
- API calls:
  - `GET /ticker/{symbol}/prices` → serie storica
  - `GET /ticker/{symbol}/fundamentals` → fondamentali
  - `GET /paper/signals?ticker={symbol}` → segnali storici
  - `GET /ticker/{symbol}/news` → news items

---

### 3.3 Screener

**Scopo:** Parametri screener + risultati filtrabili. Permette di esplorare shortlist alternative e capire i criteri di scoring.

**Layout:**
```
┌─ Screener: Speculative | Wealth ─────────────────────────┐
├──────────────────────────────────────────────────────────┤
│ ┌─ Filters (sidebar sinistra) ────────────────────────┐ │
│ │ Mode: [Speculative ▼]                              │ │
│ │                                                     │ │
│ │ Score range: [0 ─────────── 10]                    │ │
│ │ Min rank: [1] Max rank: [20]                       │ │
│ │                                                     │ │
│ │ Sector: ☑ Tech ☑ Finance ☐ Health ☐ Energy       │ │
│ │                                                     │ │
│ │ Trend (Specul): ☑ Bullish ☑ Neutral ☐ Bearish   │ │
│ │ Quality (Wealth): ☑ High ☑ Medium ☐ Low          │ │
│ │                                                     │ │
│ │ [Apply Filters] [Reset]                           │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌─ Results (main area) ────────────────────────────────┐ │
│ │ Rank | Ticker | Score | Trend | Quality | PE | Action││
│ │  1   | MSFT   | 8.7   | ↑↑    