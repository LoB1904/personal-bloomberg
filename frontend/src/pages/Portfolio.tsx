import { useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { getPaperSignals } from '../lib/api'
import type { PaperSignal } from '../lib/types'

// ── Helpers ───────────────────────────────────────────────────────────

function fmtPct(v: number | null | undefined) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}
function clrPct(v: number | null | undefined) {
  if (v == null) return 'text-slate-500'
  return v >= 0 ? 'text-green-400' : 'text-red-400'
}
function fmtDate(s: string) {
  return s.slice(0, 10)
}
function fmtProfile(p: string) {
  return p === 'speculative_trend_etf' ? 'Speculative' : 'Wealth'
}

// ── MetricCard ────────────────────────────────────────────────────────

function MetricCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 px-5 py-4">
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{label}</p>
      <p className="text-2xl font-bold text-white font-mono">{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  )
}

// ── Open positions table ──────────────────────────────────────────────

function OpenTable({ rows }: { rows: PaperSignal[] }) {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900 flex items-center justify-center h-32">
        <p className="text-slate-500 text-sm">Nessuna posizione aperta</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-slate-800 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 bg-slate-900">
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Ticker</th>
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Screener</th>
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Entry date</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Entry price</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">P&amp;L 1w</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">P&amp;L 1m</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={row.id}
              className={clsx(
                'border-b border-slate-800/50',
                i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900/30',
              )}
            >
              <td className="px-4 py-3">
                <span className="font-semibold text-white">{row.ticker}</span>
              </td>
              <td className="px-4 py-3 text-slate-400 text-xs">{fmtProfile(row.screener_profile)}</td>
              <td className="px-4 py-3 text-slate-400 font-mono text-xs">{fmtDate(row.entry_date)}</td>
              <td className="px-4 py-3 text-right font-mono text-slate-300">
                {row.entry_price != null ? row.entry_price.toFixed(2) : '—'}
              </td>
              <td className={clsx('px-4 py-3 text-right font-mono text-sm', clrPct(row.pnl['1w']))}>
                {fmtPct(row.pnl['1w'])}
              </td>
              <td className={clsx('px-4 py-3 text-right font-mono text-sm', clrPct(row.pnl['1m']))}>
                {fmtPct(row.pnl['1m'])}
              </td>
              <td className="px-4 py-3 text-right">
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-900/50 text-blue-400 border border-blue-800/50">
                  open
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Closed signals table ──────────────────────────────────────────────

function ClosedTable({ rows }: { rows: PaperSignal[] }) {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900 flex items-center justify-center h-32">
        <p className="text-slate-500 text-sm">Nessun segnale chiuso</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-slate-800 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 bg-slate-900">
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Ticker</th>
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Screener</th>
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Entry date</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Entry price</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">P&amp;L 1w</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">P&amp;L 1m</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">P&amp;L 3m</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">P&amp;L 12m</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={row.id}
              className={clsx(
                'border-b border-slate-800/50',
                i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900/30',
              )}
            >
              <td className="px-4 py-3">
                <span className="font-semibold text-white">{row.ticker}</span>
              </td>
              <td className="px-4 py-3 text-slate-400 text-xs">{fmtProfile(row.screener_profile)}</td>
              <td className="px-4 py-3 text-slate-400 font-mono text-xs">{fmtDate(row.entry_date)}</td>
              <td className="px-4 py-3 text-right font-mono text-slate-300">
                {row.entry_price != null ? row.entry_price.toFixed(2) : '—'}
              </td>
              <td className={clsx('px-4 py-3 text-right font-mono', clrPct(row.pnl['1w']))}>
                {fmtPct(row.pnl['1w'])}
              </td>
              <td className={clsx('px-4 py-3 text-right font-mono', clrPct(row.pnl['1m']))}>
                {fmtPct(row.pnl['1m'])}
              </td>
              <td className={clsx('px-4 py-3 text-right font-mono', clrPct(row.pnl['3m']))}>
                {fmtPct(row.pnl['3m'])}
              </td>
              <td className={clsx('px-4 py-3 text-right font-mono', clrPct(row.pnl['12m']))}>
                {fmtPct(row.pnl['12m'])}
              </td>
              <td className="px-4 py-3 text-right">
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700">
                  closed
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────

export default function Portfolio() {
  const [signals, setSignals] = useState<PaperSignal[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState<string | null>(null)

  useEffect(() => {
    getPaperSignals()
      .then(r => { setSignals(r.signals); setLoading(false) })
      .catch(() => { setError('Backend non raggiungibile'); setLoading(false) })
  }, [])

  const open   = useMemo(() => signals.filter(s => s.status === 'open'), [signals])
  const closed = useMemo(
    () => [...signals.filter(s => s.status !== 'open')].sort(
      (a, b) => b.entry_date.localeCompare(a.entry_date),
    ),
    [signals],
  )

  const hitRate = useMemo(() => {
    const closedWithPnl = closed.filter(s => s.pnl['1m'] != null)
    if (closedWithPnl.length === 0) return null
    const winners = closedWithPnl.filter(s => (s.pnl['1m'] ?? 0) > 0)
    return (winners.length / closedWithPnl.length) * 100
  }, [closed])

  return (
    <div className="space-y-8 max-w-6xl">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">Portfolio</h1>
        <p className="text-slate-400 text-sm mt-0.5">
          {loading
            ? 'Caricamento...'
            : `${open.length} posizioni aperte · ${closed.length} segnali chiusi`}
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/20 px-5 py-4">
          <p className="text-amber-400 text-sm">{error}</p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center h-40">
          <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {!loading && !error && (
        <>
          {/* MetricCards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <MetricCard
              label="Posizioni aperte"
              value={open.length.toString()}
            />
            <MetricCard
              label="Hit rate (1m)"
              value={hitRate != null ? `${hitRate.toFixed(0)}%` : '—'}
              sub={closed.length > 0 ? `su ${closed.filter(s => s.pnl['1m'] != null).length} segnali chiusi` : undefined}
            />
            <MetricCard
              label="Segnali totali"
              value={signals.length.toString()}
              sub={`${closed.length} chiusi`}
            />
            <MetricCard
              label="Screener attivi"
              value={[...new Set(open.map(s => s.screener_profile))].length.toString()}
              sub="profili con pos. aperte"
            />
          </div>

          {/* Open positions */}
          <div className="space-y-3">
            <h2 className="text-base font-semibold text-slate-200">Posizioni aperte</h2>
            <OpenTable rows={open} />
          </div>

          {/* Closed signals */}
          <div className="space-y-3">
            <h2 className="text-base font-semibold text-slate-200">Storico segnali chiusi</h2>
            <ClosedTable rows={closed} />
          </div>
        </>
      )}

    </div>
  )
}
