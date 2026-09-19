import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Briefcase, FlaskConical, RefreshCw, Sparkles } from 'lucide-react'
import { fetchPicks, fmtDateTimeIST, refreshPicks } from '../api'
import { PickCard } from '../components/PickCard'
import { PullbackSetupsTable } from '../components/PullbackSetupsTable'
import { StrategyTabs } from '../components/StrategyTabs'
import type { PicksResponse } from '../types'

// TWO-SECTION MODE (owner ask, 2026-09-19). The page renders exactly two content
// sections: (1) Top picks by volume, (2) Pullback Re-Entry Setups on previous
// picks. All other panels/banners were removed here AND in
// backend/orchestrator.py (both reversible). The imports + blocks below are
// commented, not deleted, so any panel can be restored:
//   import { DemoBanner } from '../components/DemoBanner'
//   import { DataHealthPill } from '../components/DataHealthPill'
//   import { ClosestToFiringPanel } from '../components/ClosestToFiringPanel'
//   import { NotActionablePanel } from '../components/NotActionablePanel'
//   import { CoiledAccumulatorsPanel } from '../components/CoiledAccumulatorsPanel'
//   import { PickFollowupTable } from '../components/PickFollowupTable'
//   import { DeliveryWeightedPicks } from '../components/DeliveryWeightedPicks'
//   import { RegimeBanner } from '../components/RegimeBanner'

export function PicksPage() {
  const qc = useQueryClient()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['picks'],
    queryFn: fetchPicks,
    staleTime: 5 * 60 * 1000,
  })
  const refresh = useMutation({
    mutationFn: refreshPicks,
    onSuccess: (resp: PicksResponse) => qc.setQueryData(['picks'], resp),
  })

  // Section 1 — the top picks, by volume. One clean list (no sub-split).
  const topPicks = data?.picks ?? []
  const pullbackSetups = data?.pullback_setups ?? []

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <StrategyTabs />
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-900">
            <Sparkles className="h-5 w-5 text-amber-500" />
            Today&apos;s Picks{topPicks.length > 0 ? ` (${topPicks.length})` : ''}
          </h1>
          <p className="mt-1 text-sm text-slate-700">
            <span className="font-medium">Don&apos;t invent. Follow the institutions.
            Pick one.</span>
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            Swing trading · 3-week to 3-month typical hold · daily review ·{' '}
            <span className="font-mono">{data?.date ?? '—'}</span>
            {data?.generated_at && (
              <span className="ml-2 font-mono text-slate-400">
                · generated {fmtDateTimeIST(data.generated_at)}
              </span>
            )}
            {data?.demo_mode && (
              <span className="ml-2 rounded bg-rose-200 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-rose-900">
                ⚠ Demo data
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/backtest"
            className="flex items-center gap-2 rounded-lg border border-violet-300 bg-violet-50 px-3 py-2 text-sm font-medium text-violet-900 shadow-sm transition hover:border-violet-400 hover:bg-violet-100"
          >
            <FlaskConical className="h-4 w-4" />
            Backtest
          </Link>
          <Link
            to="/positions"
            className="flex items-center gap-2 rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-900 shadow-sm transition hover:border-indigo-400 hover:bg-indigo-100"
          >
            <Briefcase className="h-4 w-4" />
            My positions
          </Link>
          <button
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending || isLoading}
            className="flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw
              className={`h-4 w-4 ${refresh.isPending ? 'animate-spin' : ''}`}
            />
            {refresh.isPending ? 'Regenerating…' : 'Refresh picks'}
          </button>
        </div>
      </header>

      <main className="mt-8">
        {isLoading && <SkeletonGrid />}
        {isError && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
            <div className="font-semibold">Could not load picks.</div>
            <div className="mt-1 font-mono text-xs">{(error as Error).message}</div>
            <div className="mt-2 text-xs text-rose-700">
              Check that the backend is running on http://localhost:8000.
            </div>
          </div>
        )}

        {/* Section 1 — Top picks by volume. */}
        {data && topPicks.length > 0 && (
          <section>
            <SectionHeading
              tone="emerald"
              title={`Top picks (by volume) (${topPicks.length})`}
              subtitle="The volume pipeline's strongest candidates for today."
            />
            <div className={`mt-4 grid grid-cols-1 gap-5 ${gridCols(topPicks.length)}`}>
              {topPicks.map((p) => (
                <PickCard key={p.symbol} pick={p} />
              ))}
            </div>
          </section>
        )}

        {/* No picks today — say so plainly (never a blank page). */}
        {data && topPicks.length === 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span className="font-semibold">No pick cleared the bar today.</span>{' '}
            <span className="opacity-90">
              {data.message ||
                'Normal for a large universe — quality over quantity. See the pullback setups below.'}
            </span>
          </div>
        )}
      </main>

      {/* Section 2 — Pullback Re-Entry Setups on previous picks with persisted
          interest. Renders null when empty. */}
      {pullbackSetups.length > 0 && <PullbackSetupsTable rows={pullbackSetups} />}

      {data && (
        <footer className="mt-8 text-xs text-slate-400">
          Generated at <span className="font-mono">{data.generated_at}</span> IST
        </footer>
      )}
    </div>
  )
}

// Column count matches the old single-grid behaviour: 1 card = centered single
// column, 2 = two columns, 3+ = three columns.
function gridCols(n: number): string {
  return n === 1
    ? 'lg:grid-cols-1 max-w-2xl'
    : n === 2
    ? 'lg:grid-cols-2'
    : 'lg:grid-cols-3'
}

function SectionHeading({
  title,
  subtitle,
  tone,
}: {
  title: string
  subtitle?: string
  tone: 'emerald' | 'amber' | 'slate'
}) {
  const bar = {
    emerald: 'bg-emerald-500',
    amber: 'bg-amber-500',
    slate: 'bg-slate-400',
  }[tone]
  return (
    <div className="flex items-start gap-3">
      <span className={`mt-1 h-5 w-1 flex-shrink-0 rounded-full ${bar}`} />
      <div>
        <h2 className="text-lg font-bold text-slate-900">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-slate-600">{subtitle}</p>}
      </div>
    </div>
  )
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-72 animate-pulse rounded-2xl border border-slate-200 bg-white p-6"
        >
          <div className="h-5 w-2/3 rounded bg-slate-200" />
          <div className="mt-2 h-3 w-1/3 rounded bg-slate-200" />
          <div className="mt-6 h-8 w-1/2 rounded bg-slate-200" />
          <div className="mt-6 grid grid-cols-3 gap-3">
            <div className="h-14 rounded bg-slate-100" />
            <div className="h-14 rounded bg-slate-100" />
            <div className="h-14 rounded bg-slate-100" />
          </div>
        </div>
      ))}
    </div>
  )
}
