import { Link } from 'react-router-dom'
import { Radar, Crosshair } from 'lucide-react'
import { fmtINR } from '../api'
import type { CoiledAccumulatorRow } from '../types'

/**
 * Coiled Accumulators — the "loaded spring" WATCH cohort.
 *
 * Bases that absorbed volume for a while, are STILL absorbing at the right edge,
 * and have NOT broken out yet (still sideways). This is a MONITOR section, never
 * a buy list — it surfaces names worth watching for the trigger, including
 * strong coils the entry-readiness router routed to the awareness bin as "late".
 *
 * Presentation-only: mirrors the backend (backend/coiled_accumulators.py). Never
 * touches selection/score/rank. Renders nothing when the cohort is empty, so it
 * cannot disturb the existing layout.
 */

const REBIN_META: Record<string, string> = {
  stale_base: 'was tagged stale',
  late_entry: 'was tagged late',
  extended_breakout: 'was tagged extended',
  timing_unclear: 'timing was unclear',
}

function pct(v?: number | null): string {
  return typeof v === 'number' && isFinite(v) ? `${v >= 0 ? '+' : ''}${v.toFixed(0)}%` : '—'
}

function mult(v?: number | null): string {
  return typeof v === 'number' && isFinite(v) ? `${v.toFixed(2)}x` : '—'
}

export function CoiledAccumulatorsPanel({ rows }: { rows: CoiledAccumulatorRow[] }) {
  if (!rows || rows.length === 0) return null

  return (
    <section className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50/60 p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-emerald-900">
          <Radar className="h-4 w-4" />
          Coiled Accumulators — loaded springs to watch ({rows.length})
        </h3>
        <span className="text-xs text-emerald-700">
          Still absorbing, not broken out yet
        </span>
      </div>

      <p className="mt-1 text-xs text-emerald-800">
        Bases that quietly accumulated volume and are <strong>still</strong>{' '}
        absorbing at the right edge, but price hasn&apos;t spiked yet. Watch for
        the trigger — not a buy recommendation.
      </p>

      <ul className="mt-3 space-y-2">
        {rows.map((r) => {
          const f = r.flow || {}
          const rebin =
            r.also_flagged && REBIN_META[r.also_flagged] ? REBIN_META[r.also_flagged] : null
          return (
            <li
              key={r.symbol}
              className="rounded-xl border border-emerald-200 bg-white p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/stock/${encodeURIComponent(r.symbol)}`}
                  className="font-mono text-sm font-semibold text-slate-900 hover:underline"
                >
                  {r.symbol}
                </Link>
                {r.company && (
                  <span className="truncate text-xs text-slate-500">{r.company}</span>
                )}
                {r.coil_age_days != null && (
                  <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-900">
                    coiling {r.coil_age_days}d
                  </span>
                )}
                {r.flow_strengthening && (
                  <span className="rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-white">
                    flow strengthening
                  </span>
                )}
                {r.also_pre_breakout && (
                  <span className="rounded bg-teal-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-teal-900">
                    ⚡ pre-breakout
                  </span>
                )}
                {r.source_section === 'awareness' && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-900">
                    re-surfaced{rebin ? ` · ${rebin}` : ''}
                  </span>
                )}
              </div>

              {/* Flow evidence chips */}
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600">
                <span>
                  OBV-90d <span className="font-medium text-slate-900">{pct(f.obv_90d_norm_slope_pct)}</span>
                </span>
                <span>
                  OBV-180d <span className="font-medium text-slate-900">{pct(f.obv_180d_norm_slope_pct)}</span>
                </span>
                <span>
                  up/down-90d <span className="font-medium text-slate-900">{mult(f.up_down_vol_ratio_90d)}</span>
                </span>
                <span>
                  right-edge <span className="font-medium text-slate-900">{mult(f.stealth_ratio)}</span>
                  {f.in_dryup ? ' (dry-up)' : ''}
                </span>
                {r.prior && r.prior.prior_appearances > 0 && (
                  <span>
                    seen{' '}
                    <span className="font-medium text-slate-900">
                      {r.prior.appearances_incl_today}x
                    </span>
                    {r.prior.first_seen ? ` since ${r.prior.first_seen}` : ''}
                  </span>
                )}
              </div>

              {/* Support point to enter on or before — the coil floor, tied to
                  the still-absorbing volume condition. */}
              {typeof r.support_point === 'number' && (
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-emerald-200 bg-emerald-50/80 px-2.5 py-1.5 text-xs text-emerald-900">
                  <span className="flex items-center gap-1 font-semibold">
                    <Crosshair className="h-3.5 w-3.5" />
                    Enter on / before {fmtINR(r.support_point)}
                  </span>
                  {r.support_basis && (
                    <span className="text-emerald-700">({r.support_basis})</span>
                  )}
                  {typeof r.entry_reference === 'number' && (
                    <span className="text-emerald-700">
                      don&apos;t chase above {fmtINR(r.entry_reference)}
                    </span>
                  )}
                </div>
              )}
              {r.volume_gate && (
                <p className="mt-1 text-[11px] italic text-emerald-800/90">
                  {r.volume_gate}
                </p>
              )}

              <p className="mt-1.5 text-xs text-slate-700">{r.why}</p>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
