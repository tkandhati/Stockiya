import { ShieldAlert, ShieldCheck } from 'lucide-react'
import type { Regime } from '../types'
import { fmtPct } from '../api'

/**
 * Top-of-page banner showing the day's market-regime decision.
 *
 *  - Green pill if NIFTY 100 closes above its 50d MA
 *  - Red banner if it halts — no buy alerts will issue today
 *
 * Reads PicksResponse.regime (optional — older cached files have no field).
 */
export function RegimeBanner({ regime }: { regime?: Regime }) {
  if (!regime) return null

  if (regime.passed) {
    return (
      <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-900">
        <ShieldCheck className="h-4 w-4 flex-shrink-0 text-emerald-700" />
        <span className="font-semibold">Regime ON</span>
        <span className="text-xs text-emerald-800">{regime.summary}</span>
        <span className="ml-auto flex flex-wrap items-center gap-3 font-mono text-[11px] text-emerald-700">
          {regime.checks.map((c) => (
            <span key={c.symbol}>
              {c.symbol}: {c.gap_pct != null ? fmtPct(c.gap_pct) : '—'}
            </span>
          ))}
        </span>
      </div>
    )
  }

  return (
    <div className="mt-4 rounded-xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-950">
      <div className="flex items-start gap-2">
        <ShieldAlert className="mt-0.5 h-5 w-5 flex-shrink-0 text-rose-700" />
        <div className="flex-1">
          <div className="font-bold text-rose-900">Regime HALTED</div>
          <p className="mt-0.5 text-rose-900/90">{regime.summary}</p>
          <ul className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
            {regime.checks.map((c) => (
              <li
                key={c.symbol}
                className={`flex items-center justify-between gap-3 rounded border px-2 py-1 ${
                  c.passed
                    ? 'border-emerald-200 bg-emerald-50/60'
                    : 'border-rose-200 bg-white'
                }`}
              >
                <span className="font-mono font-semibold">{c.symbol}</span>
                <span className="font-mono">{c.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
