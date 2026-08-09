import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import {
  ChartNoAxesCombined,
  Crosshair,
  RefreshCw,
  ScanSearch,
  Search,
  ShieldCheck,
  TestTube2,
} from 'lucide-react'
import { fmtDateTimeIST } from '../../api'
import { DataHealthPill } from '../../components/DataHealthPill'
import { StrategyTabs } from '../../components/StrategyTabs'
import { fetchPriceTrends, lookupPriceTrend } from './api'
import { PriceTrendCard } from './PriceTrendCard'
import type { PriceTrendResponse } from './types'

export function PriceTrendPage() {
  const queryClient = useQueryClient()
  const [symbol, setSymbol] = useState('')
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['price-trends'],
    queryFn: () => fetchPriceTrends(),
    staleTime: 15 * 60 * 1000,
  })
  const refresh = useMutation({
    mutationFn: () => fetchPriceTrends(true),
    onSuccess: (response: PriceTrendResponse) => {
      queryClient.setQueryData(['price-trends'], response)
    },
  })
  const lookup = useMutation({ mutationFn: lookupPriceTrend })

  function submitLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const requested = symbol.trim()
    if (requested) lookup.mutate(requested)
  }

  const readyCount = data?.candidates.filter((item) => item.status === 'ready').length ?? 0
  const nearest = data?.candidates.reduce<number | null>((best, item) => {
    const distance = Math.max(0, item.distance_to_breakout_pct)
    return best == null || distance < best ? distance : best
  }, null)

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <StrategyTabs />

      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-900">
            <ChartNoAxesCombined className="h-5 w-5 text-indigo-600" />
            Price Breakout Watch
          </h1>
          <p className="mt-1 text-sm font-medium text-slate-700">
            Find tightening price structures before they clear resistance.
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Price-only · trend alignment · higher lows · range compression ·{' '}
            <span className="font-mono">{data?.as_of ?? '—'}</span>
            {data?.generated_at && (
              <span className="ml-2 font-mono text-slate-400">
                · generated {fmtDateTimeIST(data.generated_at)}
              </span>
            )}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <DataHealthPill />
            <span className="rounded bg-indigo-50 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-indigo-700">
              no volume inputs
            </span>
          </div>
        </div>
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending || isLoading}
          className="flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${refresh.isPending ? 'animate-spin' : ''}`} />
          {refresh.isPending ? 'Scanning…' : 'Refresh scan'}
        </button>
      </header>

      {data?.demo_mode && (
        <div className="mt-6">
          <PriceTrendDemoBanner />
        </div>
      )}

      <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-bold text-slate-900">
              <Search className="h-4 w-4 text-indigo-600" />
              Check one stock
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Run any symbol through the same Price Trend logic, even when it is outside the configured universe.
            </p>
          </div>
          <form onSubmit={submitLookup} className="flex w-full gap-2 sm:w-auto">
            <label htmlFor="price-trend-symbol" className="sr-only">
              Stock symbol
            </label>
            <input
              id="price-trend-symbol"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              maxLength={30}
              autoComplete="off"
              placeholder="e.g. TATAPOWER or AAPL"
              className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm uppercase text-slate-900 outline-none transition placeholder:font-sans placeholder:normal-case placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 sm:w-64"
            />
            <button
              type="submit"
              disabled={!symbol.trim() || lookup.isPending}
              className="flex shrink-0 items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Search className="h-4 w-4" />
              {lookup.isPending ? 'Checkingâ€¦' : 'Check stock'}
            </button>
          </form>
        </div>

        {lookup.isError && (
          <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
            Could not check this symbol: {(lookup.error as Error).message}
          </div>
        )}

        {lookup.data && (
          <div className="mt-5 border-t border-slate-100 pt-5">
            {lookup.data.candidate ? (
              <div className="max-w-md">
                <PriceTrendCard candidate={lookup.data.candidate} mode="lookup" />
              </div>
            ) : (
              <div
                className={`rounded-xl border p-4 text-sm ${
                  lookup.data.price_history_available
                    ? 'border-amber-200 bg-amber-50 text-amber-900'
                    : 'border-slate-200 bg-slate-50 text-slate-700'
                }`}
              >
                <div className="font-semibold">
                  {lookup.data.resolved_symbol ?? lookup.data.requested_symbol}
                </div>
                <p className="mt-1">{lookup.data.message}</p>
              </div>
            )}
          </div>
        )}
      </section>

      {data && (
        <section className="mt-7 grid grid-cols-1 gap-3 sm:grid-cols-3" aria-label="Scan summary">
          <SummaryTile
            icon={<Crosshair className="h-4 w-4 text-emerald-600" />}
            label="Ready near pivot"
            value={String(readyCount)}
            detail="Strict price setup"
          />
          <SummaryTile
            icon={<ScanSearch className="h-4 w-4 text-indigo-600" />}
            label="Nearest breakout"
            value={nearest == null ? '—' : `${nearest.toFixed(1)}%`}
            detail="Distance to 55-day high"
          />
          <SummaryTile
            icon={<ShieldCheck className="h-4 w-4 text-slate-600" />}
            label="Small V1 scan"
            value={`${data.scanned_count}`}
            detail={`${data.universe} · top ${data.candidates.length} shown`}
          />
        </section>
      )}

      <main className="mt-6">
        {isLoading && <PriceTrendSkeleton />}
        {isError && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
            <div className="font-semibold">Could not run the price scan.</div>
            <div className="mt-1 font-mono text-xs">{(error as Error).message}</div>
            <div className="mt-2 text-xs text-rose-700">
              Check that the backend is running on http://localhost:8000.
            </div>
          </div>
        )}

        {data && data.candidates.length > 0 && (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
            {data.candidates.map((candidate) => (
              <PriceTrendCard key={candidate.symbol} candidate={candidate} />
            ))}
          </div>
        )}

        {data && data.candidates.length === 0 && (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-slate-100">
              <ScanSearch className="h-6 w-6 text-slate-500" />
            </div>
            <h2 className="mt-4 text-lg font-semibold text-slate-900">
              No price structures are close enough today
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-sm text-slate-600">
              The scan deliberately rejects completed breakouts and distant setups. It will surface a stock only when price is above its long-term trend and within 8% of a 55-day pivot.
            </p>
          </div>
        )}
      </main>

      {data && (
        <footer className="mt-8 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
          <span>
            {data.eligible_count} eligible · {data.skipped_count} outside the setup or unavailable
          </span>
          <span>Price structure only; volume strategy remains separate.</span>
        </footer>
      )}
    </div>
  )
}

function PriceTrendDemoBanner() {
  return (
    <div className="rounded-xl border-2 border-rose-300 bg-rose-50 px-4 py-3 text-rose-950">
      <div className="flex gap-3">
        <TestTube2 className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />
        <div>
          <div className="text-xs font-bold uppercase tracking-wide text-rose-800">
            Demo mode — synthetic price history
          </div>
          <p className="mt-1 text-sm leading-relaxed">
            These are bundled price fixtures for UI development, not today&apos;s market prices. The price-structure calculations are real, but the results are not tradeable.
          </p>
        </div>
      </div>
    </div>
  )
}

function SummaryTile({
  icon,
  label,
  value,
  detail,
}: {
  icon: ReactNode
  label: string
  value: string
  detail: string
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-xs font-medium text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-xl font-bold text-slate-900">{value}</div>
      <div className="mt-0.5 text-[11px] text-slate-400">{detail}</div>
    </div>
  )
}

function PriceTrendSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2, 3, 4, 5].map((item) => (
        <div
          key={item}
          className="h-[31rem] animate-pulse rounded-2xl border border-slate-200 bg-white p-5"
        >
          <div className="h-5 w-1/3 rounded bg-slate-200" />
          <div className="mt-4 h-7 w-1/2 rounded bg-slate-200" />
          <div className="mt-5 h-20 rounded bg-slate-100" />
          <div className="mt-4 h-28 rounded bg-slate-100" />
          <div className="mt-4 grid grid-cols-2 gap-2">
            <div className="h-12 rounded bg-slate-100" />
            <div className="h-12 rounded bg-slate-100" />
            <div className="h-12 rounded bg-slate-100" />
            <div className="h-12 rounded bg-slate-100" />
          </div>
        </div>
      ))}
    </div>
  )
}
