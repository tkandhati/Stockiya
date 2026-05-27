import { Link } from 'react-router-dom'
import {
  AlertCircle,
  AlertTriangle,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  Clock,
  Hand,
  ShieldAlert,
  TrendingUp,
} from 'lucide-react'
import type { Position, PositionAction } from '../types'
import { fmtINR, fmtPct } from '../api'

/**
 * Single open-position card. Action is the headline; ladder is the body.
 *
 * Action colour scheme:
 *   hold              → slate (neutral)
 *   tighten_stop_45   → amber (caution)
 *   exit_t1           → emerald (good news, partial profit)
 *   exit_t2           → emerald (great news, full target)
 *   exit_stop         → rose (loss, must act)
 *   exit_time_stop    → rose (loss/break-even, must act)
 *   exit_final        → rose (must act regardless)
 */
const ACTION_META: Record<PositionAction, {
  label: string
  tone: 'slate' | 'amber' | 'emerald' | 'rose'
  icon: React.ReactNode
}> = {
  hold: { label: 'Hold', tone: 'slate', icon: <Hand className="h-4 w-4" /> },
  tighten_stop_45: {
    label: 'Tighten stop',
    tone: 'amber',
    icon: <ShieldAlert className="h-4 w-4" />,
  },
  exit_t1: {
    label: 'Exit T1 (sell 50%)',
    tone: 'emerald',
    icon: <CheckCircle2 className="h-4 w-4" />,
  },
  exit_t2: {
    label: 'Exit T2 (sell remainder)',
    tone: 'emerald',
    icon: <CheckCircle2 className="h-4 w-4" />,
  },
  exit_stop: {
    label: 'Stop hit — exit',
    tone: 'rose',
    icon: <AlertTriangle className="h-4 w-4" />,
  },
  exit_time_stop: {
    label: 'Time stop — exit',
    tone: 'rose',
    icon: <Clock className="h-4 w-4" />,
  },
  exit_final: {
    label: 'Day-180 final exit',
    tone: 'rose',
    icon: <AlertCircle className="h-4 w-4" />,
  },
}

const TONE_BORDER = {
  slate: 'border-slate-200',
  amber: 'border-amber-300 ring-1 ring-amber-200',
  emerald: 'border-emerald-300 ring-1 ring-emerald-200',
  rose: 'border-rose-300 ring-1 ring-rose-200',
}

const TONE_PILL = {
  slate: 'bg-slate-100 text-slate-700',
  amber: 'bg-amber-100 text-amber-900',
  emerald: 'bg-emerald-100 text-emerald-900',
  rose: 'bg-rose-100 text-rose-900',
}

export function PositionCard({ position: p }: { position: Position }) {
  const meta = ACTION_META[p.action] ?? ACTION_META.hold
  const current = p.current_price ?? null
  const pnl = p.pnl_pct ?? null

  return (
    <Link
      to={`/stock/${encodeURIComponent(p.symbol)}`}
      className={`group block rounded-2xl border bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md ${TONE_BORDER[meta.tone]}`}
    >
      {/* Heading + action pill */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-lg font-semibold text-slate-900">
            {p.company}
          </h3>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-xs text-slate-500">
            <span>{p.symbol}</span>
            <span>· entered {p.entry_date}</span>
            <span>· {p.days_held}d held</span>
          </div>
        </div>
        <div
          className={`flex flex-shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${TONE_PILL[meta.tone]}`}
        >
          {meta.icon}
          <span>{meta.label}</span>
        </div>
      </div>

      {/* Action note */}
      <p className={`mt-3 rounded-lg px-3 py-2 text-sm leading-snug ${TONE_PILL[meta.tone]}`}>
        {p.action_note}
      </p>

      {/* Price ladder */}
      <div className="mt-4 grid grid-cols-2 gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3 text-xs md:grid-cols-5">
        <Rung label="Entry" value={fmtINR(p.entry_price)} tone="slate" />
        <Rung
          label="Current"
          value={current != null ? fmtINR(current) : '—'}
          extra={pnl != null ? fmtPct(pnl) : undefined}
          tone={pnl == null ? 'slate' : pnl >= 0 ? 'emerald' : 'rose'}
        />
        <Rung
          label="Stop"
          value={fmtINR(p.stop_price)}
          tone="rose"
          extra={p.new_stop ? `→ ${fmtINR(p.new_stop)}` : undefined}
        />
        <Rung
          label="T1 (+8%)"
          value={fmtINR(p.t1_price)}
          tone={p.hit_t1 ? 'emerald' : 'amber'}
          extra={p.hit_t1 ? '✓ hit' : undefined}
        />
        <Rung label="T2 (+16%)" value={fmtINR(p.t2_price)} tone="indigo" />
      </div>

      {/* Time-stop dates + position size */}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span className="inline-flex items-center gap-1">
          <CalendarClock className="h-3 w-3" />
          d45 {p.time_stops.day_45}
        </span>
        <span>· d90 {p.time_stops.day_90}</span>
        <span>· d180 {p.time_stops.day_180}</span>
        <span className="ml-auto inline-flex items-center gap-1">
          <TrendingUp className="h-3 w-3" />
          {p.shares_total.toLocaleString('en-IN')} sh
        </span>
        <ArrowUpRight className="h-3.5 w-3.5 text-slate-400 transition group-hover:text-slate-700" />
      </div>

      {p.headline && (
        <p className="mt-3 border-t border-slate-100 pt-2 text-[11px] italic text-slate-500">
          {p.headline}
        </p>
      )}
    </Link>
  )
}

function Rung({
  label,
  value,
  extra,
  tone,
}: {
  label: string
  value: string
  extra?: string
  tone: 'slate' | 'emerald' | 'amber' | 'rose' | 'indigo'
}) {
  const labelColor = {
    slate: 'text-slate-500',
    emerald: 'text-emerald-700',
    amber: 'text-amber-700',
    rose: 'text-rose-700',
    indigo: 'text-indigo-700',
  }[tone]
  const valueColor = {
    slate: 'text-slate-900',
    emerald: 'text-emerald-900',
    amber: 'text-amber-900',
    rose: 'text-rose-900',
    indigo: 'text-indigo-900',
  }[tone]
  return (
    <div>
      <div className={`text-[10px] font-semibold uppercase tracking-wide ${labelColor}`}>
        {label}
      </div>
      <div className={`font-semibold tabular-nums ${valueColor}`}>{value}</div>
      {extra && (
        <div className={`text-[10px] tabular-nums opacity-80 ${labelColor}`}>{extra}</div>
      )}
    </div>
  )
}
