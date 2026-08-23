import { Link } from 'react-router-dom'
import { EyeOff } from 'lucide-react'
import type { NotActionableRow } from '../types'

/**
 * Picks the engine surfaced but that are NOT enterable today — routed here so
 * the main list stays "buy this today" only. Shown for awareness, never as a
 * recommendation. Pattern-based (keyed on entry_timing), not stock-specific.
 */

const CATEGORY_META: Record<string, { label: string; badge: string }> = {
  late_entry: {
    label: 'Late entry — chasing',
    badge: 'bg-amber-100 text-amber-900',
  },
  extended_breakout: {
    label: 'Already broke out — extended',
    badge: 'bg-orange-100 text-orange-900',
  },
  distribution: {
    label: 'Distribution — avoid',
    badge: 'bg-rose-100 text-rose-900',
  },
  stale_base: {
    label: 'Stale base — recurs, goes nowhere',
    badge: 'bg-yellow-100 text-yellow-900',
  },
  timing_unclear: {
    label: 'Timing unclear',
    badge: 'bg-slate-100 text-slate-700',
  },
}

export function NotActionablePanel({ rows }: { rows: NotActionableRow[] }) {
  if (!rows || rows.length === 0) return null

  return (
    <section className="mt-6 rounded-2xl border border-amber-200 bg-amber-50/60 p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-900">
          <EyeOff className="h-4 w-4" />
          Not actionable today — for awareness ({rows.length})
        </h3>
        <span className="text-xs text-amber-700">
          Surfaced by the scan, but the timely entry has passed
        </span>
      </div>

      <p className="mt-1 text-xs text-amber-800">
        These cleared the scan but are late, already extended, or distributing —
        so they are not in today&apos;s buy list. Shown so you&apos;re aware. Not
        recommendations.
      </p>

      <ul className="mt-3 space-y-2">
        {rows.map((r) => {
          const cat = CATEGORY_META[r.reason.category] ?? CATEGORY_META.timing_unclear
          return (
            <li
              key={r.symbol}
              className="rounded-xl border border-amber-200 bg-white p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/stock/${encodeURIComponent(r.symbol)}`}
                  className="font-mono text-sm font-semibold text-slate-900 hover:underline"
                >
                  {r.symbol}
                </Link>
                {r.company && (
                  <span className="truncate text-xs text-slate-500">
                    {r.company}
                  </span>
                )}
                {r.rank != null && (
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-600">
                    scan rank {r.rank}
                  </span>
                )}
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${cat.badge}`}
                >
                  {cat.label} · {r.reason.entry_timing}
                </span>
              </div>
              <p className="mt-1.5 text-xs text-slate-700">{r.reason.why}</p>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
