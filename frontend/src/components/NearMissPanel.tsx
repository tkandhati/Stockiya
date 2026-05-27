import { CheckCircle2, XCircle } from 'lucide-react'
import type { NearMiss } from '../types'

/**
 * "Top contenders that didn't quite make it" — surfaced ONLY when picks is
 * empty. Compact section at the bottom of the picks page so the user can
 * verify the chain ran on real data and see which gate was the bottleneck.
 *
 * Quiet on purpose: small heading, tight cards, no calls to action.
 */
export function NearMissPanel({ items }: { items: NearMiss[] }) {
  if (!items || items.length === 0) return null

  return (
    <section className="mt-10 border-t border-slate-200 pt-6">
      <h3 className="text-sm font-semibold text-slate-700">
        Top {items.length} contenders today — close, but didn&apos;t clear every gate
      </h3>
      <p className="mt-1 text-xs text-slate-500">
        These tickers passed most of the chain. We surface them so you can
        verify the system filtered on real data, not because they&apos;re
        recommendations.
      </p>
      <ul className="mt-4 space-y-2">
        {items.map((nm) => (
          <li
            key={nm.symbol}
            className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-xs"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-sm font-semibold text-slate-900">
                  {nm.symbol}
                </span>
                <span className="text-slate-600">{nm.company}</span>
              </div>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">
                {nm.passed_count}/6 gates
              </span>
            </div>

            <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-[1fr_auto]">
              <ul className="space-y-0.5">
                {nm.passed_gates.map((g) => (
                  <li key={g.stage_id} className="flex items-start gap-1.5 text-slate-700">
                    <CheckCircle2 className="mt-0.5 h-3 w-3 flex-shrink-0 text-emerald-600" />
                    <span>
                      <span className="font-medium">{g.label}</span>
                      {g.evidence && g.evidence.length > 0 && (
                        <span className="text-slate-500"> — {g.evidence[0]}</span>
                      )}
                    </span>
                  </li>
                ))}
                <li className="flex items-start gap-1.5 text-rose-800">
                  <XCircle className="mt-0.5 h-3 w-3 flex-shrink-0 text-rose-600" />
                  <span>
                    <span className="font-medium">{nm.failed_gate.label}</span>
                    {nm.failed_gate.reason && (
                      <span className="text-rose-700/90"> — {nm.failed_gate.reason}</span>
                    )}
                  </span>
                </li>
              </ul>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
