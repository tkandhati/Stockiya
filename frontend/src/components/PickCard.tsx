import { Link } from 'react-router-dom'
import {
  ArrowUpRight,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
} from 'lucide-react'
import type { Pick, ReasoningPoint, WeinsteinStage } from '../types'
import { fmtINR, fmtPct } from '../api'

const timingPill: Record<Pick['entry_timing'], { label: string; tone: string }> = {
  early: { label: '🎯 Early', tone: 'bg-emerald-100 text-emerald-800 ring-emerald-200' },
  mid: { label: '🚀 Mid', tone: 'bg-sky-100 text-sky-800 ring-sky-200' },
  late: { label: '⏱️ Late', tone: 'bg-amber-100 text-amber-800 ring-amber-200' },
  missed: { label: '🛑 Missed', tone: 'bg-rose-100 text-rose-800 ring-rose-200' },
  unknown: { label: '— No signal', tone: 'bg-slate-100 text-slate-600 ring-slate-200' },
}

const stageLabel: Record<WeinsteinStage, string> = {
  stage_1_base: 'Stage 1 base',
  stage_1_to_2: 'Stage 1→2',
  stage_2_advance: 'Stage 2',
  stage_3_top: 'Stage 3 top',
  stage_4_decline: 'Stage 4',
  undefined: '—',
}

/**
 * Priority order for the 3 supporting signals shown on the card.
 * Long-term volume signals first, then medium-term, then patterns.
 */
const SIGNAL_PRIORITY = [
  'Block / Bulk deals (30d)',         // documented institutional trades — top priority when present
  'Stan Weinstein Stage',
  'OBV (90-day) — Granville',
  'OBV (180-day) — half-year confirmation',
  'Chaikin Money Flow (60d)',
  'Up/Down volume ratio (90d)',
  'Base length',
  'Pattern triggers',
  'Wyckoff phase (medium-term)',
]

function pickTopSignals(reasoning: ReasoningPoint[]): ReasoningPoint[] {
  const bullish = reasoning.filter((r) => r.state === 'bullish')
  const byLabel = new Map(bullish.map((r) => [r.label, r]))
  const out: ReasoningPoint[] = []
  for (const label of SIGNAL_PRIORITY) {
    const r = byLabel.get(label)
    if (r) {
      out.push(r)
      if (out.length === 3) return out
    }
  }
  for (const r of bullish) {
    if (!out.includes(r)) {
      out.push(r)
      if (out.length === 3) break
    }
  }
  return out
}

export function PickCard({ pick }: { pick: Pick }) {
  const topSignals = pickTopSignals(pick.reasoning ?? [])

  return (
    <Link
      to={`/stock/${encodeURIComponent(pick.symbol)}`}
      className="group block rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md"
    >
      {/* ── 1. Heading row: WHAT ───────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold leading-tight text-slate-900">
            {pick.company}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 font-mono text-xs text-slate-500">
            <span>{pick.symbol}</span>
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${
                timingPill[pick.entry_timing].tone
              }`}
            >
              {timingPill[pick.entry_timing].label}
            </span>
            {pick.weinstein_stage !== 'undefined' && (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-700 ring-1 ring-slate-200">
                {stageLabel[pick.weinstein_stage]}
              </span>
            )}
          </div>
        </div>
        <ArrowUpRight className="h-5 w-5 flex-shrink-0 text-slate-400 transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-slate-700" />
      </div>

      {/* ── 2. The trade: 5 numbers in one strip — Current → Buy → Target → Stop → Window */}
      <div className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border border-slate-200 bg-slate-50/60 p-4 text-sm md:grid-cols-5">
        <Cell label="Current" value={fmtINR(pick.current)} tone="slate" />
        <Cell label="Buy at" value={fmtINR(pick.best_buy_at)} tone="emerald" />
        <Cell
          label="Target"
          value={fmtINR(pick.sell_target)}
          extra={fmtPct(pick.upside_pct)}
          tone="amber"
        />
        <Cell
          label="Stop"
          value={fmtINR(pick.stop_loss)}
          extra={fmtPct(pick.downside_pct)}
          tone="rose"
        />
        <Cell
          label="Window"
          value={pick.target_window.label.replace(' months', 'mo').replace(' month', 'mo')}
          icon={<CalendarClock className="h-3.5 w-3.5" />}
          tone="indigo"
        />
      </div>

      {/* ── 3. WHY (one-line thesis) ─────────────────────────────────── */}
      {pick.headline && (
        <p className="mt-4 text-sm font-medium leading-snug text-slate-900">
          {pick.headline}
        </p>
      )}

      {/* ── 4. Top 3 signals (proof — each independently verifiable) ── */}
      {topSignals.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {topSignals.map((s) => (
            <li key={s.label} className="flex items-start gap-2 text-xs">
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-emerald-600" />
              <span className="text-slate-700">
                <span className="font-medium text-slate-900">{s.label}:</span>{' '}
                <span className="font-mono text-slate-600">{s.value}</span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* ── 5. RISK (one-line) ─────────────────────────────────── */}
      {pick.risk_headline && (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-rose-100 bg-rose-50/60 px-3 py-2 text-xs text-rose-900">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-rose-600" />
          <span className="leading-relaxed">{pick.risk_headline}</span>
        </div>
      )}

      {/* ── 6. Drill-down hint ─────────────────────────────────── */}
      <div className="mt-4 text-[11px] text-slate-400">
        Click for full reasoning checklist, exit scenarios, peer comparison →
      </div>
    </Link>
  )
}

function Cell({
  label,
  value,
  extra,
  icon,
  tone,
}: {
  label: string
  value: string
  extra?: string
  icon?: React.ReactNode
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
      <div className={`flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide ${labelColor}`}>
        {icon}
        <span>{label}</span>
      </div>
      <div className={`mt-0.5 font-semibold tabular-nums ${valueColor}`}>{value}</div>
      {extra && <div className={`text-[10px] tabular-nums opacity-80 ${labelColor}`}>{extra}</div>}
    </div>
  )
}
