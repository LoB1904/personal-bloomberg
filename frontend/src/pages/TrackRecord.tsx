import { useEffect, useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { differenceInDays, parseISO, format } from 'date-fns'
import clsx from 'clsx'
import { getTrackRecord, getStrategyHistory } from '../lib/api'
import type { TrackRecordResponse, StrategyHistoryResponse } from '../lib/types'

// ── Config ────────────────────────────────────────────────────────────

const STRATEGY_META: Record<string, { label: string; color: string }> = {
  speculative_trend_etf:  { label: 'Speculative (Trend)',  color: '#3b82f6' },
  wealth_quality_garp:    { label: 'Wealth (Quality)',     color: '#8b5cf6' },
  baseline_bh_vwce:       { label: 'B&H VWCE',            color: '#6b7280' },
  baseline_dual_momentum: { label: 'Dual Momentum',        color: '#f59e0b' },
  baseline_ew_sp100:      { label: 'EW S&P100',            color: '#10b981' },
}

const BASELINE_LABEL: Record<string, string> = {
  baseline_bh_vwce:       'B&H VWCE',
  baseline_dual_momentum: 'Dual Momentum',
  baseline_ew_sp100:      'EW S&P100',
}

// ── Helpers ───────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, suffix = '%', decimals = 2): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}${suffix}`
}

function colorPct(v: number | null | undefined): string {
  if (v == null) return 'text-slate-400'
  return v >= 0 ? 'text-green-400' : 'text-red-400'
}

// Merge series con date diverse in array per Recharts
function mergeSeries(
  history: StrategyHistoryResponse['series'],
): Record<string, number | null>[] {
  const allDates = new Set<string>()
  for (const pts of Object.values(history)) {
    pts.forEach(p => allDates.add(p.date))
  }
  const sorted = Array.from(allDates).sort()

  // Indice veloce: strategy → date → value
  const idx: Record<string, Record<string, number>> = {}
  for (const [code, pts] of Object.entries(history)) {
    idx[code] = {}
    pts.forEach(p => { idx[code][p.date] = p.value })
  }

  return sorted.map(date => {
    const row: Record<string, number | null> = { _date: date as unknown as number }
    for (const code of Object.keys(history)) {
      row[code] = idx[code][date] ?? null
    }
    return row
  })
}

// ── Custom Tooltip ────────────────────────────────────────────────────

interface TooltipProps {
  active?: boolean
  payload?: { name: string; value: number; color: string }[]
  label?: string
}

function ChartTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length || !label) return null
  return (
    <div className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-xs shadow-lg">
      <p className="text-slate-400 mb-1">{label}</p>
      {payload.map(p => (
        <p key={p.name} style={{ color: p.color }}>
          {STRATEGY_META[p.name]?.label ?? p.name}:{' '}
          <span className="font-semibold">{fmt(p.value)}</span>
        </p>
      ))}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────

export default function TrackRecord() {
  const [metrics, setMetrics] = useState<TrackRecordResponse | null>(null)
  const [history, setHistory] = useState<StrategyHistoryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    Promise.all([getTrackRecord(), getStrategyHistory()])
      .then(([m, h]) => {
        if (cancelled) return
        console.log('[TrackRecord] strategy-history series:')
        for (const [code, pts] of Object.entries(h.series)) {
          console.log(`  ${code}: ${pts.length} punti  |  da ${pts[0]?.date} a ${pts[pts.length - 1]?.date}`)
        }
        setMetrics(m)
        setHistory(h)
      })
      .catch(err => {
        if (cancelled) return
        setError(
          err?.response?.status
            ? `Errore ${err.response.status}: backend non raggiungibile`
            : 'Backend non raggiungibile — avvia uvicorn su porta 8000',
        )
      })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [])

  if (loading) return <LoadingState />
  if (error)   return <ErrorState message={error} />
  if (!metrics) return null

  const vwce = metrics.baselines['baseline_bh_vwce']

  // Controlla dati screener insufficienti (< 30 giorni dal primo segnale)
  const screenerProfiles = Object.entries(metrics.screener)
  const screenerInsufficient = screenerProfiles.every(([, s]) => s.n_signals === 0)

  // Costruisci righe tabella: baselines + screener
  const tableRows = buildTableRows(metrics, vwce?.cagr ?? null)

  // Dati grafico
  const chartData = history ? mergeSeries(history.series) : []
  const chartSeries = Object.keys(history?.series ?? {})

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-white">Track Record</h1>
        <p className="text-slate-400 mt-1 text-sm">
          Performance storica delle strategie vs benchmark
        </p>
      </div>

      {/* Metrics table */}
      <MetricsTable rows={tableRows} />

      {/* Screener in costruzione */}
      {screenerInsufficient && (
        <InsufficientDataBanner entryDate={null} />
      )}
      {!screenerInsufficient && screenerProfiles.map(([profile, data]) => {
        const firstWindowData = Object.values(data.windows).find(w => w.n > 0)
        if (!firstWindowData) {
          return <InsufficientDataBanner key={profile} entryDate={null} />
        }
        return null
      })}

      {/* Performance chart */}
      {chartData.length > 0 && (
        <PerformanceChart data={chartData} series={chartSeries} />
      )}
    </div>
  )
}

// ── Subcomponents ─────────────────────────────────────────────────────

function LoadingState() {
  return (
    <div className="flex items-center justify-center h-64 text-slate-400">
      <div className="text-center space-y-3">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-sm">Caricamento dati...</p>
      </div>
    </div>
  )
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-800 bg-red-950/30 p-6 text-center">
      <p className="text-red-400 font-medium">{message}</p>
      <p className="text-slate-500 text-sm mt-2">
        Controlla che <code className="text-slate-400">uvicorn app.main:app --port 8000</code> sia attivo
      </p>
    </div>
  )
}

function InsufficientDataBanner({ entryDate }: { entryDate: string | null }) {
  const daysLeft = entryDate
    ? Math.max(0, 30 - differenceInDays(new Date(), parseISO(entryDate)))
    : null

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-5 text-center">
      <p className="text-slate-300 font-medium">Track record in costruzione</p>
      <p className="text-slate-500 text-sm mt-1">
        {daysLeft != null
          ? `Torna tra ${daysLeft} giorni per i primi dati`
          : 'Esegui lo screener giornaliero per iniziare ad accumulare segnali'}
      </p>
    </div>
  )
}

// ── Table ─────────────────────────────────────────────────────────────

interface TableRow {
  code: string
  label: string
  color: string
  cagr: number | null
  sharpe: number | null
  maxDrawdown: number | null
  signals: number | null
  vsVwce: number | null
}

function buildTableRows(
  metrics: TrackRecordResponse,
  vwceCagr: number | null,
): TableRow[] {
  const rows: TableRow[] = []

  // Baselines
  for (const [code, b] of Object.entries(metrics.baselines)) {
    rows.push({
      code,
      label:       BASELINE_LABEL[code] ?? code,
      color:       STRATEGY_META[code]?.color ?? '#94a3b8',
      cagr:        b.cagr,
      sharpe:      b.sharpe,
      maxDrawdown: b.max_drawdown,
      signals:     null,
      vsVwce:      b.cagr != null && vwceCagr != null ? +(b.cagr - vwceCagr).toFixed(2) : null,
    })
  }

  // Screener profiles
  for (const [profile, s] of Object.entries(metrics.screener)) {
    rows.push({
      code:        profile,
      label:       STRATEGY_META[profile]?.label ?? profile,
      color:       STRATEGY_META[profile]?.color ?? '#94a3b8',
      cagr:        null,
      sharpe:      null,
      maxDrawdown: null,
      signals:     s.n_signals,
      vsVwce:      null,
    })
  }

  return rows
}

function MetricsTable({ rows }: { rows: TableRow[] }) {
  return (
    <div className="rounded-lg border border-slate-800 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 bg-slate-900">
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Strategia</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">CAGR</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Sharpe</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Max DD</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Segnali</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">vs VWCE</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={row.code}
              className={clsx(
                'border-b border-slate-800/50',
                i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900/30',
              )}
            >
              {/* Strategia */}
              <td className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <span
                    className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: row.color }}
                  />
                  <span className="text-slate-200 font-medium">{row.label}</span>
                </div>
              </td>

              {/* CAGR */}
              <td className={clsx('px-4 py-3 text-right font-mono', colorPct(row.cagr))}>
                {fmt(row.cagr)}
              </td>

              {/* Sharpe */}
              <td className={clsx('px-4 py-3 text-right font-mono', colorPct(row.sharpe))}>
                {fmt(row.sharpe, '', 2)}
              </td>

              {/* Max DD — sempre rosso */}
              <td className="px-4 py-3 text-right font-mono text-red-400">
                {row.maxDrawdown != null ? `${row.maxDrawdown.toFixed(2)}%` : '—'}
              </td>

              {/* Segnali */}
              <td className="px-4 py-3 text-right text-slate-300">
                {row.signals != null ? row.signals : '—'}
              </td>

              {/* vs VWCE */}
              <td className={clsx('px-4 py-3 text-right font-mono', colorPct(row.vsVwce))}>
                {row.code === 'baseline_bh_vwce' ? (
                  <span className="text-slate-600 text-xs">benchmark</span>
                ) : (
                  fmt(row.vsVwce)
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Chart ─────────────────────────────────────────────────────────────

function PerformanceChart({
  data,
  series,
}: {
  data: Record<string, number | null>[]
  series: string[]
}) {
  // Tick date formatter: mostra solo anno per non sovraffollare asse X
  const tickFmt = (v: string) => {
    try { return format(parseISO(v), 'yyyy') }
    catch { return v }
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-6">
      <h2 className="text-slate-200 font-semibold mb-6">
        Performance storica — total return %
      </h2>
      <ResponsiveContainer width="100%" height={360}>
        <LineChart data={data} margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis
            dataKey="_date"
            tickFormatter={tickFmt}
            tick={{ fill: '#64748b', fontSize: 11 }}
            axisLine={{ stroke: '#334155' }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={['auto', 'auto']}
            tickFormatter={v => `${v}%`}
            tick={{ fill: '#64748b', fontSize: 11 }}
            axisLine={{ stroke: '#334155' }}
            tickLine={false}
            width={56}
          />
          <Tooltip content={<ChartTooltip />} />
          <Legend
            wrapperStyle={{ paddingTop: '16px' }}
            formatter={(value: string) =>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>
                {STRATEGY_META[value]?.label ?? value}
              </span>
            }
          />
          {series.map(code => (
            <Line
              key={code}
              type="monotone"
              dataKey={code}
              stroke={STRATEGY_META[code]?.color ?? '#94a3b8'}
              strokeWidth={1.5}
              dot={false}
              connectNulls={true}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
