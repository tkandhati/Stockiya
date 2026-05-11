import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ShoppingCart,
  Target,
  ShieldAlert,
  AlertTriangle,
  CalendarClock,
} from 'lucide-react'
import { fetchStockDetail, fmtINR, fmtPct } from '../api'
import { AccumulationCard } from '../components/AccumulationCard'
import { PriceSparkline } from '../components/PriceSparkline'
import { ExitScenarios } from '../components/ExitScenarios'
import { ReasoningChecklist } from '../components/ReasoningChecklist'
import { DemoBanner } from '../components/DemoBanner'

export function StockDetailPage() {
  const { symbol = '' } = useParams<{ symbol: string }>()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['stock', symbol],
    queryFn: () => fetchStockDetail(symbol),
    enabled: !!symbol,
    staleTime: 60 * 1000,
  })

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
      >
        <ArrowLeft className="h-4 w-4" /> Back to picks
      </Link>

      {isLoading && (
        <div className="mt-6 h-72 animate-pulse rounded-2xl border border-slate-200 bg-white" />
      )}

      {isError && (
        <div className="mt-6 rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
          <div className="font-semibold">Could not load {symbol}.</div>
          <div className="mt-1 font-mono text-xs">{(error as Error).message}</div>
        </div>
      )}

      {data?.demo_mode && (
        <div className="mt-6">
          <DemoBanner />
        </div>
      )}

      {data && (
        <>
          <header className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <div>
                <h1 className="text-2xl font-bold text-slate-900">{data.company}</h1>
                <div className="mt-1 flex items-center gap-3 text-sm text-slate-500">
                  <span className="font-mono">{data.symbol}</span>
                  {data.sector && <span>· {data.sector}</span>}
                  {data.industry && (
                    <span className="text-slate-400">· {data.industry}</span>
                  )}
                </div>
              </div>
              <div className="text-right">
                <div className="text-3xl font-semibold tabular-nums text-slate-900">
                  {fmtINR(data.current)}
                </div>
                <div
                  className={`text-sm font-medium ${
                    data.day_change_pct == null
                      ? 'text-slate-500'
                      : data.day_change_pct >= 0
                      ? 'text-emerald-700'
                      : 'text-rose-700'
                  }`}
                >
                  {fmtPct(data.day_change_pct)} today
                </div>
              </div>
            </div>

            <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-slate-100 pt-4 text-sm md:grid-cols-4">
              <Mini label="52w high" value={fmtINR(data.fifty_two_w_high)} />
              <Mini label="52w low" value={fmtINR(data.fifty_two_w_low)} />
              <Mini label="200-day MA" value={fmtINR(data.ma200)} />
              <Mini
                label="vs 200d MA"
                value={
                  data.current != null && data.ma200
                    ? fmtPct((data.current / data.ma200 - 1) * 100)
                    : '—'
                }
              />
              <Mini label="3m return" value={fmtPct(data.return_3m_pct)} />
              <Mini label="1y return" value={fmtPct(data.return_1y_pct)} />
              <Mini
                label="Weinstein"
                value={data.accumulation?.weinstein_stage ?? 'undefined'}
              />
              <Mini
                label="Wyckoff"
                value={data.accumulation?.wyckoff_phase ?? 'indeterminate'}
              />
            </dl>
          </header>

          {data.pick_today && (
            <section className="mt-6 rounded-2xl border border-amber-200 bg-amber-50/60 p-6">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-800">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                Today&apos;s recommendation
              </div>
              <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-4">
                <RecStat
                  icon={<ShoppingCart className="h-4 w-4" />}
                  label="Best buy at"
                  value={fmtINR(data.pick_today.best_buy_at)}
                />
                <RecStat
                  icon={<Target className="h-4 w-4" />}
                  label="Sell target"
                  value={fmtINR(data.pick_today.sell_target)}
                  extra={`${fmtPct(data.pick_today.upside_pct)} upside`}
                />
                <RecStat
                  icon={<ShieldAlert className="h-4 w-4" />}
                  label="Stop-loss"
                  value={fmtINR(data.pick_today.stop_loss)}
                  extra={`${fmtPct(data.pick_today.downside_pct)} from buy`}
                />
                <RecStat
                  icon={<CalendarClock className="h-4 w-4" />}
                  label="Target window"
                  value={data.pick_today.target_window.label}
                  extra="hold horizon"
                />
              </div>
              <p className="mt-3 rounded-lg bg-indigo-50/60 px-3 py-2 text-xs text-indigo-900">
                <span className="font-semibold">Why this window: </span>
                {data.pick_today.target_window.rationale}
              </p>
              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-800">
                    Bull case · why we picked it
                  </div>
                  <p className="mt-1.5 text-sm leading-relaxed text-emerald-950">
                    {data.pick_today.rationale}
                  </p>
                </div>
                <div className="rounded-xl border border-rose-200 bg-rose-50/60 p-4">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-rose-800">
                    <AlertTriangle className="h-3 w-3" /> Bear case · risks we accepted
                  </div>
                  <p className="mt-1.5 text-sm leading-relaxed text-rose-950">
                    {data.pick_today.risks}
                  </p>
                </div>
              </div>
            </section>
          )}

          {data.pick_today && data.pick_today.reasoning?.length > 0 && (
            <section className="mt-6">
              <ReasoningChecklist
                points={data.pick_today.reasoning}
                defaultOpen
                compact={false}
              />
            </section>
          )}

          {/* PRIMARY: volume signature — the heart of the analysis */}
          <section className="mt-6">
            <AccumulationCard accum={data.accumulation} />
          </section>

          {data.pick_today && (
            <section className="mt-6">
              <ExitScenarios pick={data.pick_today} />
            </section>
          )}

          <section className="mt-6">
            <PriceSparkline data={data.history_6m} />
          </section>
        </>
      )}
    </div>
  )
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="font-medium tabular-nums text-slate-900">{value}</dd>
    </div>
  )
}

function RecStat({
  icon,
  label,
  value,
  extra,
}: {
  icon: React.ReactNode
  label: string
  value: string
  extra?: string
}) {
  return (
    <div className="rounded-xl border border-amber-100 bg-white p-4">
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-slate-500">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-slate-900">
        {value}
      </div>
      {extra && <div className="text-xs text-slate-500">{extra}</div>}
    </div>
  )
}
