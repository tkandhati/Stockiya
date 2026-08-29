import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ChevronDown, ChevronRight, Crosshair, Globe, Star, Zap } from 'lucide-react'
import { fmtINR, fmtPct } from '../api'
import type {
  AccumTrajectoryPoint,
  PickContext,
  PickFollowupRow,
  PickFollowupStatus,
  TractionLevel,
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

const TRACTION_META: Record<TractionLevel, { label: string; cls: string }> = {
  breaking_out: { label: '▲ Firing', cls: 'bg-sky-100 text-sky-900' },
  building: { label: 'Building', cls: 'bg-emerald-100 text-emerald-900' },
  early: { label: 'Early', cls: 'bg-amber-100 text-amber-900' },
  quiet: { label: 'Quiet', cls: 'bg-slate-100 text-slate-500' },
  unknown: { label: '—', cls: 'bg-transparent text-slate-300' },
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** '2026-07-21' -> '21 Jul' (no Date parsing, so no timezone drift). */
function shortDate(iso: string): string {
  const [, m, d] = iso.split('-')
  const mi = Number(m) - 1
  return `${Number(d)} ${MONTHS[mi] ?? m}`
}

/** Colour by coil quality value (not the saturating gauge): green=strong coil. */
function coilColor(score: number): string {
  if (score >= 70) return '#059669' // emerald — strong coil
  if (score >= 45) return '#f59e0b' // amber — moderate
  return '#94a3b8' // slate — weak
}

/** Coil-quality bar (0-100, continuous). Replaces the pegged gauge score. */
function CoilBar({ score }: { score?: number | null }) {
  if (typeof score !== 'number') {
    return <span className="text-xs text-slate-400">—</span>
  }
  const pct = Math.max(0, Math.min(100, score))
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-20 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: coilColor(score) }}
        />
      </div>
      <span className="tabular-nums text-xs font-semibold text-slate-800">{score}</span>
    </div>
  )
}

/**
 * Volume-accumulation vs price since the recommend date. The indigo line is the
 * CONTINUOUS OBV-90d accumulation figure (each dot labelled with its value +
 * date, plus a hover tooltip); the faint grey line is price indexed to 100 at
 * the suggestion day. When volume rises while price stays flat, the two lines
 * diverge — that is the coil. Each series is auto-scaled to its own range so the
 * shape is visible (this is a divergence view, not an absolute-level chart).
 * Scrolls horizontally when there are many scan-days so labels never overlap.
 */
function TrajectoryChart({ points }: { points: AccumTrajectoryPoint[] }) {
  const pts = points.filter((p) => typeof p.obv90 === 'number')
  if (pts.length < 1) {
    return <span className="text-xs text-slate-400">Not enough scan-days to chart</span>
  }
  const SP = 52 // px between dots
  const PAD_X = 30
  const PAD_TOP = 20
  const CHART_H = 74
  const PAD_BOTTOM = 34
  const W = PAD_X * 2 + Math.max(1, pts.length - 1) * SP
  const H = PAD_TOP + CHART_H + PAD_BOTTOM
  const x = (i: number) => PAD_X + i * SP

  // Volume (OBV-90d %) — auto-scaled to its own min/max.
  const vVals = pts.map((p) => p.obv90 as number)
  const vMin = Math.min(...vVals)
  const vMax = Math.max(...vVals)
  const vRange = vMax - vMin || 1
  const yV = (v: number) => PAD_TOP + (1 - (v - vMin) / vRange) * CHART_H

  // Price indexed to 100 at the first available close — auto-scaled.
  const base = pts.find((p) => typeof p.close === 'number')?.close ?? null
  const pIdx = pts.map((p) =>
    typeof p.close === 'number' && base ? (p.close / base) * 100 : null,
  )
  const pClean = pIdx.filter((v): v is number => v != null)
  const pMin = pClean.length ? Math.min(...pClean) : 100
  const pMax = pClean.length ? Math.max(...pClean) : 100
  const pRange = pMax - pMin || 1
  const yP = (v: number) => PAD_TOP + (1 - (v - pMin) / pRange) * CHART_H

  const volPath = pts
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${yV(p.obv90 as number).toFixed(1)}`)
    .join(' ')
  let pricePath = ''
  pIdx.forEach((v, i) => {
    if (v == null) return
    pricePath += `${pricePath ? 'L' : 'M'} ${x(i).toFixed(1)} ${yP(v).toFixed(1)} `
  })
  const yLabels = PAD_TOP + CHART_H + 12

  return (
    <div>
      <div className="mb-1 flex items-center gap-4 text-[10px] text-slate-500">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-indigo-500" /> volume (OBV-90d %)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-[2px] w-4 bg-slate-400" /> price (indexed to 100)
        </span>
      </div>
      <div className="max-w-full overflow-x-auto">
        <svg width={W} height={H} className="block">
          {/* price line — faint, no labels (context for the divergence) */}
          {pricePath && <path d={pricePath.trim()} fill="none" stroke="#94a3b8" strokeWidth={1.5} />}
          {/* volume line — primary */}
          {pts.length >= 2 && (
            <path d={volPath} fill="none" stroke="#6366f1" strokeWidth={2} strokeLinejoin="round" />
          )}
          {pts.map((p, i) => {
            const priceIdx = pIdx[i]
            return (
              <g key={p.date}>
                <circle cx={x(i)} cy={yV(p.obv90 as number)} r={3} fill="#6366f1">
                  <title>
                    {`${p.date} · OBV-90d ${p.obv90}%` +
                      (priceIdx != null ? ` · price ${priceIdx.toFixed(1)}` : '')}
                  </title>
                </circle>
                <text
                  x={x(i)}
                  y={yV(p.obv90 as number) - 7}
                  textAnchor="middle"
                  fontSize="10"
                  fontWeight={600}
                  fill="#312e81"
                >
                  {Math.round(p.obv90 as number)}
                </text>
                <text
                  x={x(i)}
                  y={yLabels}
                  textAnchor="end"
                  fontSize="9"
                  fill="#64748b"
                  transform={`rotate(-40 ${x(i)} ${yLabels})`}
                >
                  {shortDate(p.date)}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}

const CHIP = 'rounded px-1.5 py-0.5 text-[10px] font-medium'

/** Scoring-neutral conviction context — US linkage, leadership, sector, export. */
function ContextBlock({ context }: { context?: PickContext | null }) {
  if (!context) return null
  const us = context.us
  const lead = context.leadership
  const exp = context.export
  const hasUS = !!us && (typeof us.sp500_corr === 'number' || !!us.regime)
  if (!hasUS && !lead && !context.sector) return null

  const regimeCls =
    us?.regime === 'tailwind'
      ? 'bg-emerald-100 text-emerald-900'
      : us?.regime === 'headwind'
      ? 'bg-rose-100 text-rose-900'
      : 'bg-slate-100 text-slate-600'
  const leadCls =
    lead?.label === 'leader'
      ? 'bg-emerald-100 text-emerald-900'
      : lead?.label === 'laggard'
      ? 'bg-rose-100 text-rose-900'
      : 'bg-slate-100 text-slate-600'
  const expCls =
    exp?.exposure === 'high'
      ? 'bg-indigo-100 text-indigo-900'
      : exp?.exposure === 'medium'
      ? 'bg-amber-100 text-amber-900'
      : 'bg-slate-100 text-slate-500'

  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
      <div className="flex items-center gap-1.5 font-semibold text-slate-700">
        <Globe className="h-3.5 w-3.5 text-slate-500" />
        Conviction context{' '}
        <span className="font-normal text-slate-400">· doesn&apos;t change the ranking</span>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {context.sector && (
          <span className={`${CHIP} bg-slate-100 text-slate-700`}>Sector: {context.sector}</span>
        )}
        {exp && exp.exposure !== 'unknown' && (
          <span className={`${CHIP} ${expCls}`}>Export: {exp.exposure}</span>
        )}
        {lead && (
          <span className={`${CHIP} ${leadCls}`}>
            {lead.label === 'leader' ? 'Leader' : lead.label === 'laggard' ? 'Laggard' : 'In-line'} vs
            Nifty {lead.rel_pct >= 0 ? '+' : ''}
            {lead.rel_pct}%
          </span>
        )}
        {us?.regime && <span className={`${CHIP} ${regimeCls}`}>US {us.regime}</span>}
        {us && typeof us.sp500_corr === 'number' && (
          <span className={`${CHIP} bg-slate-100 text-slate-700`}>S&amp;P corr {us.sp500_corr}</span>
        )}
      </div>
      {us?.note && <p className="mt-1 text-[11px] text-slate-500">{us.note}</p>}
    </div>
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
          Volume accumulation (OBV-90d) vs price since {row.suggested_date}
        </div>
        <div className="mt-2">
          <TrajectoryChart points={traj} />
        </div>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
          {typeof row.coil_score === 'number' && (
            <span>
              coil quality <span className="font-semibold text-slate-800">{row.coil_score}</span>/100
            </span>
          )}
          {typeof row.volume_add === 'number' && (
            <span>
              volume-add <span className="font-medium text-slate-700">{Math.round(row.volume_add * 100)}%</span>
            </span>
          )}
          {typeof row.price_stillness === 'number' && (
            <span>
              price-stillness <span className="font-medium text-slate-700">{Math.round(row.price_stillness * 100)}%</span>
            </span>
          )}
          {first && last && typeof first.obv90 === 'number' && typeof last.obv90 === 'number' && (
            <span>
              OBV-90d {first.obv90} → <span className="font-medium text-slate-700">{last.obv90}</span>
            </span>
          )}
        </div>
      </div>
      <div className="text-xs text-slate-600">
        <p>{row.why}</p>

        {/* Traction — leading clues that the coil is starting to fire. */}
        {row.traction && row.traction.level !== 'unknown' && (
          <div className="mt-2 rounded-lg border border-indigo-100 bg-indigo-50/50 px-3 py-2">
            <div className="flex items-center gap-1.5 font-semibold text-indigo-900">
              <Zap className="h-3.5 w-3.5" />
              Traction — clues to watch for the trigger
            </div>
            <p className="mt-0.5 text-slate-700">{row.traction.note}</p>
            {row.traction.clues.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-slate-600">
                {row.traction.clues.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            )}
          </div>
        )}

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

        <ContextBlock context={row.context} />
      </div>
    </div>
  )
}

export function PickFollowupTable({
  rows,
  title = 'Follow-up on previous picks',
  subtitle = 'Ranked by coil quality — volume still adding + price barely moved on top',
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
              <th className="py-2 pr-3 font-medium" title="Volume still adding x price barely moved (0-100)">
                Coil quality
              </th>
              <th className="py-2 pr-3 font-medium" title="Leading clues the coil is starting to fire">
                Traction
              </th>
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
                    className={`cursor-pointer border-b border-slate-100 align-middle hover:bg-slate-50 ${
                      r.is_top_pick ? 'bg-emerald-50/60' : ''
                    }`}
                    onClick={() => toggle(r.symbol)}
                  >
                    <td className="py-2.5 pr-3 tabular-nums text-slate-400">{i + 1}</td>
                    <td className="py-2.5 pr-3">
                      <div className="flex items-center gap-1.5">
                        {r.is_top_pick && (
                          <span
                            title="Best coil to act on — strongest volume-add with the flattest price"
                            className="flex items-center gap-0.5 rounded bg-emerald-600 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wide text-white"
                          >
                            <Star className="h-2.5 w-2.5" fill="currentColor" />
                            Best
                          </span>
                        )}
                        <Link
                          to={`/stock/${encodeURIComponent(r.symbol)}`}
                          onClick={(e) => e.stopPropagation()}
                          className="font-mono text-sm font-semibold text-slate-900 hover:underline"
                        >
                          {r.symbol.replace(/\.NS$/, '')}
                        </Link>
                      </div>
                      {r.company && (
                        <div className="max-w-[160px] truncate text-[11px] text-slate-400">
                          {r.company}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <CoilBar score={r.coil_score} />
                    </td>
                    <td className="py-2.5 pr-3">
                      {(() => {
                        const t = r.traction
                        const tm = TRACTION_META[t?.level ?? 'unknown']
                        if (!t || t.level === 'unknown') {
                          return <span className="text-xs text-slate-300">—</span>
                        }
                        return (
                          <span
                            title={t.note}
                            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tm.cls}`}
                          >
                            {tm.label}
                            {t.level !== 'breaking_out' &&
                              typeof t.distance_to_pivot_pct === 'number' &&
                              t.distance_to_pivot_pct > 0 && (
                                <span className="font-normal normal-case opacity-80">
                                  {t.distance_to_pivot_pct}% to pivot
                                </span>
                              )}
                          </span>
                        )
                      })()}
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
                      <td colSpan={9} className="p-0">
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
        Monitoring only — not a buy list. <strong>Coil quality</strong> blends how
        strongly volume is still accumulating with how little price has moved since
        we suggested it, so the tightest coils rank first and{' '}
        <span className="font-semibold text-emerald-700">★ Best</span> flags the one
        to focus on. Click a row for its day-by-day volume-vs-price trajectory.
      </p>
    </section>
  )
}
