import { Link } from 'react-router-dom'
import { Check, Radar, X } from 'lucide-react'
import type { BRSubCheck, ReadyToBreak } from '../types'

const VISIBLE_ROWS = 6

export function ReadyToBreakPanel({ items }: { items: ReadyToBreak[] }) {
  if (!items.length) return null
  const visible = items.slice(0, VISIBLE_ROWS)
  const hidden = items.length - visible.length

  return (
    <section className="mt-8 rounded-2xl border border-amber-200 bg-amber-50/40 p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-800">
            <Radar className="h-4 w-4 text-amber-600" />
            Setting up — close to breakout
          </h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-600">
            These names cleared every setup gate (long-term flow, base,
            volume/divergence). They haven't fired the breakout bar yet —
            the chips show what's still missing.
          </p>
        </div>
        <span className="rounded bg-white/80 px-2 py-1 font-mono text-xs text-slate-600">
          {items.length} watchlist
        </span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        {visible.map((row) => (
          <Link
            key={row.symbol}
            to={`/stock/${encodeURIComponent(row.symbol)}`}
            className="block rounded-xl border border-amber-200/70 bg-white p-4 transition hover:-translate-y-0.5 hover:border-amber-300 hover:shadow-sm"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-slate-900">
                  {row.company || row.symbol}
                </h3>
                <div className="mt-0.5 font-mono text-xs text-slate-500">
                  {row.symbol}
                </div>
              </div>
              <PassDots passing={row.br_passing} total={row.br_total} />
            </div>

            <ul className="mt-3 space-y-1.5">
              {row.br_checks.map((c) => (
                <CheckRow key={c.name} check={c} />
              ))}
            </ul>

            <div className="mt-3 flex items-center justify-between text-[11px] text-slate-600">
              <span className="font-mono">
                setup strength {Math.round(row.setup_strength * 100)}%
              </span>
              <span className="rounded bg-amber-100 px-2 py-0.5 font-mono font-semibold text-amber-900">
                {Math.round(row.closeness_score * 100)}% close
              </span>
            </div>
          </Link>
        ))}
      </div>

      {hidden > 0 && (
        <p className="mt-3 text-xs text-slate-500">
          {hidden} more on the watchlist; refine the picker or check trace audit
          to surface them.
        </p>
      )}
    </section>
  )
}

function PassDots({ passing, total }: { passing: number; total: number }) {
  return (
    <div className="flex flex-shrink-0 items-center gap-1.5">
      <span className="font-mono text-xs font-semibold text-slate-700">
        {passing}/{total}
      </span>
      <div className="flex gap-0.5">
        {Array.from({ length: total }).map((_, i) => (
          <span
            key={i}
            className={`h-1.5 w-1.5 rounded-full ${
              i < passing ? 'bg-emerald-500' : 'bg-amber-300'
            }`}
          />
        ))}
      </div>
    </div>
  )
}

function CheckRow({ check }: { check: BRSubCheck }) {
  const icon = check.passed ? (
    <Check className="h-3.5 w-3.5 flex-shrink-0 text-emerald-600" />
  ) : (
    <X className="h-3.5 w-3.5 flex-shrink-0 text-amber-600" />
  )
  return (
    <li className="flex items-start gap-2 text-xs leading-snug text-slate-700">
      {icon}
      <span className={check.passed ? 'text-slate-700' : 'text-slate-800'}>
        <span className="font-medium">{check.label}</span>
        {!check.passed && (
          <span className="ml-1 text-slate-600">— {check.gap_detail}</span>
        )}
      </span>
    </li>
  )
}
