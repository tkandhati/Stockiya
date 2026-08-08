import { Activity, ChartNoAxesCombined } from 'lucide-react'
import { NavLink } from 'react-router-dom'

const base =
  'flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition'

export function StrategyTabs() {
  return (
    <nav
      aria-label="Strategy tabs"
      className="mb-7 inline-flex rounded-xl border border-slate-200 bg-white p-1 shadow-sm"
    >
      <NavLink
        to="/"
        end
        className={({ isActive }) =>
          `${base} ${
            isActive
              ? 'bg-slate-900 text-white shadow-sm'
              : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
          }`
        }
      >
        <Activity className="h-4 w-4" />
        Volume strategy
      </NavLink>
      <NavLink
        to="/price-trend"
        className={({ isActive }) =>
          `${base} ${
            isActive
              ? 'bg-slate-900 text-white shadow-sm'
              : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
          }`
        }
      >
        <ChartNoAxesCombined className="h-4 w-4" />
        Price trend
      </NavLink>
    </nav>
  )
}
