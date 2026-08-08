import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Crosshair,
  MoveUpRight,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { fmtINR } from '../../api'
import type { PricePoint, PriceTrendCandidate, PriceTrendStatus } from './types'

const statusMeta: Record<
  PriceTrendStatus,
  { label: string; tone: string; bar: string }
> = {
  ready: {
    label: 'Ready near pivot',
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    bar: 'bg-emerald-500',
  },
  forming: {
    label: 'Structure forming',
    tone: 'border-sky-200 bg-sky-50 text-sky-800',
    bar: 'bg-sky-500',
  },
  watch: {
    label: 'Watch',
    tone: 'border-amber-200 bg-amber-50 text-amber-800',
    bar: 'bg-amber-500',
  },
}

export function PriceTrendCard({ candidate }: { candidate: PriceTrendCandidate }) {
  const status = statusMeta[candidate.status]
  const pivotProgress = Math.max(
    3,
    Math.min(100, 100 - (candidate.distance_to_breakout_pct / 8) * 100),
  )

  return (
    <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs font-semibold text-slate-400">
                #{candidate.rank}
              </span>
              <span
                className={`rounded-full border px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide ${status.tone}`}
              >
                {status.label}
              </span>
            </div>
            <Link
              to={`/stock/${encodeURIComponent(candidate.symbol)}`}
              className="mt-3 inline-flex items-center gap-1.5 text-xl font-bold text-slate-900 hover:text-indigo-700"
            >
              {candidate.symbol.replace('.NS', '')}
              <ArrowUpRight className="h-4 w-4" />
            </Link>
            <p className="truncate text-xs text-slate-500">{candidate.company}</p>
          </div>
          <ScoreBadge score={candidate.score} />
        </div>

        <MiniPriceChart points={candidate.price_history} />

        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <div className="flex items-end justify-between gap-4">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Current
              </div>
              <div className="mt-0.5 font-mono text-lg font-bold text-slate-900">
                {fmtINR(candidate.close)}
              </div>
            </div>
            <div className="text-right">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                55-day breakout
              </div>
              <div className="mt-0.5 font-mono text-lg font-bold text-indigo-700">
                {fmtINR(candidate.breakout_price)}
              </div>
            </div>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200">
            <div
              className={`h-full rounded-full ${status.bar}`}
              style={{ width: `${pivotProgress}%` }}
            />
          </div>
          <div className="mt-1.5 flex justify-between text-[11px] text-slate-500">
            <span>Price approach</span>
            <span className="font-semibold text-slate-700">
              {candidate.distance_to_breakout_pct <= 0
                ? 'At pivot'
                : `${candidate.distance_to_breakout_pct.toFixed(1)}% to pivot`}
            </span>
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-2 text-sm">
          <Metric label="10d range" value={`${candidate.range_10d_pct.toFixed(1)}%`} />
          <Metric label="ATR (14d)" value={`${candidate.atr_14d_pct.toFixed(1)}%`} />
          <Metric
            label="50d slope"
            value={`${candidate.ma50_slope_10d_pct >= 0 ? '+' : ''}${candidate.ma50_slope_10d_pct.toFixed(1)}%`}
          />
          <Metric
            label="Higher lows"
            value={`${candidate.higher_low_pct >= 0 ? '+' : ''}${candidate.higher_low_pct.toFixed(1)}%`}
          />
        </dl>

        <div className="mt-4 space-y-2">
          {candidate.reasons.map((reason) => (
            <div key={reason} className="flex gap-2 text-xs leading-relaxed text-slate-700">
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
              <span>{reason}</span>
            </div>
          ))}
        </div>

        {candidate.watchouts.length > 0 && (
          <div className="mt-4 rounded-lg border border-amber-100 bg-amber-50/70 p-3">
            <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-amber-800">
              <AlertTriangle className="h-3.5 w-3.5" />
              Still needs work
            </div>
            <p className="mt-1 text-xs leading-relaxed text-amber-900">
              {candidate.watchouts.join(' · ')}
            </p>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 border-t border-slate-100 bg-slate-50/80 px-5 py-3 text-xs">
        <div>
          <span className="text-slate-500">Price support</span>
          <div className="mt-0.5 font-mono font-semibold text-slate-800">
            {fmtINR(candidate.support_price)}
          </div>
        </div>
        <div className="text-right">
          <span className="text-slate-500">20d move</span>
          <div className="mt-0.5 font-mono font-semibold text-slate-800">
            {candidate.return_20d_pct >= 0 ? '+' : ''}
            {candidate.return_20d_pct.toFixed(1)}%
          </div>
        </div>
      </div>
    </article>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2.5">
      <dt className="text-[11px] text-slate-500">{label}</dt>
      <dd className="mt-0.5 font-mono font-semibold text-slate-800">{value}</dd>
    </div>
  )
}

function ScoreBadge({ score }: { score: number }) {
  return (
    <div className="flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-full border-4 border-indigo-100 bg-indigo-50 text-indigo-900">
      <span className="font-mono text-base font-bold leading-none">{score.toFixed(0)}</span>
      <span className="mt-0.5 text-[8px] font-bold uppercase tracking-wider">price</span>
    </div>
  )
}

function MiniPriceChart({ points }: { points: PricePoint[] }) {
  if (points.length < 2) return null
  const values = points.map((point) => point.close)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 0.01)
  const coordinates = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 100
      const y = 36 - ((value - min) / range) * 32
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')

  return (
    <div className="mt-4 rounded-lg bg-gradient-to-b from-indigo-50/60 to-transparent px-1 pt-1">
      <svg viewBox="0 0 100 40" className="h-20 w-full" preserveAspectRatio="none" aria-label="60-day price trend">
        <polyline
          points={coordinates}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          vectorEffect="non-scaling-stroke"
          className="text-indigo-500"
        />
      </svg>
      <div className="-mt-1 flex items-center justify-between px-1 text-[10px] text-slate-400">
        <span className="flex items-center gap-1"><MoveUpRight className="h-3 w-3" /> 60 sessions</span>
        <span className="flex items-center gap-1"><Crosshair className="h-3 w-3" /> price only</span>
      </div>
    </div>
  )
}
