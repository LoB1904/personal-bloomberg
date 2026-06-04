// ── Screener ──────────────────────────────────────────────────────────

export interface SignalDetails {
  price?: number
  ma50?: number
  ma200?: number
  return_3m?: number
  return_6m?: number
  signal?: string
  weight?: number
  quality_score?: number
  garp_score?: number
  [key: string]: unknown
}

export interface ScreenerEntry {
  rank: number
  ticker: string
  score: number
  signal_details: SignalDetails
}

export interface ScreenerResponse {
  profile: string
  run_date: string
  n_signals: number
  shortlist: ScreenerEntry[]
}

// ── Paper Signals ─────────────────────────────────────────────────────

export interface PnlByWindow {
  '1w': number | null
  '1m': number | null
  '3m': number | null
  '6m': number | null
  '12m': number | null
}

export type SignalStatus = 'open' | 'closed_12m'

export interface PaperSignal {
  id: number
  ticker: string
  screener_profile: string
  entry_date: string
  entry_price: number
  status: SignalStatus
  pnl: PnlByWindow
}

export interface PaperSignalsResponse {
  n_signals: number
  signals: PaperSignal[]
}

// ── Track Record ──────────────────────────────────────────────────────

export interface WindowMetrics {
  avg_pnl: number | null
  hit_rate: number | null
  n: number
}

export interface ScreenerProfileMetrics {
  n_signals: number
  n_closed: number
  windows: Record<string, WindowMetrics>
}

export interface BaselineMetrics {
  start_date: string
  end_date: string
  total_return: number
  cagr: number | null
  max_drawdown: number
  sharpe: number | null
}

export interface TrackRecordResponse {
  screener: Record<string, ScreenerProfileMetrics>
  baselines: Record<string, BaselineMetrics>
}

// ── Strategy History ─────────────────────────────────────────────────

export interface StrategyDataPoint {
  date: string
  value: number
}

export interface StrategyHistoryResponse {
  series: Record<string, StrategyDataPoint[]>
}

// ── Shared ────────────────────────────────────────────────────────────

export type ScreenerProfile = 'speculative_trend_etf' | 'wealth_quality_garp'
