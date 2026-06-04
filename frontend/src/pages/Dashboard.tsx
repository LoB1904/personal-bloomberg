import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { getScreener, getTrackRecord } from '../lib/api'
import type {
  ScreenerResponse,
  TrackRecordResponse,
  ScreenerProfile,
} from '../lib/types'

// ── Mock data (usato se backend non risponde) ─────────────────────────

const MOCK_SCREENER_SPEC: ScreenerResponse = {
  profile: 'speculative_trend_etf',
  run_date: '2026-06-04',
  n_signals: 5,
  shortlist: [
    { rank: 1, ticker: 'EEM',  score: 100.0, signal_details: { return_3m: 11.5,  weight: 0.2, signal: 'long' } },
    { rank: 2, ticker: 'XLK',  score: 85.0,  signal_details: { return_3m: 36.9,  weight: 0.2, signal: 'long' } },
    { rank: 3, ticker: 'DBC',  score: 85.0,  signal_details: { return_3m: 14.2,  weight: 0.2, signal: 'long' } },
    { rank: 4, ticker: 'XLRE', score: 58.6,  signal_details: { return_3m: 0.2,   weight: 0.2, signal: 'long' } },
    { rank: 5, ticker: 'XLY',  score: 53.0,  signal_details: { return_3m: 4.7,   weight: 0.2, signal: 'long' } },
  ],
}

const MOCK_SCREENER_WEALTH: ScreenerResponse = {
  profile: 'wealth_quality_garp',
  run_date: '2026-06-04',
  n_signals: 0,
  shortlist: [],
}

const MOCK_TRACK_RECORD: TrackRecordResponse = {
  screener: {
    speculative_trend_etf: { n_signals: 5, n_closed: 0, windows: { '1w': { avg_pnl: null, hit_rate: null, n: 0 } } },
    wealth_quality_garp:   { n_signals: 0, n_closed: 0, windows: { '1w': { avg_pnl: null, hit_rate: null, n: 0 } } },
  },
  baselines: {
    baseline_bh_vwce:       { start_date: '2019-07-29', end_date: '2026-05-27', total_return: 125.59, cagr: 12.65, max_drawdown: -33.41, sharpe: 0.812 },
    baseline_dual_momentum: { start_date: '2017-06-01', end_date: '2026-05-27', total_return: 135.74, cagr: 10.01, max_drawdown: -33.72, sharpe: 0.65  },
    baseline_ew_sp100:      { start_date: '2016-05-31', end_date: '2026-05-27', total_return: 381.66, cagr: 17.05, max_drawdown: -34.05, sharpe: 0.997 },
  },
}

// ── Sector heatmap data ───────────────────────────────────────────────

interface Sector { name: string; abbr: string; ret: number }

const SECTORS: Sector[] = [
  { name: 'Technology',       abbr: 'XLK',  ret:  2.4  },
  { name: 'Healthcare',       abbr: 'XLV',  ret:  0.8  },
  { name: 'Financials',       abbr: 'XLF',  ret:  1.5  },
  { name: 'Comm. Services',   abbr: 'XLC',  ret:  3.1  },
  { name: 'Consumer Disc.',   abbr: 'XLY',  ret: -1.2  },
  { name: 'Industrials',      abbr: 'XLI',  ret:  0.6  },
  { name: 'Consumer Staples', abbr: 'XLP',  ret: -0.3  },
  { name: 'Energy',           abbr: 'XLE',  ret: -2.1  },
  { name: 'Utilities',        abbr: 'XLU',  ret:  0.4  },
  { name: 'Real Estate',      abbr: 'XLRE', ret: -0.9  },
  { name: 'Materials',        abbr: 'XLB',  ret:  1.1  },
]

// ── Helpers ───────────────────────────────────────────────────────────

const LS_KEY = 'dashboard_profile_toggle'

function readStoredProfile(): ScreenerProfile {
  try {
    const v = localStorage.getItem(LS_KEY)
    if (v === 'speculative_trend_etf' || v === 'wealth_quality_garp') return v
  } catch { /* ignore */ }
  return 'speculative_trend_etf'
}

function fmtPct(v: number | null | undefined, decimals = 2): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`
}

function colorPct(v: number | null | undefined): string {
  if (v == null) return 'text-slate-400'
  return v > 0 ? 'text-green-400' : 'text-red-400'
}

// Colore cella heatmap: intensità proporzionale al valore, ±3% = saturazione max
function sectorBg(ret: number): string {
  const abs = Math.min(Math.abs(ret) / 3, 1)
  if (ret > 0) {
    // Verde: da slate-900 (0) a green-900 (1)
    if (abs < 0.33) return 'bg-green-950 text-green-400'
    if (abs < 0.67) return 'bg-green-900 text-green-300'
    return 'bg-green-800 text-green-200'
  }
  if (abs < 0.33) return 'bg-red-950 text-red-400'
  if (abs < 0.67) return 'bg-red-900 text-red-300'
  return 'bg-red-800 text-red-200'
}

// ── Subcomponents ─────────────────────────────────────────────────────

function MetricCard({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string
  value: string
  sub?: string
  valueClass?: string
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 px-5 py-4">
      <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={clsx('text-2xl font-bold font-mono', valueClass ?? 'text-slate-100')}>
        {value}
      </p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  )
}

function Spinner() {
  return (
    <div className="flex items-center justify-center h-40 text-slate-500">
      <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate()

  const [profile, setProfile] = useState<ScreenerProfile>(readStoredProfile)
  const [screener, setScreener] = useState<ScreenerResponse | null>(null)
  const [trackRecord, setTrackRecord] = useState<TrackRecordResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [usingMock, setUsingMock] = useState(false)
  const [expanded, setExpanded] = useState(false)

  // Fetch on profile change
  useEffect(() => {
    let cancelled = false
    setLoading(true)

    Promise.all([getScreener(profile), getTrackRecord()])
      .then(([sc, tr]) => {
        if (cancelled) return
        setScreener(sc)
        setTrackRecord(tr)
        setUsingMock(false)
      })
      .catch(() => {
        if (cancelled) return
        setScreener(profile === 'speculative_trend_etf' ? MOCK_SCREENER_SPEC : MOCK_SCREENER_WEALTH)
        setTrackRecord(MOCK_TRACK_RECORD)
        setUsingMock(true)
      })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [profile])

  function switchProfile(p: ScreenerProfile) {
    setProfile(p)
    try { localStorage.setItem(LS_KEY, p) } catch { /* ignore */ }
    setExpanded(false)
  }

  // Derived: metriche per MetricCards
  const vwce    = trackRecord?.baselines['baseline_bh_vwce']
  const bestSharpe = trackRecord
    ? Math.max(...Object.values(trackRecord.baselines).map(b => b.sharpe ?? -Infinity))
    : null
  const bestSharpeVal = bestSharpe != null && isFinite(bestSharpe) ? bestSharpe : null

  const specCagr  = null  // screener non ha ancora CAGR giornaliero
  const wealthCagr = null
  const vsVwce    = null  // nessun dato screener vs benchmark ancora

  const shortlist = screener?.shortlist ?? []
  const visible   = expanded ? shortlist : shortlist.slice(0, 10)

  return (
    <div className="space-y-8 max-w-5xl">

      {/* Header + toggle */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-slate-400 text-sm mt-0.5">
            {screener?.run_date
              ? `Dati al ${screener.run_date}`
              : 'Caricamento...'}
            {usingMock && (
              <span className="ml-2 text-xs text-amber-500 border border-amber-800 rounded px-1.5 py-0.5">
                mock — backend offline
              </span>
            )}
          </p>
        </div>

        {/* Profile toggle */}
        <div className="flex rounded-lg overflow-hidden border border-slate-700">
          {(['speculative_trend_etf', 'wealth_quality_garp'] as ScreenerProfile[]).map(p => (
            <button
              key={p}
              onClick={() => switchProfile(p)}
              className={clsx(
                'px-4 py-2 text-sm font-medium transition-colors',
                profile === p
                  ? p === 'speculative_trend_etf'
                    ? 'bg-blue-600 text-white'
                    : 'bg-violet-600 text-white'
                  : 'bg-slate-900 text-slate-400 hover:text-white',
              )}
            >
              {p === 'speculative_trend_etf' ? 'Speculative' : 'Wealth'}
            </button>
          ))}
        </div>
      </div>

      {/* Performance snapshot */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Performance snapshot
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard
            label="Speculative CAGR"
            value={specCagr != null ? fmtPct(specCagr) : '—'}
            sub="dati insufficienti"
            valueClass={specCagr != null ? colorPct(specCagr) : 'text-slate-600'}
          />
          <MetricCard
            label="Wealth CAGR"
            value={wealthCagr != null ? fmtPct(wealthCagr) : '—'}
            sub="dati insufficienti"
            valueClass={wealthCagr != null ? colorPct(wealthCagr) : 'text-slate-600'}
          />
          <MetricCard
            label="vs VWCE alpha"
            value={vsVwce != null ? fmtPct(vsVwce) : '—'}
            sub={vwce ? `VWCE CAGR ${fmtPct(vwce.cagr)}` : undefined}
            valueClass={vsVwce != null ? colorPct(vsVwce) : 'text-slate-600'}
          />
          <MetricCard
            label="Sharpe migliore"
            value={bestSharpeVal != null ? bestSharpeVal.toFixed(3) : '—'}
            sub="tra le baseline"
            valueClass={bestSharpeVal != null ? 'text-green-400' : 'text-slate-600'}
          />
        </div>
      </section>

      {/* Shortlist table */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Shortlist — {profile === 'speculative_trend_etf' ? 'Speculative (Trend ETF)' : 'Wealth (Quality GARP)'}
        </h2>

        {loading ? (
          <Spinner />
        ) : shortlist.length === 0 ? (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-6 text-center">
            <p className="text-slate-400">
              {profile === 'wealth_quality_garp'
                ? 'Nessun segnale Wealth — esegui python -m app.analytics.screener'
                : 'Nessun ETF in trend'}
            </p>
          </div>
        ) : (
          <>
            <div className="rounded-lg border border-slate-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800 bg-slate-900">
                    <th className="text-left px-4 py-3 text-slate-400 font-medium w-12">#</th>
                    <th className="text-left px-4 py-3 text-slate-400 font-medium">Ticker</th>
                    <th className="text-right px-4 py-3 text-slate-400 font-medium">Score</th>
                    <th className="text-right px-4 py-3 text-slate-400 font-medium">Ret 3m</th>
                    <th className="text-right px-4 py-3 text-slate-400 font-medium">Peso</th>
                    <th className="text-right px-4 py-3 text-slate-400 font-medium">Segnale</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row, i) => (
                    <tr
                      key={row.ticker}
                      onClick={() => navigate(`/ticker/${row.ticker}`)}
                      className={clsx(
                        'border-b border-slate-800/50 cursor-pointer transition-colors',
                        'hover:bg-slate-800/60',
                        i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900/30',
                      )}
                    >
                      <td className="px-4 py-3 text-slate-500 font-mono">{row.rank}</td>
                      <td className="px-4 py-3">
                        <span className="font-semibold text-white">{row.ticker}</span>
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-slate-200">
                        {row.score.toFixed(1)}
                      </td>
                      <td className={clsx(
                        'px-4 py-3 text-right font-mono',
                        colorPct(row.signal_details.return_3m),
                      )}>
                        {row.signal_details.return_3m != null
                          ? fmtPct(row.signal_details.return_3m)
                          : '—'}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-400 font-mono">
                        {row.signal_details.weight != null
                          ? `${(row.signal_details.weight * 100).toFixed(1)}%`
                          : '—'}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-900/50 text-green-400 border border-green-800/50">
                          {row.signal_details.signal ?? 'long'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {shortlist.length > 10 && (
              <button
                onClick={() => setExpanded(e => !e)}
                className="mt-2 text-xs text-slate-500 hover:text-slate-300 transition-colors"
              >
                {expanded
                  ? '↑ Collassa'
                  : `↓ Mostra altri ${shortlist.length - 10} ticker`}
              </button>
            )}
          </>
        )}
      </section>

      {/* Sector heatmap */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Heatmap settoriale S&P 500
          <span className="ml-2 text-xs font-normal text-slate-600 normal-case tracking-normal">
            dati simulati — MTD %
          </span>
        </h2>
        <div className="grid grid-cols-4 gap-2">
          {SECTORS.map(s => (
            <div
              key={s.abbr}
              className={clsx(
                'rounded-lg p-3 flex flex-col justify-between min-h-[72px] border border-black/20',
                sectorBg(s.ret),
              )}
            >
              <p className="text-xs font-medium leading-tight opacity-80">{s.name}</p>
              <div className="flex items-end justify-between mt-2">
                <span className="text-xs opacity-50">{s.abbr}</span>
                <span className="text-lg font-bold font-mono leading-none">
                  {s.ret >= 0 ? '+' : ''}{s.ret.toFixed(1)}%
                </span>
              </div>
            </div>
          ))}

          {/* Cella placeholder per completare la griglia 4×3 */}
          <div className="rounded-lg border border-slate-800 bg-slate-900/30 min-h-[72px] flex items-center justify-center">
            <span className="text-slate-700 text-xs">—</span>
          </div>
        </div>
      </section>

    </div>
  )
}
