import { Link } from 'react-router-dom'
import {
  ArrowUpRight,
  CheckCircle2,
  Layers,
  ShieldCheck,
  Target,
  TrendingUp,
} from 'lucide-react'
import type { Pick } from '../types'
import { fmtINR, fmtPct } from '../api'

/**
 * Pick card — gates-based spine.
 * Layout:
 *   1. Rank badge + symbol + headline
 *   2. Confirmation strip (score + bonuses fired)
 *   3. Price plan grid: Entry, Stop, T1, T2, Shares
 *   4. Top gate evidence (CS / VD / BR — one line each)
 */
export function PickCard({ pick }: { pick: Pick }) {
  const plan = pick.price_plan
  const conf = pick.confirmation
  const ev = pick.gates_evidence || {}

  const entry = plan?.entry ?? pick.best_buy_at ?? pick.current_price ?? 0
  const stop = plan?.stop ?? pick.stop_loss ?? 0
  const t1 = plan?.t1 ?? entry * 1.08
  const t2 = plan?.t2 ?? pick.sell_target ?? entry * 1.16
  const shares = plan?.shares_total ?? pick.shares_to_buy ?? 0

  const upside = pick.upside_pct ?? (entry > 0 ? (t2 / entry - 1) * 100 : 0)
  const downside =
    pick.downside_pct ?? (entry > 0 ? (stop / entry - 1) * 100 : 0)

  return (
    <Link
      to={`/stock/${encodeURIComponent(pick.symbol)}`}
      className="group block rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md"
    >
      {/* 1. Heading */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {pick.rank != null && (
              <span className="rounded-full bg-amber-500 px-2 py-0.5 text-xs font-bold text-white">
                #{pick.rank}
              </span>
            )}
            <h2 className="text-xl font-semibold leading-tight text-slate-900">
              {pick.company || pick.symbol}
            </h2>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 font-mono text-xs text-slate-500">
            <span>{pick.symbol}</span>
            {pick.sector && <span>· {pick.sector}</span>}
          </div>
        </div>
        <ArrowUpRight className="h-5 w-5 flex-shrink-0 text-slate-400 transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-slate-700" />
      </div>

      {/* 2. Confirmation strip */}
      {conf && (
        <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-indigo-100 bg-indigo-50/60 px-3 py-2 text-xs text-indigo-900">
          <ShieldCheck className="h-3.5 w-3.5 text-indigo-700" />
          <span className="font-semibold">Confirmation</span>
          <span className="font-mono tabular-nums">{conf.score.toFixed(2)}</span>
          {typeof conf.bonus_count === 'number' && (
            <span className="rounded bg-indigo-100 px-1.5 py-0.5 font-semibold">
              {conf.bonus_count} bonus
            </span>
          )}
          {conf.bonuses_fired && conf.bonuses_fired.length > 0 && (
            <span className="ml-1 truncate text-indigo-800/80">
              {conf.bonuses_fired.slice(0, 2).join(' · ')}
              {conf.bonuses_fired.length > 2 && ` +${conf.bonuses_fired.length - 2}`}
            </span>
          )}
        </div>
      )}

      {/* 3. Headline */}
      {pick.headline && (
        <p className="mt-3 text-sm font-medium leading-snug text-slate-900">
          {pick.headline}
        </p>
      )}

      {/* 4. Price plan grid */}
      <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border border-slate-200 bg-slate-50/60 p-4 text-sm md:grid-cols-5">
        <Cell label="Entry" value={fmtINR(entry)} tone="emerald" />
        <Cell
          label="Stop"
          value={fmtINR(stop)}
          extra={fmtPct(downside)}
          tone="rose"
        />
        <Cell
          label="T1 (+8%)"
          value={fmtINR(t1)}
          extra="sell 50%"
          tone="amber"
        />
        <Cell
          label="T2 (+16%)"
          value={fmtINR(t2)}
          extra={fmtPct(upside)}
          tone="indigo"
        />
        <Cell
          label="Shares"
          value={shares > 0 ? shares.toLocaleString('en-IN') : '—'}
          icon={<Layers className="h-3.5 w-3.5" />}
          tone="slate"
          extra={
            plan?.risk_pct_of_account
              ? `${plan.risk_pct_of_account.toFixed(2)}% risk`
              : undefined
          }
        />
      </div>

      {/* 5. Gate evidence — one line per gate */}
      <ul className="mt-3 space-y-1.5">
        {(['CS', 'VD', 'BR'] as const).map((gid) => {
          const lines = ev[gid]
          if (!lines || lines.length === 0) return null
          const label =
            gid === 'CS'
              ? 'Consolidation'
              : gid === 'VD'
              ? 'Volume / Divergence'
              : 'Breakout'
          const icon =
            gid === 'CS' ? (
              <Target className="h-3.5 w-3.5 text-emerald-600" />
            ) : gid === 'VD' ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
            ) : (
              <TrendingUp className="h-3.5 w-3.5 text-emerald-600" />
            )
          return (
            <li key={gid} className="flex items-start gap-2 text-xs">
              <span className="mt-0.5">{icon}</span>
              <span className="text-slate-700">
                <span className="font-medium text-slate-900">{label}:</span>{' '}
                <span className="text-slate-600">{lines[0]}</span>
              </span>
            </li>
          )
        })}
      </ul>

      {/* 6. Drill-down hint */}
      <div className="mt-4 text-[11px] text-slate-400">
        Click for exit schedule, all gate evidence, and price chart →
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
      <div
        className={`flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide ${labelColor}`}
      >
        {icon}
        <span>{label}</span>
      </div>
      <div className={`mt-0.5 font-semibold tabular-nums ${valueColor}`}>
        {value}
      </div>
      {extra && (
        <div className={`text-[10px] tabular-nums opacity-80 ${labelColor}`}>
          {extra}
        </div>
      )}
    </div>
  )
}
