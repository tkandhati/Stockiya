import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Check, Copy, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { fetchDataHealth } from '../api'
import type { HealthItem, HealthOverall, HealthStatus } from '../types'

const OVERALL_HEADER: Record<HealthOverall, { bg: string; label: string; dot: string }> = {
  green:  { bg: 'bg-emerald-50  border-emerald-200', label: 'All data healthy',          dot: 'bg-emerald-500' },
  yellow: { bg: 'bg-amber-50    border-amber-200',   label: 'Some warnings',              dot: 'bg-amber-500'   },
  red:    { bg: 'bg-rose-50     border-rose-300',    label: 'Critical data missing',      dot: 'bg-rose-500'    },
}

const STATUS_BADGE: Record<HealthStatus, string> = {
  ok:    'bg-emerald-100 text-emerald-800 border-emerald-200',
  warn:  'bg-amber-100   text-amber-900   border-amber-200',
  error: 'bg-rose-100    text-rose-900    border-rose-200',
}

const STATUS_DOT: Record<HealthStatus, string> = {
  ok:    'bg-emerald-500',
  warn:  'bg-amber-500',
  error: 'bg-rose-500',
}

const STATUS_LABEL: Record<HealthStatus, string> = {
  ok:    'OK',
  warn:  'WARN',
  error: 'ERROR',
}

export function DataHealthPage() {
  const qc = useQueryClient()
  const { data, isLoading, isError, error, isFetching } = useQuery({
    queryKey: ['data-health'],
    queryFn: fetchDataHealth,
    staleTime: 30 * 1000,
  })

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-6 flex items-center justify-between gap-4">
        <Link
          to="/"
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to picks
        </Link>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ['data-health'] })}
          disabled={isFetching}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          {isFetching ? 'Rechecking…' : 'Recheck'}
        </button>
      </div>

      <h1 className="text-2xl font-bold text-slate-900">Data health</h1>
      <p className="mt-1 text-sm text-slate-600">
        Status of every file the pipeline depends on. If something here is red,
        the picks on the main screen are running on incomplete data — fix it before
        trusting today's recommendation.
      </p>

      {isLoading && (
        <div className="mt-6 h-32 animate-pulse rounded-2xl border border-slate-200 bg-white" />
      )}

      {isError && (
        <div className="mt-6 rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
          <div className="font-semibold">Could not reach the data-health probe.</div>
          <div className="mt-1 font-mono text-xs">{(error as Error).message}</div>
        </div>
      )}

      {data && (
        <>
          <div className={`mt-6 flex items-center gap-3 rounded-2xl border p-4 ${OVERALL_HEADER[data.overall].bg}`}>
            <span className={`h-3 w-3 rounded-full ${OVERALL_HEADER[data.overall].dot}`} />
            <div className="flex-1">
              <div className="font-semibold text-slate-900">
                {OVERALL_HEADER[data.overall].label}
              </div>
              <div className="mt-0.5 text-xs text-slate-600">
                {data.summary.ok} OK · {data.summary.warn} warn · {data.summary.error} error ·
                {' '}checked {fmtRelative(data.checked_at)}
              </div>
            </div>
          </div>

          {data.groups.map((g) => (
            <section key={g.name} className="mt-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                {g.name}
              </h2>
              <div className="mt-2 overflow-hidden rounded-2xl border border-slate-200 bg-white">
                {g.items.map((it, idx) => (
                  <HealthRow key={it.id} item={it} divider={idx > 0} />
                ))}
              </div>
            </section>
          ))}

          <p className="mt-6 text-xs text-slate-500">
            The probe lives in <code className="font-mono">backend/data_health.py</code>.
            Failures here used to be silently ignored — they are now surfaced so you can
            verify before relying on the picks.
          </p>
        </>
      )}
    </div>
  )
}

function HealthRow({ item, divider }: { item: HealthItem; divider: boolean }) {
  return (
    <div className={`flex items-start gap-4 p-4 ${divider ? 'border-t border-slate-100' : ''}`}>
      <span className={`mt-1 h-2.5 w-2.5 flex-shrink-0 rounded-full ${STATUS_DOT[item.status]}`} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-900">{item.label}</span>
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${STATUS_BADGE[item.status]}`}>
            {STATUS_LABEL[item.status]}
          </span>
          <code className="truncate font-mono text-xs text-slate-500">{item.path}</code>
        </div>
        <div className="mt-1 text-sm text-slate-700">{item.detail}</div>
        {item.last_modified && (
          <div className="mt-0.5 text-xs text-slate-400">
            last modified {fmtRelative(item.last_modified)}
          </div>
        )}
        {item.fix && <FixCommand cmd={item.fix} />}
      </div>
    </div>
  )
}

function FixCommand({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = () => {
    navigator.clipboard.writeText(cmd).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <div className="mt-2 flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <code className="flex-1 truncate font-mono text-xs text-slate-700">{cmd}</code>
      <button
        onClick={onCopy}
        className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
        title="Copy fix command"
      >
        {copied ? (
          <>
            <Check className="h-3 w-3" /> Copied
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" /> Copy
          </>
        )}
      </button>
    </div>
  )
}

function fmtRelative(iso: string): string {
  try {
    const t = new Date(iso).getTime()
    const dt = (Date.now() - t) / 1000
    if (Number.isNaN(dt)) return iso
    if (dt < 60)       return `${Math.floor(dt)}s ago`
    if (dt < 3600)     return `${Math.floor(dt / 60)}m ago`
    if (dt < 86400)    return `${Math.floor(dt / 3600)}h ago`
    return `${Math.floor(dt / 86400)}d ago`
  } catch {
    return iso
  }
}
