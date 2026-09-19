import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ChevronDown,
  ChevronRight,
  Crosshair,
  Target,
  TrendingUp,
} from 'lucide-react'
import { fmtINR } from '../api'
import type { PullbackSetupRow } from '../types'

/**
 * Section 2 — Pullback Re-Entry Setups.
 *
 * The impulse -> volume-dry-up (VDU) pullback -> breakout re-entry strategy,
 * evaluated on PREVIOUS picks that still show persisted interest. A ranked table
 * (actionable first). Expand a row for the interest reasons + full notes.
 *
 * Presentation-only: mirrors backend/pullback_setups.py. Renders nothing when
 * empty.
 */

const STATUS_META: Record<
  PullbackSetupRow['status'],
  { label: string; cls: string; hint: string }
> = {
  buy_trigger: {
    label: 'Buy trigger',
    cls: 'bg-emerald-600 text-white',
    hint: 'Broke the prior bar high on expanding volume after a VDU pullback — entry/stop/target set',
  },
  vdu_confirmed_watch_trigger: {
    label: 'VDU · watch trigger',
    cls: 'bg-emerald-100 text-emerald-900',
    hint: 'Volume dried up on the dip and support held — buy on a close above the prior high on rising volume',
  },
  in_pullback_vdu_pending: {
    label: 'In pullback',
    cls: 'bg-amber-100 text-amber-900',
    hint: 'Pulling back after the impulse — waiting for a volume-dry-up down-day / support confirmation',
  },
  awaiting_pullback: {
    label: 'Awaiting pullback',
    cls: 'bg-sky-100 text-sky-900',
    hint: 'Impulse day is in; needs 2–5 sessions of pullback before a re-entry can set up',
  },
  no_impulse: {
    label: 'No impulse',
    cls: 'bg-slate-100 text-slate-600',
    hint: 'Interest is holding, but no recent +2% / 1.5×ADV50 spark to build a setup from',
  },
  below_50sma: {
    label: 'Below trend',
    cls: 'bg-slate-100 text-slate-500',
    hint: 'Price is below its 50-day trend — no re-entry',
  },
  invalidated: {
    label: 'Invalidated',
    cls: 'bg-rose-100 text-rose-900',
    hint: 'A heavy-volume down-day or a close below the Day-0 low broke the setup',
  },
  insufficient_history: {
    label: 'No data',
    cls: 'bg-slate-100 text-slate-400',
    hint: 'Not enough candles to evaluate',
  },
  interest_faded: {
    label: 'Interest faded',
    cls: 'bg-slate-100 text-slate-400',
    hint: 'Accumulation no longer persisting (shown only with STOCKYA_PULLBACK_SHOW_FADED=1)',
  },
}

const CHIP = 'rounded px-1.5 py-0.5 text-[10px] font-medium'

function InterestChips({ interest }: { interest: PullbackSetupRow['interest'] }) {
  const obvOk = typeof interest.obv90_slope === 'number' && interest.obv90_slope >= 0
  const udOk = typeof interest.ud_ratio_90 === 'number' && interest.ud_ratio_90 >= 1
  return (
    <div className="flex flex-wrap gap-1">
      <span
        className={`${CHIP} ${obvOk ? 'bg-emerald-100 text-emerald-900' : 'bg-slate-100 text-slate-500'}`}
        title="OBV-90d normalized slope — on-balance volume still net-rising through the consolidation"
      >
        OBV{typeof interest.obv90_slope === 'number' ? ` ${interest.obv90_slope >= 0 ? '+' : ''}${Math.round(interest.obv90_slope)}%` : ' n/a'}
      </span>
      <span
        className={`${CHIP} ${udOk ? 'bg-emerald-100 text-emerald-900' : 'bg-slate-100 text-slate-500'}`}
        title="Up/down volume ratio over 90 days — buyers vs sellers"
      >
        U/D{typeof interest.ud_ratio_90 === 'number' ? ` ${interest.ud_ratio_90.toFixed(2)}` : ' n/a'}
      </span>
      {typeof interest.delivery_pct === 'number' && (
        <span className={`${CHIP} bg-slate-100 text-slate-600`} title="Latest NSE delivery %">
          Del {Math.round(interest.delivery_pct)}%
        </span>
      )}
    </div>
  )
}

function ExpandedRow({ row }: { row: PullbackSetupRow }) {
  return (
    <div className="bg-slate-50 px-4 py-4 text-xs text-slate-600">
      <p className="text-slate-700">{row.notes}</p>

      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1">
        {row.day0_date && (
          <span>
            Impulse (Day 0){' '}
            <span className="font-medium text-slate-900">{row.day0_date}</span>
            {typeof row.day0_pct === 'number' && (
              <span className="text-emerald-700"> (+{row.day0_pct.toFixed(1)}%)</span>
            )}
          </span>
        )}
        {typeof row.pullback_low === 'number' && (
          <span className="flex items-center gap-1">
            <Crosshair className="h-3 w-3 text-rose-500" />
            Pullback low{' '}
            <span className="font-medium text-slate-900">{fmtINR(row.pullback_low)}</span>
          </span>
        )}
        {typeof row.support_base === 'number' && (
          <span>
            Support (20 EMA){' '}
            <span className="font-medium text-slate-900">{fmtINR(row.support_base)}</span>
          </span>
        )}
        <span>
          VDU{' '}
          <span className={row.vdu ? 'font-medium text-emerald-700' : 'text-slate-400'}>
            {row.vdu ? 'confirmed' : 'pending'}
          </span>
        </span>
      </div>

      {row.status === 'buy_trigger' && (
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 rounded-lg border border-emerald-200 bg-emerald-50/60 px-3 py-2">
          <span className="flex items-center gap-1 font-semibold text-emerald-900">
            <Target className="h-3.5 w-3.5" /> Trade plan
          </span>
          {typeof row.entry === 'number' && (
            <span>Entry <span className="font-medium text-slate-900">{fmtINR(row.entry)}</span></span>
          )}
          {typeof row.stop === 'number' && (
            <span>Stop <span className="font-medium text-rose-700">{fmtINR(row.stop)}</span></span>
          )}
          {typeof row.target1 === 'number' && (
            <span>T1 <span className="font-medium text-emerald-700">{fmtINR(row.target1)}</span></span>
          )}
          {typeof row.rr === 'number' && (
            <span className="text-slate-500">R:R {row.rr}:1 · partial at T1</span>
          )}
        </div>
      )}

      {row.interest.reasons.length > 0 && (
        <div className="mt-3">
          <div className="font-semibold text-slate-700">Interest read</div>
          <ul className="mt-1 list-disc pl-4">
            {row.interest.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function PullbackSetupsTable({ rows }: { rows: PullbackSetupRow[] }) {
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
    <section className="mt-8 rounded-2xl border border-emerald-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <span className="mt-1 h-5 w-1 flex-shrink-0 rounded-full bg-emerald-500" />
        <div>
          <h2 className="flex items-center gap-2 text-lg font-bold text-slate-900">
            <TrendingUp className="h-5 w-5 text-emerald-600" />
            Pullback Re-Entry Setups ({rows.length})
          </h2>
          <p className="mt-0.5 text-sm text-slate-600">
            Impulse → volume dry-up pullback → breakout trigger, evaluated on your previous
            picks that still show persisted interest. Monitoring only — buy only on the trigger.
          </p>
        </div>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-500">
              <th className="py-2 pr-3 font-medium">Symbol</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 pr-3 font-medium" title="Accumulation-continuity signals — NOT raw volume size">
                Interest
              </th>
              <th className="py-2 pr-3 font-medium">Day 0</th>
              <th className="py-2 pr-3 font-medium">Entry / Stop / T1</th>
              <th className="py-2 pr-1 font-medium" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const meta = STATUS_META[r.status] ?? STATUS_META.no_impulse
              const isOpen = open.has(r.symbol)
              return (
                <Fragment key={r.symbol}>
                  <tr
                    className={`cursor-pointer border-b border-slate-100 align-middle hover:bg-slate-50 ${
                      r.status === 'buy_trigger' ? 'bg-emerald-50/60' : ''
                    }`}
                    onClick={() => toggle(r.symbol)}
                  >
                    <td className="py-2.5 pr-3">
                      <Link
                        to={`/stock/${encodeURIComponent(r.symbol)}`}
                        onClick={(e) => e.stopPropagation()}
                        className="font-mono text-sm font-semibold text-slate-900 hover:underline"
                      >
                        {r.symbol.replace(/\.NS$/, '')}
                      </Link>
                      {r.company && r.company !== r.symbol && (
                        <div className="max-w-[160px] truncate text-[11px] text-slate-400">
                          {r.company}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <span
                        title={meta.hint}
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${meta.cls}`}
                      >
                        {meta.label}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3">
                      <InterestChips interest={r.interest} />
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-slate-600">
                      {r.day0_date ? (
                        <>
                          <span className="tabular-nums">{r.day0_date}</span>
                          {typeof r.day0_pct === 'number' && (
                            <span className="ml-1 text-emerald-700">+{r.day0_pct.toFixed(1)}%</span>
                          )}
                        </>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-xs tabular-nums text-slate-700">
                      {r.status === 'buy_trigger' &&
                      typeof r.entry === 'number' &&
                      typeof r.stop === 'number' &&
                      typeof r.target1 === 'number' ? (
                        <span>
                          {fmtINR(r.entry)}
                          {' / '}
                          <span className="text-rose-700">{fmtINR(r.stop)}</span>
                          {' / '}
                          <span className="text-emerald-700">{fmtINR(r.target1)}</span>
                        </span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
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
                      <td colSpan={6} className="p-0">
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
        Monitoring only — not an at-market buy list. A stock appears here only while interest
        persists (OBV-90d rising, up/down-volume ≥ 1.0, price above its 50-day trend). A{' '}
        <span className="font-medium">low</span>-volume down-day on the pullback is the VDU
        supply test (bullish); only a <span className="font-medium">high</span>-volume down-day
        invalidates. Buy on the trigger — a close above the prior bar&apos;s high on rising
        volume — with a 0.5%-below-low stop and a 2:1 first target.
      </p>
    </section>
  )
}
