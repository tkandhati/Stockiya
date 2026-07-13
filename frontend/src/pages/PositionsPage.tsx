import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Briefcase } from 'lucide-react'
import { fetchPositions } from '../api'
import { PositionCard } from '../components/PositionCard'
import { Disclaimer } from '../components/Disclaimer'
import type { Position } from '../types'

/**
 * My Positions.
 *
 * Two sections (V1 — ownership + user-actual entry):
 *   Suggested — scanner emitted, user hasn't acted. Take / Decline.
 *   Held      — user marked paper / live. Monitored with real-fill guidance.
 *
 * Declined rows are filtered server-side and never rendered here.
 */
export function PositionsPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['positions'],
    queryFn: fetchPositions,
    staleTime: 60 * 1000,
  })

  const positions = data?.positions ?? []
  const { suggested, held } = useMemo(() => {
    const s: Position[] = []
    const h: Position[] = []
    for (const p of positions) {
      if (p.ownership === 'suggested') s.push(p)
      else h.push(p) // paper | live
    }
    return { suggested: s, held: h }
  }, [positions])

  const actionCounts = held.reduce<Record<string, number>>((acc, p) => {
    acc[p.action] = (acc[p.action] || 0) + 1
    return acc
  }, {})

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
      >
        <ArrowLeft className="h-4 w-4" /> Today&apos;s buy alerts
      </Link>

      <header className="mt-4">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-900">
          <Briefcase className="h-5 w-5 text-indigo-600" />
          My positions
        </h1>
        <p className="mt-1 text-sm text-slate-700">
          Scanner suggestions on top; positions you took below. Guidance
          re-anchors on your actual fill if you enter one.
        </p>
        {data && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded bg-slate-200 px-2 py-1 font-mono text-slate-800">
              {suggested.length} suggested
            </span>
            <span className="rounded bg-indigo-100 px-2 py-1 font-mono text-indigo-900">
              {held.length} held
            </span>
            {Object.entries(actionCounts).map(([action, n]) => (
              <span
                key={action}
                className="rounded bg-slate-100 px-2 py-1 font-mono text-slate-600"
              >
                {action}: {n}
              </span>
            ))}
            <span className="ml-auto font-mono text-slate-500">
              {data.date_ist}
            </span>
          </div>
        )}
      </header>

      <div className="mt-6">
        <Disclaimer />
      </div>

      <main className="mt-6 space-y-8">
        {isLoading && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {[0, 1].map((i) => (
              <div
                key={i}
                className="h-56 animate-pulse rounded-2xl border border-slate-200 bg-white p-5"
              />
            ))}
          </div>
        )}

        {isError && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
            <div className="font-semibold">Could not load positions.</div>
            <div className="mt-1 font-mono text-xs">
              {(error as Error).message}
            </div>
          </div>
        )}

        {data && positions.length === 0 && (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center">
            <div className="mx-auto h-12 w-12 rounded-full bg-slate-100 leading-[3rem] text-2xl">
              📭
            </div>
            <h2 className="mt-4 text-lg font-semibold text-slate-900">
              No positions yet
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-sm text-slate-600">
              Once a buy alert is emitted, it appears here as a suggestion.
              Confirm you took it (paper or live) to lock in your actual
              fill and start monitored guidance.
            </p>
          </div>
        )}

        {data && suggested.length > 0 && (
          <Section
            title="Suggested"
            subtitle="Scanner emitted, you haven't acted. Take (paper / live) or Decline."
          >
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {suggested.map((p) => (
                <PositionCard key={p.pick_id} position={p} />
              ))}
            </div>
          </Section>
        )}

        {data && held.length > 0 && (
          <Section
            title="Held"
            subtitle="Guidance below reflects your actual fill where you entered one."
          >
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {held.map((p) => (
                <PositionCard key={p.pick_id} position={p} />
              ))}
            </div>
          </Section>
        )}
      </main>
    </div>
  )
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: React.ReactNode
}) {
  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-800">
          {title}
        </h2>
        <span className="text-xs text-slate-500">{subtitle}</span>
      </div>
      {children}
    </section>
  )
}
