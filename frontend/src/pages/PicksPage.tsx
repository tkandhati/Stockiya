import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Sparkles } from 'lucide-react'
import { fetchPicks, refreshPicks } from '../api'
import { Disclaimer } from '../components/Disclaimer'
import { DemoBanner } from '../components/DemoBanner'
import { PickCard } from '../components/PickCard'
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
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-900">
            <Sparkles className="h-5 w-5 text-amber-500" />
            Today&apos;s Top 3
          </h1>
          <p className="mt-1 text-sm text-slate-700">
            <span className="font-medium">Don&apos;t invent. Follow the institutions.
            Pick one.</span>
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            Investing, not trading · daily review · 3-6 month hold ·{' '}
            <span className="font-mono">{data?.date ?? '—'}</span>
            {data && (
              <span className="ml-2 rounded bg-slate-100 px-2 py-0.5 text-xs uppercase tracking-wide text-slate-600">
                {data.source === 'llm' ? 'AI-picked' : 'rule-based fallback'}
              </span>
            )}
            {data?.demo_mode && (
              <span className="ml-2 rounded bg-rose-200 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-rose-900">
                ⚠ Demo data
              </span>
            )}
          </p>
        </div>
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
      </header>

      {data?.demo_mode && (
        <div className="mt-6">
          <DemoBanner />
        </div>
      )}

      <div className="mt-6">
        <Disclaimer />
      </div>

      <main className="mt-8">
        {isLoading && <SkeletonGrid />}
        {isError && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
            <div className="font-semibold">Could not load picks.</div>
            <div className="mt-1 font-mono text-xs">{(error as Error).message}</div>
            <div className="mt-2 text-xs text-rose-700">
              Check that the backend is running on http://localhost:8000 and that
              ANTHROPIC_API_KEY is set in <code>backend/.env</code>.
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

        {data && data.picks.length === 0 && (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center">
            <div className="mx-auto h-12 w-12 rounded-full bg-slate-100 leading-[3rem] text-2xl">
              ⏸️
            </div>
            <h2 className="mt-4 text-lg font-semibold text-slate-900">
              Nothing actionable today
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-sm text-slate-600">
              No Nifty 50 stock currently shows a clean long-term institutional-accumulation
              footprint that meets the gate. The right move is to{' '}
              <span className="font-semibold">do nothing</span> and check back tomorrow.
            </p>
            <p className="mx-auto mt-3 max-w-xl text-xs text-slate-500">
              Quality over quantity. We'd rather show 0 picks than pad with weak setups —
              capital preserved is capital available for the next real signal.
            </p>
          </div>
        )}
      </main>

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
