import { useState } from 'react'
import type { ClosestRow, ClosestToFiring } from '../types'

// Trader-UI rule: four columns, every one earns its place.
//   S      = the number that decides selection
//   Gap    = distance from firing (τ − S)
//   Held   = the one stage that would flip this ticker if fully fired
//   Reason = one line of *why* that stage scored low
//
// No chart, no evidence blocks, no colour salad. If something below is not
// helping the trader pick tomorrow's watchlist or the CTO tune weights, cut it.

type Tab = 'accumulation' | 'breakout' | 'overall'

const TAB_LABEL: Record<Tab, string> = {
  accumulation: 'Accumulation',
  breakout: 'Breakout',
  overall: 'Overall',
}

const TAB_DESC: Record<Tab, string> = {
  accumulation: 'Ranked by ACS + AC weighted margin',
  breakout: 'Ranked by LT + CS + VD + BR weighted margin',
  overall: 'Ranked by composite score S',
}

export function ClosestToFiringPanel({ data }: { data: ClosestToFiring }) {
  const counts: Record<Tab, number> = {
    accumulation: data.accumulation?.length ?? 0,
    breakout:     data.breakout?.length ?? 0,
    overall:      data.overall?.length ?? 0,
  }
  const [tab, setTab] = useState<Tab>(() => {
    // Default to whichever tab has picks; fall back to overall.
    if (counts.accumulation > 0) return 'accumulation'
    if (counts.breakout > 0) return 'breakout'
    return 'overall'
  })

  const rows: ClosestRow[] = data[tab] ?? []
  const total = counts.accumulation + counts.breakout + counts.overall
  if (total === 0) return null

  return (
    <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-sm font-semibold text-slate-900">
          Closest to firing
        </h3>
        <span className="text-xs text-slate-500">
          {TAB_DESC[tab]}
        </span>
      </div>

      <div className="mt-3 flex gap-1 border-b border-slate-200">
        {(['accumulation', 'breakout', 'overall'] as Tab[]).map((t) => {
          const active = t === tab
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium transition ${
                active
                  ? 'border-indigo-500 text-indigo-700'
                  : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {TAB_LABEL[t]} ({counts[t]})
            </button>
          )
        })}
      </div>

      {rows.length === 0 ? (
        <p className="mt-4 text-xs text-slate-500">
          Nothing eligible in this tab today.
        </p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
              <th className="py-2 pr-3 font-medium">Symbol</th>
              <th className="py-2 pr-3 font-medium">S</th>
              <th className="py-2 pr-3 font-medium">Gap</th>
              <th className="py-2 pr-3 font-medium">Held back by</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.symbol} className="text-slate-700">
                <td className="py-2 pr-3 font-mono text-xs">
                  <div className="font-semibold">{r.symbol.replace('.NS', '')}</div>
                  <div className="text-[10px] text-slate-400">{r.company}</div>
                </td>
                <td className="py-2 pr-3 font-mono">
                  {r.composite_score.toFixed(3)}
                </td>
                <td className="py-2 pr-3 font-mono text-rose-600">
                  {r.gap_to_tau > 0 ? `-${r.gap_to_tau.toFixed(3)}` : r.gap_to_tau.toFixed(3)}
                </td>
                <td className="py-2 pr-3">
                  <div className="text-xs font-medium text-slate-800">
                    {r.pulled_down_by.label || '—'}
                  </div>
                  <div className="text-[10px] text-slate-500 truncate max-w-[24rem]"
                       title={r.pulled_down_by.reason}>
                    {r.pulled_down_by.reason || '—'}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
