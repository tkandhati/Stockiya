import type { StockDetail, ValuationSignals } from '../types'
import { fmtNum, fmtPct } from '../api'

const verdictTone: Record<NonNullable<ValuationSignals['pe_vs_sector']>, string> = {
  cheap: 'bg-emerald-100 text-emerald-700',
  fair: 'bg-slate-100 text-slate-700',
  expensive: 'bg-rose-100 text-rose-700',
}

export function ValuationCard({ detail }: { detail: StockDetail }) {
  const v = detail.valuation
  const verdict = v.pe_vs_sector
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-baseline justify-between">
        <h3 className="font-semibold text-slate-900">Valuation</h3>
        {verdict && (
          <span
            className={`rounded px-2 py-0.5 text-xs font-medium uppercase tracking-wide ${verdictTone[verdict]}`}
          >
            {verdict}
          </span>
        )}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <Row label="P/E" value={fmtNum(detail.pe)} />
        <Row label="Sector median P/E" value={fmtNum(v.sector_pe_median)} />
        <Row label="P/B" value={fmtNum(detail.pb)} />
        <Row label="ROE" value={fmtPct(detail.roe_pct)} />
        <Row label="Dividend yield" value={fmtPct(detail.dividend_yield_pct)} />
        <Row label="Debt / Equity" value={fmtNum(detail.debt_to_equity)} />
      </dl>

      <div className="mt-5 space-y-3 border-t border-slate-100 pt-4 text-sm">
        <Bar
          label="Position in 52-week range"
          help="0% = at year low, 100% = at year high"
          pct={v.price_in_52w_band_pct ?? null}
        />
        <Row
          label="Price vs 200-day MA"
          value={fmtPct(v.price_vs_200dma_pct)}
          color={
            v.price_vs_200dma_pct == null
              ? undefined
              : v.price_vs_200dma_pct >= 0
              ? 'text-emerald-700'
              : 'text-rose-700'
          }
        />
      </div>
    </div>
  )
}

function Row({
  label,
  value,
  color,
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      <dd className={`text-right tabular-nums font-medium ${color ?? 'text-slate-900'}`}>
        {value}
      </dd>
    </>
  )
}

function Bar({
  label,
  help,
  pct,
}: {
  label: string
  help?: string
  pct: number | null
}) {
  const safePct = pct == null ? null : Math.max(0, Math.min(100, pct))
  return (
    <div>
      <div className="flex items-center justify-between text-slate-500">
        <span>{label}</span>
        <span className="tabular-nums text-slate-900">
          {pct == null ? '—' : `${pct.toFixed(0)}%`}
        </span>
      </div>
      {help && <div className="text-xs text-slate-400">{help}</div>}
      <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-100">
        {safePct != null && (
          <div
            className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-amber-400"
            style={{ width: `${safePct}%` }}
          />
        )}
      </div>
    </div>
  )
}
