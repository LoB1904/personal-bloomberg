import { useEffect, useRef, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  createChart,
  ColorType,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
} from 'lightweight-charts'
import type { Time } from 'lightweight-charts'
import clsx from 'clsx'
import { getPaperSignals } from '../lib/api'
import type { PaperSignal } from '../lib/types'

// ── Ticker metadata ───────────────────────────────────────────────────

interface TickerMeta { name: string; exchange: string; sector: string; isEtf: boolean }

const TICKER_META: Record<string, TickerMeta> = {
  EEM:  { name: 'iShares MSCI Emerging Markets ETF',     exchange: 'NASDAQ', sector: 'ETF — Emerging Markets', isEtf: true  },
  XLK:  { name: 'Technology Select Sector SPDR Fund',    exchange: 'NYSE',   sector: 'Technology',              isEtf: true  },
  DBC:  { name: 'Invesco DB Commodity Index Fund',       exchange: 'NYSE',   sector: 'Commodities',             isEtf: true  },
  XLRE: { name: 'Real Estate Select Sector SPDR Fund',   exchange: 'NYSE',   sector: 'Real Estate',             isEtf: true  },
  XLY:  { name: 'Consumer Discret. Select Sector SPDR',  exchange: 'NYSE',   sector: 'Consumer Disc.',          isEtf: true  },
  SPY:  { name: 'SPDR S&P 500 ETF Trust',                exchange: 'NYSE',   sector: 'ETF — US Large Cap',      isEtf: true  },
  VWCE: { name: 'Vanguard FTSE All-World UCITS ETF',     exchange: 'XETRA',  sector: 'ETF — Global',            isEtf: true  },
  AAPL: { name: 'Apple Inc.',                            exchange: 'NASDAQ', sector: 'Technology',              isEtf: false },
  MSFT: { name: 'Microsoft Corporation',                 exchange: 'NASDAQ', sector: 'Technology',              isEtf: false },
  NVDA: { name: 'NVIDIA Corporation',                    exchange: 'NASDAQ', sector: 'Technology',              isEtf: false },
  AMZN: { name: 'Amazon.com Inc.',                       exchange: 'NASDAQ', sector: 'Consumer Disc.',          isEtf: false },
}

// ── Fundamentals mock ─────────────────────────────────────────────────

interface FundData {
  pe: number | null; pb: number | null
  roe: number | null; roic: number | null
  debtEq: number | null; fcfYield: number | null
  revGrowth: number | null; epsGrowth: number | null
}

const FUND_MOCK: Record<string, FundData> = {
  AAPL: { pe: 32.1, pb: 48.5, roe: 147.9, roic: 56.8, debtEq: 1.95, fcfYield: 3.8,  revGrowth: 7.8,   epsGrowth: 12.3  },
  MSFT: { pe: 37.4, pb: 14.2, roe: 38.7,  roic: 29.1, debtEq: 0.31, fcfYield: 2.9,  revGrowth: 15.1,  epsGrowth: 18.4  },
  NVDA: { pe: 55.2, pb: 32.8, roe: 122.4, roic: 89.3, debtEq: 0.43, fcfYield: 1.1,  revGrowth: 114.2, epsGrowth: 168.0 },
  AMZN: { pe: 44.7, pb: 9.1,  roe: 23.8,  roic: 16.2, debtEq: 0.58, fcfYield: 3.1,  revGrowth: 12.5,  epsGrowth: 94.8  },
}

const NULL_FUND: FundData = {
  pe: null, pb: null, roe: null, roic: null,
  debtEq: null, fcfYield: null, revGrowth: null, epsGrowth: null,
}

// ── Mock OHLCV generator ──────────────────────────────────────────────

interface Bar { time: string; open: number; high: number; low: number; close: number; volume: number }

const ANCHOR_PRICES: Record<string, number> = {
  EEM: 68.6, XLK: 191.0, DBC: 29.5, XLRE: 44.0, XLY: 120.9,
  SPY: 550.0, VWCE: 162.0, AAPL: 195.0, MSFT: 420.0, NVDA: 130.0, AMZN: 210.0,
}

function seededRand(seed: number) {
  let s = seed >>> 0
  return () => { s = (Math.imul(1664525, s) + 1013904223) >>> 0; return s / 0xffffffff }
}

function generateOHLCV(symbol: string, days = 800): Bar[] {
  const seed = symbol.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0)
  const rand = seededRand(seed)
  const endPrice = ANCHOR_PRICES[symbol] ?? (40 + (seed % 120))
  let price = endPrice / Math.exp(0.00025 * days)

  const today = new Date('2026-06-04')
  const bars: Bar[] = []

  for (let i = days; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(d.getDate() - i)
    if (d.getDay() === 0 || d.getDay() === 6) continue

    const open = price
    const change = (rand() - 0.47) * 0.014 + 0.00025
    const close = Math.max(0.01, open * (1 + change))
    const high = Math.max(open, close) * (1 + rand() * 0.005)
    const low  = Math.min(open, close) * (1 - rand() * 0.005)
    const volume = Math.floor((800_000 + rand() * 6_000_000) * (Math.abs(change) * 25 + 0.6))

    bars.push({
      time:   d.toISOString().split('T')[0],
      open:   +open.toFixed(3),
      high:   +high.toFixed(3),
      low:    +low.toFixed(3),
      close:  +close.toFixed(3),
      volume,
    })
    price = close
  }
  return bars
}

function calcMA200(bars: Bar[]) {
  return bars
    .map((b, i) => {
      if (i < 199) return null
      const avg = bars.slice(i - 199, i + 1).reduce((s, x) => s + x.close, 0) / 200
      return { time: b.time, value: +avg.toFixed(3) }
    })
    .filter((x): x is { time: string; value: number } => x !== null)
}

// ── Helpers ───────────────────────────────────────────────────────────

type Timeframe = '1M' | '3M' | '1Y' | '3Y'
const TF_BARS: Record<Timeframe, number> = { '1M': 21, '3M': 63, '1Y': 252, '3Y': 756 }

function fmtPct(v: number | null, d = 2) {
  if (v === null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`
}
function clrPct(v: number | null) {
  if (v === null) return 'text-slate-500'
  return v >= 0 ? 'text-green-400' : 'text-red-400'
}
function fmtNum(v: number | null, suffix = '') {
  if (v === null) return '—'
  return `${v.toFixed(1)}${suffix}`
}

// ── PriceChart subcomponent ───────────────────────────────────────────

function PriceChart({
  bars,
  ma200,
  timeframe,
  onTimeframeChange,
}: {
  bars: Bar[]
  ma200: { time: string; value: number }[]
  timeframe: Timeframe
  onTimeframeChange: (tf: Timeframe) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<ReturnType<typeof createChart> | null>(null)

  // Create chart once, update visible range on timeframe change
  useEffect(() => {
    const container = containerRef.current
    if (!container || bars.length === 0) return

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#020617' },
        textColor: '#64748b',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#0f172a' },
        horzLines: { color: '#0f172a' },
      },
      crosshair: {
        vertLine: { color: '#475569', labelBackgroundColor: '#1e293b' },
        horzLine: { color: '#475569', labelBackgroundColor: '#1e293b' },
      },
      rightPriceScale:  { borderColor: '#1e293b' },
      timeScale:        { borderColor: '#1e293b', timeVisible: true, secondsVisible: false },
      width:  container.clientWidth,
      height: 420,
    })
    chartRef.current = chart

    // Candlestick
    const candles = chart.addSeries(CandlestickSeries, {
      upColor:       '#22c55e',
      downColor:     '#ef4444',
      borderVisible: false,
      wickUpColor:   '#22c55e',
      wickDownColor: '#ef4444',
    })
    candles.setData(bars.map(b => ({ ...b, time: b.time as Time })))

    // MA200 line
    const maLine = chart.addSeries(LineSeries, {
      color:             '#fbbf24',
      lineWidth:         1,
      priceLineVisible:  false,
      lastValueVisible:  false,
      crosshairMarkerVisible: false,
    })
    maLine.setData(ma200.map(p => ({ time: p.time as Time, value: p.value })))

    // Volume histogram overlay — bottom 15% of chart
    const volSeries = chart.addSeries(HistogramSeries, {
      priceFormat:  { type: 'volume' },
      priceScaleId: 'vol',
    })
    chart.priceScale('vol').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    })
    volSeries.setData(
      bars.map(b => ({
        time:  b.time as Time,
        value: b.volume,
        color: b.close >= b.open ? '#22c55e30' : '#ef444430',
      })),
    )

    // Responsive resize
    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
    })
    ro.observe(container)

    return () => { ro.disconnect(); chart.remove(); chartRef.current = null }
  }, [bars, ma200])

  // Update visible range when timeframe changes
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || bars.length === 0) return
    const count = TF_BARS[timeframe]
    const from  = bars[Math.max(0, bars.length - count)].time as Time
    const to    = bars[bars.length - 1].time as Time
    chart.timeScale().setVisibleRange({ from, to })
  }, [timeframe, bars])

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-slate-600">
          Prezzi simulati — dati live in Fase 4 · MA200 <span className="text-amber-500">▬</span>
        </span>
        <div className="flex gap-1">
          {(['1M', '3M', '1Y', '3Y'] as Timeframe[]).map(tf => (
            <button
              key={tf}
              onClick={() => onTimeframeChange(tf)}
              className={clsx(
                'px-2.5 py-1 text-xs rounded font-medium transition-colors',
                timeframe === tf
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-500 hover:text-slate-200',
              )}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>
      <div ref={containerRef} className="w-full" style={{ height: 420 }} />
    </div>
  )
}

// ── Fundamentals grid ─────────────────────────────────────────────────

function FundamentalsGrid({ fund, isEtf }: { fund: FundData; isEtf: boolean }) {
  const cells: { label: string; value: string; sub?: string }[] = [
    { label: 'P/E Ratio',    value: fmtNum(fund.pe, 'x'),       sub: 'Price/Earnings' },
    { label: 'P/B Ratio',    value: fmtNum(fund.pb, 'x'),       sub: 'Price/Book' },
    { label: 'ROE',          value: fmtNum(fund.roe, '%'),      sub: 'Return on Equity' },
    { label: 'ROIC',         value: fmtNum(fund.roic, '%'),     sub: 'Return on Inv. Capital' },
    { label: 'Debt / Equity',value: fmtNum(fund.debtEq, 'x'),  sub: 'Leva finanziaria' },
    { label: 'FCF Yield',    value: fmtNum(fund.fcfYield, '%'), sub: 'Free Cash Flow / MktCap' },
    { label: 'Rev Growth',   value: fmtPct(fund.revGrowth),     sub: 'YoY Revenue Growth' },
    { label: 'EPS Growth',   value: fmtPct(fund.epsGrowth),     sub: 'YoY EPS Growth' },
  ]

  return (
    <div className="rounded-lg border border-slate-800">
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800">
        <h2 className="text-sm font-semibold text-slate-300">Fondamentali</h2>
        <span className="text-xs text-slate-600">
          {isEtf ? 'N/A per ETF' : 'dati mock — live con EODHD in Fase 4'}
        </span>
      </div>
      <div className="grid grid-cols-4 divide-x divide-slate-800">
        {cells.map(({ label, value, sub }, i) => (
          <div
            key={label}
            className={clsx(
              'px-5 py-4',
              i >= 4 && 'border-t border-slate-800',
            )}
          >
            <p className="text-xs text-slate-500">{label}</p>
            <p className="text-xl font-bold font-mono text-slate-100 mt-0.5">{value}</p>
            {sub && <p className="text-xs text-slate-600 mt-0.5">{sub}</p>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Signals table ─────────────────────────────────────────────────────

function SignalsTable({ signals, symbol }: { signals: PaperSignal[]; symbol: string }) {
  const ticker_signals = signals.filter(s => s.ticker === symbol)

  return (
    <div className="rounded-lg border border-slate-800">
      <div className="px-5 py-3 border-b border-slate-800">
        <h2 className="text-sm font-semibold text-slate-300">
          Segnali paper trading
          <span className="ml-2 text-slate-600 font-normal">({ticker_signals.length})</span>
        </h2>
      </div>

      {ticker_signals.length === 0 ? (
        <div className="px-5 py-8 text-center">
          <p className="text-slate-500 text-sm">
            Nessun segnale per <span className="text-slate-300 font-mono">{symbol}</span>
          </p>
          <p className="text-slate-600 text-xs mt-1">
            Il ticker non è ancora nella shortlist screener
          </p>
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 bg-slate-900/50">
              <th className="text-left px-5 py-3 text-slate-400 font-medium">Data entry</th>
              <th className="text-left px-5 py-3 text-slate-400 font-medium">Screener</th>
              <th className="text-right px-5 py-3 text-slate-400 font-medium">Entry price</th>
              <th className="text-right px-5 py-3 text-slate-400 font-medium">Status</th>
              <th className="text-right px-5 py-3 text-slate-400 font-medium">P&L 1w</th>
              <th className="text-right px-5 py-3 text-slate-400 font-medium">P&L 1m</th>
            </tr>
          </thead>
          <tbody>
            {ticker_signals.map((s, i) => (
              <tr
                key={s.id}
                className={clsx(
                  'border-b border-slate-800/50',
                  i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900/20',
                )}
              >
                <td className="px-5 py-3 text-slate-300 font-mono text-xs">{s.entry_date}</td>
                <td className="px-5 py-3">
                  <span className={clsx(
                    'text-xs px-2 py-0.5 rounded border',
                    s.screener_profile === 'speculative_trend_etf'
                      ? 'border-blue-800 text-blue-400 bg-blue-950/40'
                      : 'border-violet-800 text-violet-400 bg-violet-950/40',
                  )}>
                    {s.screener_profile === 'speculative_trend_etf' ? 'Speculative' : 'Wealth'}
                  </span>
                </td>
                <td className="px-5 py-3 text-right font-mono text-slate-200">
                  ${s.entry_price.toFixed(2)}
                </td>
                <td className="px-5 py-3 text-right">
                  <span className={clsx(
                    'text-xs px-2 py-0.5 rounded',
                    s.status === 'open'
                      ? 'bg-green-950/50 text-green-400 border border-green-800/50'
                      : 'bg-slate-800 text-slate-400',
                  )}>
                    {s.status}
                  </span>
                </td>
                <td className={clsx('px-5 py-3 text-right font-mono', clrPct(s.pnl['1w']))}>
                  {fmtPct(s.pnl['1w'])}
                </td>
                <td className={clsx('px-5 py-3 text-right font-mono', clrPct(s.pnl['1m']))}>
                  {fmtPct(s.pnl['1m'])}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────

export default function Ticker() {
  const { symbol = '' } = useParams<{ symbol: string }>()
  const [timeframe, setTimeframe] = useState<Timeframe>('1Y')
  const [signals, setSignals] = useState<PaperSignal[]>([])

  // Generate mock price data once per symbol
  const bars  = useMemo(() => generateOHLCV(symbol), [symbol])
  const ma200 = useMemo(() => calcMA200(bars), [bars])

  // Ticker metadata
  const meta = TICKER_META[symbol] ?? {
    name: symbol, exchange: '—', sector: '—', isEtf: false,
  }

  // Fundamentals
  const fund = meta.isEtf ? NULL_FUND : (FUND_MOCK[symbol] ?? NULL_FUND)

  // Last bar for header price
  const lastBar = bars[bars.length - 1]
  const prevBar = bars[bars.length - 2]
  const changeToday = prevBar
    ? ((lastBar.close - prevBar.close) / prevBar.close) * 100
    : null

  // Fetch signals
  useEffect(() => {
    getPaperSignals()
      .then(r => setSignals(r.signals))
      .catch(() => setSignals([]))
  }, [])

  return (
    <div className="space-y-6 max-w-6xl">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold text-white font-mono">{symbol}</h1>
            <span className="text-xs px-2 py-0.5 rounded border border-slate-700 text-slate-400">
              {meta.exchange}
            </span>
            <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300">
              {meta.sector}
            </span>
          </div>
          <p className="text-slate-400 text-sm mt-1">{meta.name}</p>
        </div>

        <div className="text-right">
          <p className="text-3xl font-bold font-mono text-white">
            ${lastBar?.close.toFixed(2) ?? '—'}
          </p>
          {changeToday !== null && (
            <p className={clsx('text-sm font-mono mt-0.5', clrPct(changeToday))}>
              {fmtPct(changeToday)} oggi
            </p>
          )}
        </div>
      </div>

      {/* Chart */}
      <PriceChart
        bars={bars}
        ma200={ma200}
        timeframe={timeframe}
        onTimeframeChange={setTimeframe}
      />

      {/* Fundamentals */}
      <FundamentalsGrid fund={fund} isEtf={meta.isEtf} />

      {/* Signals */}
      <SignalsTable signals={signals} symbol={symbol} />

    </div>
  )
}
