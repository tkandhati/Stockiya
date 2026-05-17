import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchDataHealth } from '../api'
import type { HealthOverall } from '../types'

const DOT: Record<HealthOverall, string> = {
  green:  'bg-emerald-500',
  yellow: 'bg-amber-500',
  red:    'bg-rose-500',
}

const PILL_BG: Record<HealthOverall, string> = {
  green:  'border-emerald-200 bg-emerald-50  text-emerald-800  hover:bg-emerald-100',
  yellow: 'border-amber-200   bg-amber-50    text-amber-900    hover:bg-amber-100',
  red:    'border-rose-300    bg-rose-50     text-rose-900     hover:bg-rose-100',
}

export function DataHealthPill() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['data-health'],
    queryFn: fetchDataHealth,
    staleTime: 30 * 1000,        // refresh every 30s when stale
    refetchInterval: 60 * 1000,  // background poll every 60s
  })

  if (isLoading) {
    return (
      <Link
        to="/health"
        className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-500"
      >
        <span className="h-2 w-2 animate-pulse rounded-full bg-slate-300" />
        Data: checking…
      </Link>
    )
  }

  if (isError || !data) {
    return (
      <Link
        to="/health"
        className="inline-flex items-center gap-2 rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-xs font-medium text-rose-800 hover:bg-rose-100"
        title="Data-health probe could not be reached"
      >
        <span className="h-2 w-2 rounded-full bg-rose-500" />
        Data: probe offline
      </Link>
    )
  }

  const { overall, summary } = data
  const ok = summary.ok
  const total = summary.total
  const tooltip =
    `${summary.ok} OK · ${summary.warn} warn · ${summary.error} error · click for details`

  return (
    <Link
      to="/health"
      title={tooltip}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium transition ${PILL_BG[overall]}`}
    >
      <span className={`h-2 w-2 rounded-full ${DOT[overall]}`} />
      Data: {ok}/{total} OK
      {summary.error > 0 && (
        <span className="ml-1 rounded bg-rose-200 px-1.5 text-[10px] font-bold text-rose-900">
          {summary.error} ✕
        </span>
      )}
      {summary.warn > 0 && summary.error === 0 && (
        <span className="ml-1 rounded bg-amber-200 px-1.5 text-[10px] font-bold text-amber-900">
          {summary.warn} !
        </span>
      )}
    </Link>
  )
}
