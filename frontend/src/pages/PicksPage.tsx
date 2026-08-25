import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Briefcase, FlaskConical, RefreshCw, Sparkles } from 'lucide-react'
import { fetchPicks, fmtDateTimeIST, refreshPicks } from '../api'
import { DemoBanner } from '../components/DemoBanner'
import { DataHealthPill } from '../components/DataHealthPill'
import { ClosestToFiringPanel } from '../components/ClosestToFiringPanel'
import { NotActionablePanel } from '../components/NotActionablePanel'
import { CoiledAccumulatorsPanel } from '../components/CoiledAccumulatorsPanel'
import { PickCard } from '../components/PickCard'
import { DeliveryWeightedPicks } from '../components/DeliveryWeightedPicks'
import { RegimeBanner } from '../components/RegimeBanner'
import { StrategyTabs } from '../components/StrategyTabs'
import type { PicksResponse } from '../types'

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

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <StrategyTabs />
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-900">
            <Sparkles className="h-5 w-5 text-amber-500" />
            Today&apos;s Picks{data && data.picks.length > 0 ? ` (${data.picks.length})` : ''}
          </h1>
          <p className="mt-1 text-sm text-slate-700">
            <span className="font-medium">Don&apos;t invent. Follow the institutions.
            Pick one.</span>
          </p>
          {/* Readiness legend — shown when any pick carries a badge (STOCKYA_MAIN_
              SHOW_ALL on). Green = enter today, amber = watch, rose = avoid. */}
          {data?.picks?.some((p) => p.readiness) && (
            <p className="mt-0.5 text-xs text-slate-500">
              <span className="font-medium text-emerald-700">Enter today</span> ·{' '}
              <span className="font-medium text-amber-700">Watch</span> (surfaced, not
              enterable yet) ·{' '}
              <span className="font-medium text-rose-700">Avoid</span> (distribution)
            </p>
          )}
          <p className="mt-0.5 text-xs text-slate-500">
            Swing trading · 3-week to 3-month typical hold · daily review ·{' '}
            <span className="font-mono">{data?.date ?? '—'}</span>
            {data?.generated_at && (
              <span className="ml-2 font-mono text-slate-400">
                · generated {fmtDateTimeIST(data.generated_at)}
              </span>
            )}
            {data && (
              <span className="ml-2 rounded bg-slate-100 px-2 py-0.5 text-xs uppercase tracking-wide text-slate-600">
                volume pipeline
              </span>
            )}
            {data?.demo_mode && (
              <span className="ml-2 rounded bg-rose-200 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-rose-900">
                ⚠ Demo data
              </span>
            )}
          </p>
          <div className="mt-2">
            <DataHealthPill />
          </div>
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

      {data?.regime && <RegimeBanner regime={data.regime} />}

      {data?.demo_mode && (
        <div className="mt-6">
          <DemoBanner />
        </div>
      )}

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
        {data && data.picks.length > 0 && (
          <div
            className={`grid grid-cols-1 gap-5 ${
              data.picks.length === 1
                ? 'lg:grid-cols-1 max-w-2xl'
                : data.picks.length === 2
                ? 'lg:grid-cols-2'
                : 'lg:grid-cols-3'
            }`}
          >
            {data.picks.map((p) => (
              <PickCard key={p.symbol} pick={p} />
            ))}
          </div>
        )}

        {/* NEW: fresh, delivery-led analysis over today's eligible field —
            its own ranking, independent of the picks above; never changes
            selection. Sits right after the canonical picks. */}
        {data && data.delivery_analysis && data.delivery_analysis.length > 0 && (
          <DeliveryWeightedPicks rows={data.delivery_analysis} />
        )}

        {data && data.picks.length === 0 && (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center">
            <div className="mx-auto h-12 w-12 rounded-full bg-slate-100 leading-[3rem] text-2xl">
              ⏸️
            </div>
            <h2 className="mt-4 text-lg font-semibold text-slate-900">
              {data.regime && !data.regime.passed
                ? 'Buy alerts halted'
                : 'Nothing actionable today'}
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-sm text-slate-600">
              {data.message ||
                (data.regime && !data.regime.passed
                  ? 'Market regime is off. No alerts will issue until NIFTY 100 closes above its 50-day moving average.'
                  : 'Nothing cleared the composite score threshold today. Check the panel below for the tickers closest to firing.')}
            </p>
          </div>
        )}

      </main>

      {/* Picks the scan surfaced that are NOT enterable today (late / extended /
          distribution). Own section below the buy list, for awareness only. */}
      {data?.not_actionable && data.not_actionable.length > 0 && (
        <NotActionablePanel rows={data.not_actionable} />
      )}

      {/* "Loaded spring" WATCH cohort — coiling bases still absorbing volume that
          have not broken out yet. Monitor only; renders null when empty. */}
      {data?.coiled_accumulators && data.coiled_accumulators.length > 0 && (
        <CoiledAccumulatorsPanel rows={data.coiled_accumulators} />
      )}

      {/* The full candidate pool — moved to the very bottom and shown on EVERY
          day (not only empty days), so the near-misses are always visible. */}
      {data?.closest_to_firing && (
        <ClosestToFiringPanel data={data.closest_to_firing} />
      )}

      {data && (
        <footer className="mt-8 text-xs text-slate-400">
          Generated at <span className="font-mono">{data.generated_at}</span> IST
        </footer>
      )}
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
