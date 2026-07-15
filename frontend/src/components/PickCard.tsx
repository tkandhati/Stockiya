import { Link } from 'react-router-dom'
import {
  ArrowUpRight,
  CheckCircle2,
  Clock,
  History,
  Layers,
  ShieldCheck,
  Target,
  TrendingDown,
  TrendingUp,
  Wallet,
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
  const volEvent = pick.volume_event

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

      {/* 2b. Already-held banner — pick's symbol is a live position */}
      {pick.already_held && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <Wallet className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="font-semibold uppercase tracking-wide">
              Already held ({pick.already_held.ownership || 'unknown'})
            </div>
            <div className="mt-0.5 leading-snug">
              Entry {pick.already_held.entry_date || '—'}
              {typeof pick.already_held.days_held === 'number' && (
                <> · day {pick.already_held.days_held}</>
              )}
              {typeof pick.already_held.pnl_pct === 'number' && (
                <> · P&amp;L {fmtPct(pick.already_held.pnl_pct)}</>
              )}
            </div>
            {pick.already_held.portfolio_action_note && (
              <div className="mt-0.5 leading-snug opacity-85">
                Portfolio says: <span className="font-mono">{pick.already_held.portfolio_action}</span>
                {' — '}{pick.already_held.portfolio_action_note}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 2c. Consecutive-pick diff — what changed since last time */}
      {pick.change_since_prev_pick && (
        <div className="mt-3 rounded-lg border border-sky-200 bg-sky-50/70 px-3 py-2 text-xs text-sky-900">
          <div className="flex items-center gap-1.5 font-semibold uppercase tracking-wide">
            <History className="h-3.5 w-3.5" />
            Since last pick
            <span className="font-normal opacity-80">
              ({pick.change_since_prev_pick.prev_date}
              {typeof pick.change_since_prev_pick.days_ago === 'number' &&
                ` · ${pick.change_since_prev_pick.days_ago}d ago`})
            </span>
          </div>
          <ul className="mt-1 space-y-0.5 leading-snug">
            {pick.change_since_prev_pick.confirmation_score && (
              <li>
                Confirmation{' '}
                <span className="font-mono">
                  {pick.change_since_prev_pick.confirmation_score.was.toFixed(2)}
                  {' → '}
                  {pick.change_since_prev_pick.confirmation_score.now.toFixed(2)}
                </span>
                {' '}
                <span
                  className={
                    (pick.change_since_prev_pick.confirmation_score.delta ?? 0) >= 0
                      ? 'text-emerald-700'
                      : 'text-rose-700'
                  }
                >
                  ({(pick.change_since_prev_pick.confirmation_score.delta ?? 0) >= 0 ? '+' : ''}
                  {(pick.change_since_prev_pick.confirmation_score.delta ?? 0).toFixed(2)})
                </span>
              </li>
            )}
            {pick.change_since_prev_pick.bonuses && (
              <>
                {pick.change_since_prev_pick.bonuses.added.length > 0 && (
                  <li>
                    Bonuses added:{' '}
                    <span className="text-emerald-800">
                      {pick.change_since_prev_pick.bonuses.added.join(', ')}
                    </span>
                  </li>
                )}
                {pick.change_since_prev_pick.bonuses.removed.length > 0 && (
                  <li>
                    Bonuses lost:{' '}
                    <span className="text-rose-800">
                      {pick.change_since_prev_pick.bonuses.removed.join(', ')}
                    </span>
                  </li>
                )}
              </>
            )}
            {pick.change_since_prev_pick.entry_timing && (
              <li>
                Entry timing:{' '}
                <span className="font-mono">
                  {pick.change_since_prev_pick.entry_timing.was || '—'}
                  {' → '}
                  {pick.change_since_prev_pick.entry_timing.now || '—'}
                </span>
              </li>
            )}
            {pick.change_since_prev_pick.weinstein_stage && (
              <li>
                Weinstein stage:{' '}
                <span className="font-mono">
                  {pick.change_since_prev_pick.weinstein_stage.was || '—'}
                  {' → '}
                  {pick.change_since_prev_pick.weinstein_stage.now || '—'}
                </span>
              </li>
            )}
            {pick.change_since_prev_pick.rank_change && (
              <li>
                Rank{' '}
                <span className="font-mono">
                  #{pick.change_since_prev_pick.rank_change.was}
                  {' → '}
                  #{pick.change_since_prev_pick.rank_change.now}
                </span>
                {pick.change_since_prev_pick.rank_change.delta !== 0 && (
                  <span
                    className={
                      pick.change_since_prev_pick.rank_change.delta < 0
                        ? 'ml-1 text-emerald-700'
                        : 'ml-1 text-rose-700'
                    }
                  >
                    ({pick.change_since_prev_pick.rank_change.delta < 0 ? 'climbed ' : 'dropped '}
                    {Math.abs(pick.change_since_prev_pick.rank_change.delta)})
                  </span>
                )}
              </li>
            )}
            {pick.change_since_prev_pick.headline_changed && (
              <li className="italic opacity-80">Headline reworded</li>
            )}
          </ul>
        </div>
      )}

      {/* 2c2. Multi-day pick trail — one snapshot per prior appearance */}
      {pick.pick_history && pick.pick_history.length > 0 && (
        <div className="mt-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
          <div className="flex items-center gap-1.5 font-semibold uppercase tracking-wide text-slate-800">
            <History className="h-3.5 w-3.5" />
            Last {pick.pick_history.length} appearance{pick.pick_history.length === 1 ? '' : 's'}
          </div>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full text-left font-mono">
              <thead className="text-slate-500">
                <tr>
                  <th className="pr-2 font-normal">Date</th>
                  <th className="pr-2 font-normal">Rank</th>
                  <th className="pr-2 font-normal">Score</th>
                  <th className="pr-2 font-normal">Δ</th>
                  <th className="pr-2 font-normal">Entry</th>
                  <th className="pr-2 font-normal">Bonuses</th>
                </tr>
              </thead>
              <tbody>
                {pick.pick_history.map((h) => {
                  const tone =
                    h.direction === 'positive'
                      ? 'text-emerald-700 bg-emerald-50/50'
                      : h.direction === 'negative'
                      ? 'text-rose-700 bg-rose-50/50'
                      : h.direction === 'first_appearance'
                      ? 'text-slate-500 bg-slate-50/60'
                      : 'text-slate-700'
                  const marker =
                    h.direction === 'positive'
                      ? '▲'
                      : h.direction === 'negative'
                      ? '▼'
                      : h.direction === 'first_appearance'
                      ? '◇'
                      : '·'
                  return (
                    <tr key={h.date} className={tone}>
                      <td className="pr-2 py-0.5">{h.date}</td>
                      <td className="pr-2 py-0.5">{h.rank != null ? `#${h.rank}` : '—'}</td>
                      <td className="pr-2 py-0.5 tabular-nums">{h.score.toFixed(2)}</td>
                      <td className="pr-2 py-0.5 tabular-nums">
                        <span className="mr-0.5">{marker}</span>
                        {h.score_delta != null
                          ? `${h.score_delta > 0 ? '+' : ''}${h.score_delta.toFixed(2)}`
                          : '—'}
                      </td>
                      <td className="pr-2 py-0.5 tabular-nums">{fmtINR(h.entry)}</td>
                      <td className="pr-2 py-0.5">{h.bonus_count}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-1 text-[10px] text-slate-500">
            ▲ stronger than the day before · ▼ weaker · · flat · ◇ first appearance in window
          </div>
        </div>
      )}

      {/* 2d. Holding horizon badge */}
      {pick.holding_horizon && (
        <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-700">
          <Clock className="h-3.5 w-3.5 text-slate-500" />
          <span className="font-semibold">Horizon</span>
          <span className="font-mono">{pick.holding_horizon.days}d</span>
          <span className="opacity-70">· volume-based bucket</span>
        </div>
      )}

      {/* 3. Headline */}
      {pick.headline && (
        <p className="mt-3 text-sm font-medium leading-snug text-slate-900">
          {pick.headline}
        </p>
      )}

      {volEvent && volEvent.kind !== 'neutral' && (
        <div
          className={`mt-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${
            volEvent.direction === 'bullish'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
              : volEvent.direction === 'bearish'
              ? 'border-rose-200 bg-rose-50 text-rose-900'
              : 'border-slate-200 bg-slate-50 text-slate-700'
          }`}
        >
          {volEvent.direction === 'bearish' ? (
            <TrendingDown className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          ) : (
            <TrendingUp className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          )}
          <div>
            <div className="font-semibold">{volEvent.label}</div>
            <div className="mt-0.5 leading-snug opacity-85">
              {volEvent.detail}
            </div>
          </div>
        </div>
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
