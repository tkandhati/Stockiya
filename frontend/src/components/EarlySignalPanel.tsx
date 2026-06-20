import { Link } from 'react-router-dom'
import { AlertTriangle, Eye, TrendingDown, TrendingUp } from 'lucide-react'
import type { EarlyVolumeSignal } from '../types'

function toneFor(direction: EarlyVolumeSignal['direction']) {
  if (direction === 'bullish') {
    return {
      wrap: 'border-emerald-200 bg-emerald-50/60',
      pill: 'bg-emerald-100 text-emerald-900',
      icon: <TrendingUp className="h-4 w-4" />,
      action: 'Watch for follow-through',
    }
  }
  if (direction === 'bearish') {
    return {
      wrap: 'border-rose-200 bg-rose-50/60',
      pill: 'bg-rose-100 text-rose-900',
      icon: <TrendingDown className="h-4 w-4" />,
      action: 'Exit / avoid warning',
    }
  }
  return {
    wrap: 'border-slate-200 bg-slate-50',
    pill: 'bg-slate-100 text-slate-700',
    icon: <Eye className="h-4 w-4" />,
    action: 'Needs confirmation',
  }
}

export function EarlySignalPanel({ items }: { items: EarlyVolumeSignal[] }) {
  if (!items.length) return null

  return (
    <section className="mt-8 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-800">
            <Eye className="h-4 w-4 text-indigo-600" />
            Early volume indications
          </h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-600">
            These are not buy alerts. They flag abnormal volume bars early:
            accumulation/ignition for watchlist candidates, distribution/climax
            for exit or avoid checks.
          </p>
        </div>
        <span className="rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-600">
          {items.length} signal{items.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        {items.map((item) => {
          const tone = toneFor(item.direction)
          const ev = item.event
          return (
            <Link
              key={`${item.symbol}-${item.kind}`}
              to={`/stock/${encodeURIComponent(item.symbol)}`}
              className={`block rounded-xl border p-4 transition hover:-translate-y-0.5 hover:shadow-sm ${tone.wrap}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${tone.pill}`}>
                      {tone.icon}
                      {item.label}
                    </span>
                    <span className="font-mono text-xs text-slate-500">
                      score {item.score.toFixed(2)}
                    </span>
                  </div>
                  <h3 className="mt-2 truncate text-sm font-semibold text-slate-900">
                    {item.company || item.symbol}
                  </h3>
                  <div className="mt-0.5 font-mono text-xs text-slate-500">
                    {item.symbol}
                  </div>
                </div>
                {item.direction === 'bearish' && (
                  <AlertTriangle className="h-4 w-4 flex-shrink-0 text-rose-600" />
                )}
              </div>

              <p className="mt-3 text-xs leading-relaxed text-slate-700">
                {item.detail}
              </p>

              <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] md:grid-cols-4">
                <Mini label="Volume" value={ev.vol_ratio_50 != null ? `${ev.vol_ratio_50.toFixed(2)}x` : '-'} />
                <Mini label="Quiet 5/50" value={ev.quiet_ratio_5_50 != null ? `${ev.quiet_ratio_5_50.toFixed(2)}x` : '-'} />
                <Mini label="Close loc" value={ev.close_location != null ? `${Math.round(ev.close_location * 100)}%` : '-'} />
                <Mini label="Vs 50d MA" value={ev.close_vs_ma50_pct != null ? `${ev.close_vs_ma50_pct >= 0 ? '+' : ''}${ev.close_vs_ma50_pct.toFixed(1)}%` : '-'} />
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
                <span className={`rounded px-2 py-0.5 font-semibold ${tone.pill}`}>
                  {tone.action}
                </span>
                {item.failed_gate && (
                  <span className="rounded bg-white/70 px-2 py-0.5 font-mono text-slate-600">
                    not alert: failed {item.failed_gate.stage_id}
                  </span>
                )}
                {item.stage_reached && !item.failed_gate && (
                  <span className="rounded bg-white/70 px-2 py-0.5 font-mono text-slate-600">
                    reached {item.stage_reached}
                  </span>
                )}
              </div>
            </Link>
          )
        })}
      </div>
    </section>
  )
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/70 bg-white/60 px-2 py-1">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="font-mono font-semibold text-slate-800">{value}</div>
    </div>
  )
}
