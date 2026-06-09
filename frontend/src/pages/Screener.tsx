import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { getScreener } from '../lib/api'
import type { ScreenerEntry, ScreenerProfile } from '../lib/types'

// ── Types ─────────────────────────────────────────────────────────────

type SortKey = 'score' | 'ticker' | 'ret_3m'
type SignalFilter = 'all' | 'long' | 'flat'

interface ProfileData {
  shortlist: ScreenerEntry[]
  runDate: string
  loading: boolean
  error: string | null
}

// ── CSV export ────────────────────────────────────────────────────────

function exportCSV(rows: ScreenerEntry[], profile: ScreenerProfile, runDate: string) {
  const isSpec = profile === 'speculative_trend_etf'
  const header = isSpec
    ? 'Rank,Ticker,Score,Signal,Return3m(%),Return6m(%),Weight\n'
    : 'Rank,Ticker,Score,QualityScore,GARPScore,Weight\n'

  const lines = rows.map(r => {
    const d = r.signal_details
    if (isSpec) {
      return [r.rank, r.ticker, r.score.toFixed(1),
        d.signal ?? '', (d.return_3m ?? '').toString(),
        (d.return_6m ?? '').toString(), (d.weight ?? '').toString()].join(',')
    }
    return [r.rank, r.ticker, r.score.toFixed(1),
      (d.quality_score ?? '').toString(),
      (d.garp_score ?? '').toString(),
      (d.weight ?? '').toString()].join(',')
  }).join('\n')

  const blob = new Blob([header + lines], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = Object.assign(document.createElement('a'), {
    href:     url,
    download: `screener_${profile}_${runDate}.csv`,
  })
  a.click()
  URL.revokeObjectURL(url)
}

// ── Helpers ───────────────────────────────────────────────────────────

function fmtPct(v: number | null | undefined) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
}
function clrPct(v: number | null | undefined) {
  if (v == null) return 'text-slate-500'
  return v >= 0 ? 'text-green-400' : 'text-red-400'
}

// ── Subcomponents ─────────────────────────────────────────────────────

function FilterSidebar({
  minScore, onMinScore,
  signalFilter, onSignalFilter,
  sortKey, onSortKey,
  isSpec,
}: {
  minScore: number; onMinScore: (v: number) => void
  signalFilter: SignalFilter; onSignalFilter: (v: SignalFilter) => void
  sortKey: SortKey; onSortKey: (v: SortKey) => void
  isSpec: boolean
}) {
  return (
    <aside className="w-52 shrink-0 space-y-6">
      {/* Score slider */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Score minimo
        </p>
        <div className="flex items-center gap-2 mb-1">
          <input
            type="range"
            min={0} max={100} step={5}
            value={minScore}
            onChange={e => onMinScore(Number(e.target.value))}
            className="w-full accent-blue-500 cursor-pointer"
          />
        </div>
        <p className="text-right text-sm font-mono text-blue-400">{minScore}</p>
      </div>

      {/* Signal filter — speculative only */}
      {isSpec && (
        <div>
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
            Segnale
          </p>
          <div className="space-y-1.5">
            {(['all', 'long', 'flat'] as SignalFilter[]).map(opt => (
              <label key={opt} className="flex items-center gap-2 cursor-pointer group">
                <input
                  type="radio"
                  name="signal"
                  value={opt}
                  checked={signalFilter === opt}
                  onChange={() => onSignalFilter(opt)}
                  className="accent-blue-500"
                />
                <span className={clsx(
                  'text-sm capitalize',
                  signalFilter === opt ? 'text-slate-200' : 'text-slate-500 group-hover:text-slate-300',
                )}>
                  {opt === 'all' ? 'Tutti' : opt}
                </span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Sort */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Ordina per
        </p>
        <div className="space-y-1.5">
          {([
            { key: 'score',  label: 'Score' },
            { key: 'ticker', label: 'Ticker A→Z' },
            ...(isSpec ? [{ key: 'ret_3m', label: 'Return 3m' }] : []),
          ] as { key: SortKey; label: string }[]).map(opt => (
            <label key={opt.key} className="flex items-center gap-2 cursor-pointer group">
              <input
                type="radio"
                name="sort"
                value={opt.key}
                checked={sortKey === opt.key}
                onChange={() => onSortKey(opt.key)}
                className="accent-blue-500"
              />
              <span className={clsx(
                'text-sm',
                sortKey === opt.key ? 'text-slate-200' : 'text-slate-500 group-hover:text-slate-300',
              )}>
                {opt.label}
              </span>
            </label>
          ))}
        </div>
      </div>
    </aside>
  )
}

function ResultsTable({
  rows,
  runDate,
  isSpec,
}: {
  rows: ScreenerEntry[]
  runDate: string
  isSpec: boolean
}) {
  const navigate = useNavigate()

  if (rows.length === 0) {
    return (
      <div className="flex-1 rounded-lg border border-slate-800 bg-slate-900 flex items-center justify-center h-40">
        <p className="text-slate-500 text-sm">Nessun risultato con i filtri applicati</p>
      </div>
    )
  }

  return (
    <div className="flex-1 rounded-lg border border-slate-800 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 bg-slate-900">
            <th className="text-left px-4 py-3 text-slate-400 font-medium w-10">#</th>
            <th className="text-left px-4 py-3 text-slate-400 font-medium">Ticker</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Score</th>
            {isSpec ? (
              <>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Segnale</th>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Ret 3m</th>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Ret 6m</th>
              </>
            ) : (
              <>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Quality</th>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">GARP</th>
              </>
            )}
            <th className="text-right px-4 py-3 text-slate-400 font-medium">Peso</th>
            <th className="text-right px-4 py-3 text-slate-400 font-medium text-xs">
              {runDate}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const d = row.signal_details
            return (
              <tr
                key={row.ticker}
                onClick={() => navigate(`/ticker/${row.ticker}`)}
                className={clsx(
                  'border-b border-slate-800/50 cursor-pointer transition-colors hover:bg-slate-800/60',
                  i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900/30',
                )}
              >
                <td className="px-4 py-3 text-slate-600 font-mono text-xs">{row.rank}</td>
                <td className="px-4 py-3">
                  <span className="font-semibold text-white">{row.ticker}</span>
                </td>
                <td className="px-4 py-3 text-right font-mono text-slate-200">
                  {row.score.toFixed(1)}
                </td>

                {isSpec ? (
                  <>
                    <td className="px-4 py-3 text-right">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-900/50 text-green-400 border border-green-800/50">
                        {d.signal ?? 'long'}
                      </span>
                    </td>
                    <td className={clsx('px-4 py-3 text-right font-mono', clrPct(d.return_3m))}>
                      {fmtPct(d.return_3m)}
                    </td>
                    <td className={clsx('px-4 py-3 text-right font-mono', clrPct(d.return_6m))}>
                      {fmtPct(d.return_6m)}
                    </td>
                  </>
                ) : (
                  <>
                    <td className="px-4 py-3 text-right font-mono text-slate-300">
                      {d.quality_score != null ? d.quality_score.toFixed(1) : '—'}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-slate-300">
                      {d.garp_score != null ? d.garp_score.toFixed(1) : '—'}
                    </td>
                  </>
                )}

                <td className="px-4 py-3 text-right text-slate-400 font-mono text-xs">
                  {d.weight != null ? `${(d.weight * 100).toFixed(1)}%` : '—'}
                </td>
                <td className="px-4 py-3 text-right text-slate-600 text-xs">→</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────

export default function Screener() {
  const [activeTab, setActiveTab] = useState<ScreenerProfile>('speculative_trend_etf')
  const [minScore, setMinScore] = useState(0)
  const [signalFilter, setSignalFilter] = useState<SignalFilter>('all')
  const [sortKey, setSortKey] = useState<SortKey>('score')

  const [spec, setSpec]   = useState<ProfileData>({ shortlist: [], runDate: '—', loading: true, error: null })
  const [wealth, setWealth] = useState<ProfileData>({ shortlist: [], runDate: '—', loading: true, error: null })

  useEffect(() => {
    // Speculative
    getScreener('speculative_trend_etf')
      .then(r => setSpec({ shortlist: r.shortlist, runDate: r.run_date, loading: false, error: null }))
      .catch(() => setSpec({ shortlist: [], runDate: '—', loading: false, error: 'Backend non raggiungibile' }))

    // Wealth — 404 = nessun dato ancora, non è un errore bloccante
    getScreener('wealth_quality_garp')
      .then(r => setWealth({ shortlist: r.shortlist, runDate: r.run_date, loading: false, error: null }))
      .catch(err => {
        const is404 = err?.response?.status === 404
        setWealth({
          shortlist: [],
          runDate:   '—',
          loading:   false,
          error:     is404
            ? 'Nessun dato — esegui python -m app.analytics.screener'
            : 'Backend non raggiungibile',
        })
      })
  }, [])

  const isSpec    = activeTab === 'speculative_trend_etf'
  const active    = isSpec ? spec : wealth

  // Apply filters + sort
  const filtered = useMemo(() => {
    let rows = [...active.shortlist]

    if (minScore > 0) rows = rows.filter(r => r.score >= minScore)

    if (isSpec && signalFilter !== 'all') {
      rows = rows.filter(r => (r.signal_details.signal ?? 'long') === signalFilter)
    }

    if (sortKey === 'score')  rows.sort((a, b) => b.score - a.score)
    if (sortKey === 'ticker') rows.sort((a, b) => a.ticker.localeCompare(b.ticker))
    if (sortKey === 'ret_3m') rows.sort((a, b) => {
      const av = a.signal_details.return_3m ?? -Infinity
      const bv = b.signal_details.return_3m ?? -Infinity
      return bv - av
    })

    return rows
  }, [active.shortlist, minScore, signalFilter, sortKey, isSpec])

  return (
    <div className="space-y-6 max-w-6xl">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Screener</h1>
          <p className="text-slate-400 text-sm mt-0.5">
            {active.loading ? 'Caricamento...' : `${filtered.length} risultati · dati al ${active.runDate}`}
          </p>
        </div>

        {/* Export CSV */}
        {filtered.length > 0 && (
          <button
            onClick={() => exportCSV(filtered, activeTab, active.runDate)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-700 text-slate-300 text-sm hover:bg-slate-800 hover:text-white transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            Export CSV
          </button>
        )}
      </div>

      {/* Tab selector */}
      <div className="flex rounded-lg overflow-hidden border border-slate-700 w-fit">
        {([
          { id: 'speculative_trend_etf' as ScreenerProfile, label: 'Speculative', color: 'bg-blue-600' },
          { id: 'wealth_quality_garp'   as ScreenerProfile, label: 'Wealth',      color: 'bg-violet-600' },
        ]).map(tab => (
          <button
            key={tab.id}
            onClick={() => { setActiveTab(tab.id); setSortKey('score'); setSignalFilter('all') }}
            className={clsx(
              'px-5 py-2.5 text-sm font-medium transition-colors',
              activeTab === tab.id ? `${tab.color} text-white` : 'bg-slate-900 text-slate-400 hover:text-white',
            )}
          >
            {tab.label}
            {tab.id === 'speculative_trend_etf' && !spec.loading && (
              <span className="ml-2 text-xs opacity-70">{spec.shortlist.length}</span>
            )}
            {tab.id === 'wealth_quality_garp' && !wealth.loading && (
              <span className="ml-2 text-xs opacity-70">{wealth.shortlist.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* Error state */}
      {active.error && active.shortlist.length === 0 && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/20 px-5 py-4">
          <p className="text-amber-400 text-sm">{active.error}</p>
        </div>
      )}

      {/* Loading spinner */}
      {active.loading && (
        <div className="flex items-center justify-center h-40">
          <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* Filters + Table */}
      {!active.loading && active.shortlist.length > 0 && (
        <div className="flex gap-6">
          <FilterSidebar
            minScore={minScore}       onMinScore={setMinScore}
            signalFilter={signalFilter} onSignalFilter={setSignalFilter}
            sortKey={sortKey}         onSortKey={setSortKey}
            isSpec={isSpec}
          />
          <ResultsTable rows={filtered} runDate={active.runDate} isSpec={isSpec} />
        </div>
      )}

    </div>
  )
}
