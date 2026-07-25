import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPositionAsOf, getPositionDates, fmtINR, fmtPct } from '../api'
import type { AccumulationGauge, Position } from '../types'

/**
 * Accumulation-strength header for a position card.
 *
 *   - A 5-segment horizontal bar, low -> high (left = weak/red, right =
 *     strong/dark-green). Segments up to the current level are filled.
 *   - The per-stock adversity buffer (ATR-based runway to the stop) and a
 *     plain-language "what to do" line.
 *   - A date picker to replay the position's strength as of any past trading
 *     day the symbol has an on-disk trace for (file-only; no live fetch).
 *
 * Advisory only — this changes no selection / sizing / exit decision.
 */

// Must match backend/accumulation_gauge.py LEVELS (index 0 = level 1).
const LEVEL_COLORS = ['#ef4444', '#f97316', '#f59e0b', '#10b981', '#059669']

function StrengthBar({ gauge }: { gauge: AccumulationGauge }) {
  const level = Math.max(1, Math.min(5, gauge.level))
  const tip = [gauge.message, '', ...gauge.reasons].join('\n')
  return (
    <div title={tip}>
      <div className="flex items-center gap-2">
        <div className="flex flex-1 gap-1" aria-label={`strength ${level} of 5`}>
          {LEVEL_COLORS.map((c, i) => {
            const on = i < level
            const active = i === level - 1
            return (
              <div
                key={i}
                className={`h-2.5 flex-1 rounded-full transition ${
                  active ? 'ring-2 ring-offset-1 ring-slate-400' : ''
                }`}
                style={{ backgroundColor: on ? c : '#e2e8f0' }}
              />
            )
          })}
        </div>
        <span
          className="rounded-full px-2 py-0.5 text-[10px] font-bold text-white"
          style={{ backgroundColor: gauge.color }}
        >
          {gauge.label}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-600">
        <span className="font-medium">{gauge.buffer_text}</span>
        {gauge.buffer_sessions != null && (
          <span className="font-mono text-slate-400">
            · ~{gauge.buffer_sessions} session{gauge.buffer_sessions === 1 ? '' : 's'} to stop
            {gauge.atr_pct != null && ` (ATR ${gauge.atr_pct}%/day)`}
          </span>
        )}
        {gauge.score != null && (
          <span className="font-mono text-slate-400">· score {gauge.score}/100</span>
        )}
      </div>
    </div>
  )
}

export function AccumulationStrength({ position: p }: { position: Position }) {
  const [asOf, setAsOf] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)

  const datesQ = useQuery({
    queryKey: ['position-dates', p.symbol],
    queryFn: () => getPositionDates(p.symbol),
    enabled: pickerOpen,
    staleTime: 5 * 60 * 1000,
  })

  const asOfQ = useQuery({
    queryKey: ['position-asof', p.symbol, asOf],
    queryFn: () => getPositionAsOf(p.symbol, asOf as string),
    enabled: !!asOf,
    staleTime: 5 * 60 * 1000,
  })

  const isHistorical = !!asOf
  const asOfData = asOfQ.data
  const gauge: AccumulationGauge | null | undefined =
    isHistorical && asOfData?.available
      ? asOfData.accumulation_gauge
      : p.accumulation_gauge

  const dates = datesQ.data?.dates ?? []

  return (
    <div className="mb-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          Accumulation strength
          {isHistorical && asOfData?.available && (
            <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 font-mono text-[9px] text-slate-700">
              AS OF {asOf}
              {asOfData.close != null && ` · ${fmtINR(asOfData.close)}`}
              {asOfData.pnl_pct != null && ` · ${fmtPct(asOfData.pnl_pct)}`}
            </span>
          )}
        </span>
        <div className="flex items-center gap-1">
          <select
            value={asOf ?? 'latest'}
            onFocus={() => setPickerOpen(true)}
            onMouseDown={() => setPickerOpen(true)}
            onChange={(e) =>
              setAsOf(e.target.value === 'latest' ? null : e.target.value)
            }
            className="rounded-md border border-slate-300 bg-white px-1.5 py-0.5 font-mono text-[10px] text-slate-700"
            title="Replay this position's strength as of a past date"
          >
            <option value="latest">Latest</option>
            {dates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          {isHistorical && (
            <button
              type="button"
              onClick={() => setAsOf(null)}
              className="rounded-md border border-slate-300 bg-white px-1.5 py-0.5 text-[10px] font-semibold text-slate-600 hover:bg-slate-100"
            >
              Latest
            </button>
          )}
        </div>
      </div>

      {isHistorical && asOfQ.isLoading && (
        <p className="text-[11px] text-slate-500">Loading {asOf}…</p>
      )}
      {isHistorical && asOfData && !asOfData.available && (
        <p className="text-[11px] text-amber-700">
          No trace for {asOf} (market holiday or not scanned).
        </p>
      )}
      {gauge ? (
        <StrengthBar gauge={gauge} />
      ) : (
        !isHistorical && (
          <p className="text-[11px] text-slate-400">Strength unavailable.</p>
        )
      )}
    </div>
  )
}
