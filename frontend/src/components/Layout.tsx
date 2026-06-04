import { NavLink, Outlet } from 'react-router-dom'
import clsx from 'clsx'

const NAV_ITEMS = [
  { to: '/',             label: 'Dashboard' },
  { to: '/screener',     label: 'Screener' },
  { to: '/track-record', label: 'Track Record' },
  { to: '/portfolio',    label: 'Portfolio' },
] as const

export default function Layout() {
  return (
    <div className="flex h-screen bg-slate-950 text-slate-100">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 bg-black border-r border-slate-800 flex flex-col">
        <div className="px-5 py-6 border-b border-slate-800">
          <span className="text-sm font-semibold tracking-widest text-slate-400 uppercase">
            Bloomberg
          </span>
          <p className="text-xs text-slate-600 mt-0.5">Personal</p>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV_ITEMS.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                clsx(
                  'block px-3 py-2 rounded text-sm transition-colors',
                  isActive
                    ? 'bg-slate-800 text-white font-medium'
                    : 'text-slate-400 hover:text-white hover:bg-slate-900',
                )
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="px-5 py-4 border-t border-slate-800">
          <p className="text-xs text-slate-600">Fase 3 — UI</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto p-8">
        <Outlet />
      </main>
    </div>
  )
}
