import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ChevronDown, ChevronRight, Crosshair } from 'lucide-react'
import { fmtINR, fmtPct } from '../api'
import type {
  AccumStrength,
  AccumTrajectoryPoint,
  PickFollowupRow,
  PickFollowupStatus,
} from '../types'

/**
 * Pick Follow-up — the persistent "continuous eye on previous picks" tracker.
 *
 * A ranked TABLE of previous picks (the open portfolio cohort), sorted by
 * accumulation strength. Each row shows how volume accumulation has evolved
 * from the day we suggested it to today (not a static snapshot); expand a row to
 * reveal the precalculated day-by-day strength trajectory.
 *
 * Presentation-only: mirrors backend/pick_followup.py. Never touches selection/
 * score/rank/sizing/exits. Renders nothing when the cohort is empty.
 */

const STATUS_META: Record<PickFollowupStatus, { label: string; cls: string; hint: string }> = {
  coiling: {
    label: 'Coiling',
    cls: 'bg-emerald-100 text-emerald-900',
    hint: 'Volume still adding up while price consolidates below its expected target',
  },
  firing: {
    label: 'Firing',
    cls: 'bg-sky-100 text-sky-900',
    hint: 'Price has reached its expected target — the move delivered',
  },
  weakening: {
    label: 'Weakening',
    cls: 'bg-amber-100 text-amber-900',
    hint: 'Accumulation strength has faded',
  },
  broke_down: {
    label: 'Broke down',
    cls: 'bg-rose-100 text-rose-900',
    hint: 'Price fell through the base',
  },
  watch: {
    label: 'Watch',
    cls: 'bg-slate-100 text-slate-700',
    hint: 'Building — not yet conclusive',
  },
  'no-data': {
    label: 'No data',
    cls: 'bg-slate-100 text-slate-400',
    hint: 'No recent scan data to score',
  },
}

const CONS_LABEL: Record<string, string> = { small: 'tight', big: 'wide' }

function StrengthBar({ accum }: { accum?: AccumStrength | null }) {
  if (!accum || typeof accum.score !== 'number') {
    return <span className="text-xs text-slate-400">—</span>
  }
  const pct = Math.max(0, Math.min(100, accum.score))
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-20 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: accum.color }}
        />
      </div>
      <span className="tabular-nums text-xs font-semibold text-slate-800">{accum.score}</span>
      <span className="hidden text-[10px] font-medium uppercase tracking-wide text-slate-500 sm:inline">
        {accum.label}
      </span>
    </div>
  )
}

/** Compact SVG sparkline of the day-by-day accumulation-strength scores (0-100). */
function TrajectorySparkline({ points }: { points: AccumTrajectoryPoint[] }) {
  const scored = points.filter((p) => typeof p.score === 'number')
  if (scored.length < 2) {
    return <span className="text-xs text-slate-400">Not enough scan-days to chart</span>
  }
  const W = 260
  const H = 48
  const pad = 4
  const n = scored.length
  const x = (i: number) => pad + (i * (W - 2 * pad)) / (n - 1)
  const y = (s: number) => pad + (1 - s / 100) * (H - 2 * pad)
  const path = scored.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(p.score).toFixed(1)}`).join(' ')
  const lastColor = scored[scored.length - 1].color || '#6366f1'
  return (
    <svg width={W} height={H} className="overflow-visible">
      {/* healthy line at level-4 threshold (65) for reference */}
      <line x1={pad} y1={y(65)} x2={W - pad} y2={y(65)} stroke="#e2e8f0" strokeDasharray="2 3" />
      <path d={path} fill="none" stroke={lastColor} strokeWidth={2} strokeLinejoin="round" />
      {scored.map((p, i) => (
        <circle key={p.date} cx={x(i)} cy={y(p.score)} r={1.8} fill={p.color || lastColor} />
      ))}
    </svg>
  )
}

function ExpandedRow({ row }: { row: PickFollowupRow }) {
  const traj = row.trajectory || []
  const first = traj[0]
  const last = traj[traj.length - 1]
  return (
    <div className="grid gap-4 bg-slate-50 px-4 py-4 md:grid-cols-[auto_1fr]">
      <div>
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-700">
          <Activity className="h-3.5 w-3.5 text-indigo-600" />
          Accumulation strength since {row.suggested_date}
        </div>
        <div className="mt-2">
          <TrajectorySparkline points={traj} />
        </div>
        {first && last && (
          <div className="mt-1 flex gap-4 text-[11px] text-slate-500">
            <span>
              {first.date}: <span className="font-medium text-slate-700">{first.score}</span>
            </span>
            <span>
              {last.date}: <span className="font-medium text-slate-700">{last.score}</span>
            </span>
            {typeof row.strength_change === 'number' && (
              <span className={row.strength_change >= 0 ? 'text-emerald-700' : 'text-rose-700'}>
                {row.strength_change >= 0 ? '+' : ''}
                {row.strength_change} pts
              </span>
            )}
          </div>
        )}
      </div>
      <div className="text-xs text-slate-600">
        <p>{row.why}</p>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {typeof row.support1 === 'number' && (
            <span className="flex items-center gap-1">
              <Crosshair className="h-3 w-3 text-emerald-600" />
              Support 1 <span className="font-medium text-slate-900">{fmtINR(row.support1)}</span>
              <span className="text-slate-400">({row.support1_basis})</span>
            </span>
          )}
          {typeof row.support2 === 'number' && (
            <span className="flex items-center gap-1">
              <Crosshair className="h-3 w-3 text-rose-500" />
              Support 2 <span className="font-medium text-slate-900">{fmtINR(row.support2)}</span>
              <span className="text-slate-400">({row.support2_basis})</span>
            </span>
          )}
          {typeof row.entry_price === 'number' && (
            <span>
              Suggested at <span className="font-medium text-slate-900">{fmtINR(row.entry_price)}</span>
            </span>
          )}
          {typeof row.current_price === 'number' && (
            <span>
              Now <span className="font-medium text-slate-900">{fmtINR(row.current_price)}</span>
            </span>
          )}
          {typeof row.expected_price === 'number' && (
            <span>
              Expected target{' '}
              <span className="font-medium text-slate-900">{fmtINR(row.expected_price)}</span>
              {row.reached_expected === false && (
                <span className="ml-1 text-amber-700">(not reached — still consolidating)</span>
              )}
            </span>
          )}
          {row.volume_still_building && (
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-900">
              volume still building
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export function PickFollowupTable({
  rows,
  title = 'Follow-up on previous picks',
  subtitle = 'Continuous eye on what we suggested — ranked by accumulation strength',
}: {
  rows: PickFollowupRow[]
  title?: string
  subtitle?: string
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  if (!rows || rows.length === 0) return null

  const toggle = (sym: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(sym)) next.delete(sym)
      else next.add(sym)
      return next
    })

  return (
    <section className="mt-6 rounded-2xl border border-indigo-200 bg-white p-5 shadow-sm">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900">
          <Activity className="h-4 w-4 text-indigo-600" />
          {title} ({rows.length})
        </h3>
        <span className="hidden text-xs text-slate-500 sm:inline">{subtitle}</span>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-500">
              <th className="py-2 pr-3 font-medium">#</th>
              <th className="py-2 pr-3 font-medium">Symbol</th>
              <th className="py-2 pr-3 font-medium">Accumulation strength</th>
              <th className="py-2 pr-3 font-medium">Since suggested</th>
              <th className="py-2 pr-3 font-medium">Base</th>
              <th className="py-2 pr-3 font-medium">Support 1 / 2</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 pr-1 font-medium" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const meta = STATUS_META[r.status] ?? STATUS_META.watch
              const isOpen = open.has(r.symbol)
              const cons = r.consolidation || {}
              const consTxt =
                cons.size && CONS_LABEL[cons.size]
                  ? `${CONS_LABEL[cons.size]}${cons.range_pct != null ? ` · ${cons.range_pct}%` : ''}`
                  : '—'
              return (
                <Fragment key={r.symbol}>
                  <tr
                    className="cursor-pointer border-b border-slate-100 align-middle hover:bg-slate-50"
                    onClick={() => toggle(r.symbol)}
                  >
                    <td className="py-2.5 pr-3 tabular-nums text-slate-400">{i + 1}</td>
                    <td className="py-2.5 pr-3">
                      <Link
                        to={`/stock/${encodeURIComponent(r.symbol)}`}
                        onClick={(e) => e.stopPropagation()}
                        className="font-mono text-sm font-semibold text-slate-900 hover:underline"
                      >
                        {r.symbol.replace(/\.NS$/, '')}
                      </Link>
                      {r.company && (
                        <div className="max-w-[160px] truncate text-[11px] text-slate-400">
                          {r.company}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <StrengthBar accum={r.accum_now} />
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums">
                      {typeof r.price_change_pct === 'number' ? (
                        <span
                          className={
                            r.price_change_pct >= 0 ? 'text-emerald-700' : 'text-rose-700'
                          }
                        >
                          {fmtPct(r.price_change_pct)}
                        </span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                      <div className="text-[11px] text-slate-400">{r.days_tracked}d tracked</div>
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-slate-600">{consTxt}</td>
                    <td className="py-2.5 pr-3 text-xs tabular-nums text-slate-600">
                      {typeof r.support1 === 'number' ? fmtINR(r.support1) : '—'}
                      {' / '}
                      {typeof r.support2 === 'number' ? fmtINR(r.support2) : '—'}
                    </td>
                    <td className="py-2.5 pr-3">
                      <span
                        title={meta.hint}
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${meta.cls}`}
                      >
                        {meta.label}
                      </span>
                    </td>
                    <td className="py-2.5 pr-1 text-slate-400">
                      {isOpen ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={8} className="p-0">
                        <ExpandedRow row={r} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="mt-3 text-[11px] text-slate-400">
        Monitoring only — not a buy list. Accumulation strength is the volume/OBV
        gauge recomputed for each scan-day since we suggested the name. Click a row
        for its day-by-day trajectory.
      </p>
    </section>
  )
}
