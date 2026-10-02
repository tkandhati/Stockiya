import { Link } from 'react-router-dom'
import { BarChart3, Lightbulb, Target } from 'lucide-react'
import { fmtINR } from '../api'
import type { Section2Dashboard as Dash, Section2Tile, PullbackSetupRow } from '../types'

/**
 * Section 2 — tiled monitoring dashboard over the recent-picks cohort.
 *
 * A grid of top-5 leaderboard tiles (volume 15/30/45/60d, delivery,
 * accumulation, most-recommended) + past-learnings tiles (shown only when they
 * have qualifying members), with a compact actionable-setups strip underneath.
 *
 * Presentation-only — mirrors backend/section2_dashboard.py. Never a buy list.
 */

const RANK_DOT = ['bg-amber-500', 'bg-slate-400', 'bg-amber-700', 'bg-slate-300', 'bg-slate-300']

function TileCard({ tile }: { tile: Section2Tile }) {
  if (!tile.rows.length) return null
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-bold text-slate-900">{tile.title}</h3>
      <p className="mt-0.5 text-[11px] leading-snug text-slate-500">{tile.subtitle}</p>
      <ol className="mt-3 space-y-1.5">
        {tile.rows.map((r, i) => (
          <li key={r.symbol} className="flex items-center gap-2 text-sm">
            <span
              className={`flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white ${RANK_DOT[i] ?? 'bg-slate-300'}`}
            >
              {i + 1}
            </span>
            <Link
              to={`/stock/${encodeURIComponent(r.symbol)}`}
              className="min-w-0 flex-1 truncate font-mono text-xs font-semibold text-slate-800 hover:underline"
              title={r.company}
            >
              {r.display}
            </Link>
            <span className="font-semibold tabular-nums text-slate-900">{r.value}</span>
          </li>
        ))}
      </ol>
      {tile.rows.some((r) => r.extra) && (
        <div className="mt-2 border-t border-slate-100 pt-1.5 text-[10px] text-slate-400">
          {tile.rows.find((r) => r.extra)?.extra}
        </div>
      )}
    </div>
  )
}

function LearningCard({ learning }: { learning: Dash['learnings'][number] }) {
  if (!learning.names.length) return null
  const tone =
    learning.id === 'distribution_risk'
      ? 'border-rose-200 bg-rose-50'
      : learning.id === 'trigger_fired'
      ? 'border-emerald-200 bg-emerald-50'
      : 'border-indigo-200 bg-indigo-50'
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
        <span>{learning.badge}</span>
        <span>{learning.label}</span>
        <span className="ml-auto rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-600">
          {learning.names.length}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {learning.names.map((n) => (
          <Link
            key={n.symbol}
            to={`/stock/${encodeURIComponent(n.symbol)}`}
            className="rounded bg-white/80 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-slate-800 hover:underline"
          >
            {n.display}
          </Link>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-snug text-slate-600">{learning.why}</p>
    </div>
  )
}

const ACTIONABLE = new Set(['buy_trigger', 'vdu_confirmed_watch_trigger'])

function SetupStrip({ setups }: { setups: PullbackSetupRow[] }) {
  const rows = setups.filter((s) => ACTIONABLE.has(s.status))
  if (!rows.length) return null
  return (
    <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50/50 p-4">
      <div className="flex items-center gap-2 text-sm font-bold text-emerald-900">
        <Target className="h-4 w-4" />
        Actionable pullback setups ({rows.length})
      </div>
      <ul className="mt-2 space-y-1.5">
        {rows.map((r) => (
          <li key={r.symbol} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
            <Link
              to={`/stock/${encodeURIComponent(r.symbol)}`}
              className="font-mono text-sm font-semibold text-slate-900 hover:underline"
            >
              {r.symbol.replace(/\.NS$/, '')}
            </Link>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                r.status === 'buy_trigger'
                  ? 'bg-emerald-600 text-white'
                  : 'bg-emerald-100 text-emerald-900'
              }`}
            >
              {r.status === 'buy_trigger' ? 'Buy trigger' : 'VDU · watch trigger'}
            </span>
            {r.status === 'buy_trigger' &&
              typeof r.entry === 'number' &&
              typeof r.stop === 'number' &&
              typeof r.target1 === 'number' && (
                <span className="tabular-nums text-slate-700">
                  {fmtINR(r.entry)} / <span className="text-rose-700">{fmtINR(r.stop)}</span> /{' '}
                  <span className="text-emerald-700">{fmtINR(r.target1)}</span>
                </span>
              )}
            <span className="w-full text-[11px] text-slate-500">{r.notes}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Section2Dashboard({ dash }: { dash: Dash }) {
  return (
    <section className="mt-8 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <span className="mt-1 h-5 w-1 flex-shrink-0 rounded-full bg-emerald-500" />
        <div>
          <h2 className="flex items-center gap-2 text-lg font-bold text-slate-900">
            <BarChart3 className="h-5 w-5 text-emerald-600" />
            Recent-picks analysis
          </h2>
          <p className="mt-0.5 text-sm text-slate-600">
            Leaderboards over the {dash.cohort_size} pick{dash.cohort_size === 1 ? '' : 's'} from the
            last {dash.window_days} days. Monitoring only — not an at-market buy list.
          </p>
        </div>
      </div>

      {dash.cohort_size === 0 ? (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          No open picks in the last {dash.window_days} days to analyze yet. New picks will show up
          here as they accumulate.
        </div>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {dash.tiles.map((t) => (
              <TileCard key={t.id} tile={t} />
            ))}
          </div>

          {dash.learnings.length > 0 && (
            <div className="mt-6">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <Lightbulb className="h-3.5 w-3.5 text-amber-500" />
                What past picks taught us
              </div>
              <div className="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {dash.learnings.map((l) => (
                  <LearningCard key={l.id} learning={l} />
                ))}
              </div>
            </div>
          )}

          <SetupStrip setups={dash.setups} />
        </>
      )}
    </section>
  )
}
