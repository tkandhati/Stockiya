import { useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  CircleSlash,
  PlayCircle,
  XCircle,
} from 'lucide-react'
import { FlaskConical, Layers } from 'lucide-react'
import { fmtINR, fmtPct, runBacktest } from '../api'
import { Disclaimer } from '../components/Disclaimer'
import { RegimeBanner } from '../components/RegimeBanner'
import type {
  BacktestOverrides,
  BacktestResponse,
  BacktestSelected,
  BacktestSymbolBlock,
  ForwardWalk,
  ForwardWalkBar,
  ScanDay,
  ScanGateId,
  UniverseBucket,
  UniversePick,
  UniverseSummary,
} from '../types'

/**
 * BacktestPage — historical replay UI built on POST /api/backtest.
 *
 * Two tabs:
 *   - "Historical scan"  — picks across the universe over a date range.
 *                          Single-date edge → Mode B (one-day funnel).
 *                          Range            → Mode C universe (full history).
 *   - "Symbol check"     — one symbol over a date range, any Yahoo ticker
 *                          (not just Nifty 100). Single-date edge → Mode A
 *                          (gate explanation for one day). Range → Mode C
 *                          symbol (timeline + pass-list + forward walks).
 *
 * Explanation comes first. The numbers are secondary. The screen exists to
 * teach WHY each pick (or each rejection) happened.
 */

type TabId = 'historical' | 'symbol'
type WindowKey = '3M' | '6M' | '1Y' | '2Y'

const MIN_AS_OF = '2022-01-01'
const TODAY_ISO = new Date().toISOString().slice(0, 10)

const WINDOW_DAYS: Record<WindowKey, number> = {
  '3M': 92,
  '6M': 183,
  '1Y': 365,
  '2Y': 730,
}
const WINDOW_LABEL: Record<WindowKey, string> = {
  '3M': '3 months',
  '6M': '6 months',
  '1Y': '1 year',
  '2Y': '2 years',
}

// The 5 high-control thresholds we expose for backtest-only sensitivity tuning.
// Canonical = PRINCIPLES.md values. Off-canonical runs get an amber badge.
type OverrideKey = keyof BacktestOverrides

interface ThresholdSpec {
  key: OverrideKey
  gate: string
  label: string
  unit: string
  canonical: number
  min: number
  max: number
  step: number
  desc: string
}

const THRESHOLD_SPECS: ThresholdSpec[] = [
  {
    key: 'vd_dryup_ratio',
    gate: 'VD',
    label: 'VD dry-up ratio',
    unit: '× of 50d avg',
    canonical: 0.5,
    min: 0.3,
    max: 0.95,
    step: 0.05,
    desc:
      "Requires the prior 5-day avg volume to be below this fraction of the 50d avg. Lower = stricter dry-up. Canonical 0.50.",
  },
  {
    key: 'cs_atr_pct_max',
    gate: 'CS',
    label: 'CS ATR % max',
    unit: '%',
    canonical: 4.0,
    min: 2.0,
    max: 10.0,
    step: 0.5,
    desc:
      'Maximum daily true-range as % of close. Lower = demands tighter base. Canonical 4.0% (Minervini VCP).',
  },
  {
    key: 'lt_obv_90d_slope_min',
    gate: 'LT',
    label: 'LT OBV 90d slope min',
    unit: '%',
    canonical: 3.0,
    min: 0.0,
    max: 10.0,
    step: 0.5,
    desc:
      'Minimum OBV slope over the trailing 90 days. Higher = demands stronger accumulation. Canonical +3.0%.',
  },
  {
    key: 'br_volume_mult',
    gate: 'BR',
    label: 'BR volume multiplier',
    unit: '× adv(50)',
    canonical: 1.5,
    min: 1.0,
    max: 3.0,
    step: 0.1,
    desc:
      "Today's volume must be ≥ this × the 50d avg. Lower = accepts quieter breakouts. Canonical 1.5×.",
  },
  {
    key: 'hr_parabolic_30d_max_pct',
    gate: 'HR',
    label: 'HR parabolic 30d max',
    unit: '%',
    canonical: 25.0,
    min: 10.0,
    max: 60.0,
    step: 5.0,
    desc:
      'Reject if 30-day return exceeds this. Lower = more aggressive distribution-detection. Canonical 25%.',
  },
]

const CANONICAL: Record<OverrideKey, number> = THRESHOLD_SPECS.reduce(
  (acc, s) => ({ ...acc, [s.key]: s.canonical }),
  {} as Record<OverrideKey, number>,
)

function lastTradingDayBefore(today: string): string {
  // Default to "yesterday or last Friday". Yahoo gives EOD bars; today's
  // bar may not exist yet during Indian market hours.
  const d = new Date(today)
  d.setDate(d.getDate() - 1)
  while (d.getDay() === 0 || d.getDay() === 6) {
    d.setDate(d.getDate() - 1)
  }
  return d.toISOString().slice(0, 10)
}

function TabButton({
  active,
  onClick,
  icon,
  label,
  hint,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
  hint: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex flex-col gap-0.5 px-4 py-3 text-left transition ${
        active
          ? 'border-b-2 border-indigo-600 text-indigo-900'
          : 'border-b-2 border-transparent text-slate-600 hover:text-slate-900'
      }`}
    >
      <span className="flex items-center gap-2 text-sm font-semibold">
        {icon} {label}
      </span>
      <span
        className={`text-[11px] ${active ? 'text-indigo-700' : 'text-slate-500'}`}
      >
        {hint}
      </span>
    </button>
  )
}

function DeviationBanner({
  deviated,
}: {
  deviated: Record<string, { value: number; canonical: number }>
}) {
  const items = Object.entries(deviated)
  return (
    <div className="mt-4 rounded-lg border border-amber-400 bg-amber-100 px-4 py-3 text-xs text-amber-950">
      <strong>Exploratory — gate thresholds deviated from canonical.</strong>
      <ul className="mt-1 list-inside list-disc">
        {items.map(([key, dev]) => (
          <li key={key} className="font-mono">
            {key}: {dev.value} <span className="text-amber-700">(canonical {dev.canonical})</span>
          </li>
        ))}
      </ul>
      <p className="mt-1">
        Results are research only. Do not adjust the live strategy based on
        what you see here (PRINCIPLES Section 8).
      </p>
    </div>
  )
}

function OutsideUniverseBanner({ symbol }: { symbol: string }) {
  return (
    <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900">
      <strong>{symbol}</strong> isn't in the tuned Nifty 100 universe. The
      gate thresholds (OBV slope, ATR%, breakout volume) were calibrated
      against large-cap institutional patterns. Mid/small caps and non-NSE
      tickers may legitimately show different volume/divergence dynamics —
      treat results as exploratory, not prescriptive.
    </div>
  )
}

export function BacktestPage() {
  const lastDay = lastTradingDayBefore(TODAY_ISO)
  const [tab, setTab] = useState<TabId>('historical')
  const [windowKey, setWindowKey] = useState<WindowKey>('1Y')
  const [symbolsRaw, setSymbolsRaw] = useState<string>('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [advOpen, setAdvOpen] = useState<boolean>(false)
  const [overrides, setOverrides] = useState<Record<OverrideKey, number>>(() => ({
    ...CANONICAL,
  }))
  const overridesDeviated = useMemo(() => {
    const out: Partial<Record<OverrideKey, number>> = {}
    let any = false
    for (const s of THRESHOLD_SPECS) {
      if (overrides[s.key] !== s.canonical) {
        out[s.key] = overrides[s.key]
        any = true
      }
    }
    return { any, payload: out }
  }, [overrides])

  // Fixed defaults — hidden from the user per the simplified UI.
  // hold_days = 90 matches PRINCIPLES.md. top_n + capital are display-only knobs.
  const HOLD_DAYS = 90
  const TOP_N = 3
  const CAPITAL = 100000

  const mut = useMutation({ mutationFn: runBacktest })

  const symbols = useMemo(() => {
    return symbolsRaw
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)
  }, [symbolsRaw])

  // Derive start from windowKey: end - WINDOW_DAYS[windowKey]
  const { start, end } = useMemo(() => {
    const endD = new Date(lastDay)
    const startD = new Date(endD)
    startD.setDate(startD.getDate() - WINDOW_DAYS[windowKey])
    // Clamp to MIN_AS_OF
    const minD = new Date(MIN_AS_OF)
    if (startD < minD) startD.setTime(minD.getTime())
    return {
      start: startD.toISOString().slice(0, 10),
      end: endD.toISOString().slice(0, 10),
    }
  }, [windowKey, lastDay])

  const symbolMissing = tab === 'symbol' && symbols.length === 0
  const symbolTooMany = tab === 'symbol' && symbols.length > 1

  const switchTo = (next: TabId) => {
    if (next === tab) return
    setTab(next)
    setSymbolsRaw('')
    setExpanded(null)
  }

  const canRun = !mut.isPending && !symbolMissing && !symbolTooMany

  const onRun = () => {
    if (!canRun) return
    setExpanded(null)
    mut.mutate({
      as_of: start,
      end,
      symbols: symbols.length ? symbols : undefined,
      hold_days: HOLD_DAYS,
      top_n: TOP_N,
      capital: CAPITAL,
      overrides: overridesDeviated.any ? (overridesDeviated.payload as BacktestOverrides) : undefined,
    })
  }

  const resetOverrides = () => setOverrides({ ...CANONICAL })

  const resp = mut.data

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Backtest</h1>
          <p className="mt-1 text-sm text-slate-700">
            Replay the live picker against history. See what would have been
            alerted, why each rejection happened, and how the trade would have
            unfolded over the holding period.
          </p>
        </div>
        <Link
          to="/"
          className="flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:border-slate-400 hover:bg-slate-50"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to today's picks
        </Link>
      </header>

      {/* Tab nav */}
      <div className="mt-6 flex gap-2 border-b border-slate-200">
        <TabButton
          active={tab === 'historical'}
          onClick={() => switchTo('historical')}
          icon={<Layers className="h-4 w-4" />}
          label="Historical scan"
          hint="Every pick across the universe, by year / quarter"
        />
        <TabButton
          active={tab === 'symbol'}
          onClick={() => switchTo('symbol')}
          icon={<FlaskConical className="h-4 w-4" />}
          label="Symbol check"
          hint="When was THIS stock a right pick? (any Yahoo ticker)"
        />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          onRun()
        }}
        className="mt-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      >
        {tab === 'symbol' && (
          <div className="mb-4 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-900">
            <strong>Symbol check.</strong> Enter any ticker (Nifty 100 or not).
            We'll walk every trading day in your window and show which gates
            fired, on which days, and what the forward outcome was. Useful for
            "was this a right pick for our strategy?" research.
          </div>
        )}
        {tab === 'historical' && (
          <div className="mb-4 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-900">
            <strong>Historical scan.</strong> Pick a window (year, quarter,
            etc.) and we walk every trading day across the Nifty 100, listing
            every pick the strategy would have alerted plus its forward outcome.
            Leave symbols blank for the full universe, or list a few to filter.
          </div>
        )}

        <div className="space-y-4">
          {/* Window preset buttons */}
          <div>
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
              Window
            </span>
            <div className="mt-2 flex flex-wrap gap-2">
              {(Object.keys(WINDOW_DAYS) as WindowKey[]).map((k) => (
                <button
                  type="button"
                  key={k}
                  onClick={() => setWindowKey(k)}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                    windowKey === k
                      ? 'border-indigo-600 bg-indigo-600 text-white shadow'
                      : 'border-slate-300 bg-white text-slate-700 hover:border-slate-400'
                  }`}
                >
                  {WINDOW_LABEL[k]}
                </button>
              ))}
            </div>
            <p className="mt-1 font-mono text-[11px] text-slate-500">
              {start} → {end}
            </p>
          </div>

          {/* Symbol field */}
          <label className="block">
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
              {tab === 'symbol' ? (
                <>Symbol <span className="text-rose-600">*</span></>
              ) : (
                <>Symbols (optional filter)</>
              )}
            </span>
            <input
              type="text"
              placeholder={
                tab === 'symbol'
                  ? 'Any Yahoo ticker. e.g. BAJAJ-AUTO.NS, TANLA.NS, AAPL'
                  : 'Blank = full Nifty 100. Or list a few to filter.'
              }
              value={symbolsRaw}
              onChange={(e) => setSymbolsRaw(e.target.value)}
              className={`mt-1 w-full rounded-md border px-3 py-2 font-mono text-sm ${
                symbolMissing || symbolTooMany
                  ? 'border-rose-400 bg-rose-50'
                  : 'border-slate-300'
              }`}
            />
            <span
              className={`mt-1 block text-[11px] ${
                symbolMissing || symbolTooMany ? 'text-rose-700' : 'text-slate-500'
              }`}
            >
              {tab === 'symbol' && symbolMissing && 'Required'}
              {tab === 'symbol' && symbolTooMany && 'Enter exactly one symbol'}
              {tab === 'symbol' && symbols.length === 1 && `Will check: ${symbols[0]}`}
              {tab === 'historical' &&
                (symbols.length === 0
                  ? 'Scanning full Nifty 100'
                  : `Filtering to ${symbols.length} symbol${symbols.length === 1 ? '' : 's'}`)}
            </span>
          </label>
        </div>

        {/* Advanced section — sensitivity tuning. Off-canonical = amber badge. */}
        <div className="mt-5 border-t border-slate-200 pt-4">
          <button
            type="button"
            onClick={() => setAdvOpen((v) => !v)}
            className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-700 hover:text-slate-900"
          >
            <span>{advOpen ? '▾' : '▸'}</span>
            Advanced — gate threshold tuning (exploratory)
            {overridesDeviated.any && (
              <span className="ml-2 rounded bg-amber-200 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-900">
                deviated
              </span>
            )}
          </button>
          {advOpen && (
            <div className="mt-3 space-y-4 rounded-lg border border-amber-200 bg-amber-50/50 p-4">
              <p className="text-xs text-amber-900">
                <strong>Exploratory only.</strong> Adjusting these answers
                "what if I loosened/tightened this gate?" — the canonical
                strategy uses the values marked ◆. PRINCIPLES.md forbids
                hand-tuning live based on backtest results; treat any
                deviated result as research, not a strategy change.
              </p>
              {THRESHOLD_SPECS.map((s) => {
                const v = overrides[s.key]
                const isDev = v !== s.canonical
                return (
                  <div key={s.key} className="text-xs">
                    <div className="flex items-baseline justify-between gap-3">
                      <label className="font-semibold text-slate-800">
                        [{s.gate}] {s.label}
                      </label>
                      <div className="flex items-center gap-2 font-mono">
                        <span
                          className={`${isDev ? 'text-amber-900' : 'text-slate-700'}`}
                        >
                          {v.toFixed(s.step < 1 ? 2 : 1)} {s.unit}
                        </span>
                        <span className="text-slate-400">◆ {s.canonical}</span>
                      </div>
                    </div>
                    <input
                      type="range"
                      min={s.min}
                      max={s.max}
                      step={s.step}
                      value={v}
                      onChange={(e) =>
                        setOverrides((prev) => ({ ...prev, [s.key]: Number(e.target.value) }))
                      }
                      className="mt-1 w-full"
                    />
                    <p className="mt-0.5 text-[11px] text-slate-600">{s.desc}</p>
                  </div>
                )
              })}
              {overridesDeviated.any && (
                <button
                  type="button"
                  onClick={resetOverrides}
                  className="rounded border border-slate-300 bg-white px-3 py-1 text-[11px] font-semibold text-slate-700 hover:border-slate-400"
                >
                  Reset to canonical
                </button>
              )}
            </div>
          )}
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-slate-500">
            Hold = 90 days · Top-N = 3 · Capital = ₹1L (defaults; not user-tunable).
          </p>
          <button
            type="submit"
            disabled={!canRun}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <PlayCircle className={`h-4 w-4 ${mut.isPending ? 'animate-pulse' : ''}`} />
            {mut.isPending ? 'Running…' : 'Run backtest'}
          </button>
        </div>
      </form>

      {mut.isError && (
        <div className="mt-4 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <strong>Backtest failed:</strong>{' '}
          {(mut.error as Error)?.message ?? 'Unknown error'}
        </div>
      )}

      {resp?.error && (
        <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {resp.error}
        </div>
      )}

      {resp && !resp.error && (
        <>
          {resp.regime && <RegimeBanner regime={resp.regime} />}
          {/* Deviated-threshold advisory */}
          {resp.assumptions?.thresholds_deviated &&
            Object.keys(resp.assumptions.thresholds_deviated).length > 0 && (
              <DeviationBanner
                deviated={resp.assumptions.thresholds_deviated}
              />
            )}
          {/* Outside-universe advisory */}
          {(resp as { scope?: string }).scope === 'symbol' &&
            resp.symbol &&
            (resp as { in_universe?: boolean }).in_universe === false && (
              <OutsideUniverseBanner symbol={resp.symbol} />
            )}
          {resp.mode === 'A' &&
            (resp.symbols ?? []).some((b) => b.in_universe === false) && (
              <OutsideUniverseBanner
                symbol={
                  (resp.symbols ?? [])
                    .filter((b) => b.in_universe === false)
                    .map((b) => b.symbol)
                    .join(', ')
                }
              />
            )}
          <AssumptionsBanner resp={resp} />

          {resp.mode === 'A' && <ModeA resp={resp} />}
          {resp.mode === 'B' && (
            <ModeB
              resp={resp}
              expanded={expanded}
              onExpand={(sym) => setExpanded(expanded === sym ? null : sym)}
            />
          )}
          {resp.mode === 'C' && (
            (resp as { scope?: string }).scope === 'universe'
              ? <ModeCUniverse resp={resp} />
              : <ModeC resp={resp} />
          )}
        </>
      )}

      <div className="mt-8">
        <Disclaimer />
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Assumptions banner — echoes every default the user didn't fill in.
// --------------------------------------------------------------------------- //

function AssumptionsBanner({ resp }: { resp: BacktestResponse }) {
  const a = resp.assumptions
  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-700">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
        <span>
          <strong>Fill:</strong> {a.fill_model}
        </span>
        <span>
          <strong>Hold:</strong> {a.hold_days} days
        </span>
        <span>
          <strong>Top-N:</strong> {a.top_n}
        </span>
        <span>
          <strong>Capital:</strong> {fmtINR(a.capital)}
        </span>
        <span>
          <strong>Stop / T1 / T2:</strong> -{a.stop_pct}% / +{a.t1_pct}% / +{a.t2_pct}%
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-amber-800">
        <AlertTriangle className="h-3.5 w-3.5" />
        <span>
          {a.costs_modeled ? '' : 'Costs not modeled. '}
          {a.survivorship_note}
        </span>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Mode A — 1-2 symbols, deep explanation
// --------------------------------------------------------------------------- //

function ModeA({ resp }: { resp: BacktestResponse }) {
  if (!resp.symbols?.length) {
    return <p className="mt-6 text-sm text-slate-600">No symbols returned.</p>
  }
  return (
    <div className="mt-6 space-y-6">
      {resp.symbols.map((b) => (
        <SymbolBlock key={b.symbol} block={b} />
      ))}
    </div>
  )
}

function SymbolBlock({ block }: { block: BacktestSymbolBlock }) {
  if (block.error) {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
        <strong className="font-mono">{block.symbol}</strong>: {block.error}
      </div>
    )
  }

  const passed = block.passed_all_gates
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <header
        className={`flex flex-wrap items-center justify-between gap-3 rounded-t-xl border-b px-5 py-3 ${
          passed
            ? 'border-emerald-200 bg-emerald-50/50'
            : 'border-rose-200 bg-rose-50/50'
        }`}
      >
        <div className="flex items-center gap-2">
          {passed ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-700" />
          ) : (
            <XCircle className="h-5 w-5 text-rose-700" />
          )}
          <div>
            <div className="font-mono text-sm font-bold">
              {block.symbol}
              {block.snapshot?.company ? (
                <span className="ml-2 font-sans text-xs font-normal text-slate-600">
                  {block.snapshot.company}
                </span>
              ) : null}
            </div>
            <div className="text-xs text-slate-700">
              {passed
                ? 'PASSED all gates — would have been a pick candidate'
                : `REJECTED at [${block.killing_gate}] ${block.killing_gate_label}`}
            </div>
          </div>
        </div>
        {block.forward && (
          <div className="text-right">
            <div className="text-[11px] uppercase tracking-wide text-slate-500">
              forward outcome
            </div>
            <div
              className={`font-mono text-sm font-bold ${
                block.forward.return_pct >= 0 ? 'text-emerald-700' : 'text-rose-700'
              }`}
            >
              {fmtPct(block.forward.return_pct)} · {block.forward.exit_reason}
            </div>
          </div>
        )}
      </header>

      <GateChain block={block} />

      {block.counterfactual && !block.counterfactual.error && (
        <Counterfactual block={block} />
      )}

      {block.forward && <ForwardWalkPanel forward={block.forward} />}
    </div>
  )
}

function GateChain({ block }: { block: BacktestSymbolBlock }) {
  if (!block.chain) return null
  return (
    <div className="border-b border-slate-100 px-5 py-4">
      <h3 className="text-sm font-semibold text-slate-800">Gate-by-gate trace</h3>
      <ul className="mt-3 space-y-3">
        {block.chain.map((row) => (
          <li
            key={row.stage_id}
            className={`rounded-md border p-3 text-sm ${
              row.passed === true
                ? 'border-emerald-200 bg-emerald-50/40'
                : row.passed === false
                ? 'border-rose-300 bg-rose-50/60'
                : 'border-slate-200 bg-slate-50/60 opacity-70'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                {row.passed === true && (
                  <CheckCircle2 className="h-4 w-4 text-emerald-700" />
                )}
                {row.passed === false && (
                  <XCircle className="h-4 w-4 text-rose-700" />
                )}
                {row.passed === null && (
                  <CircleSlash className="h-4 w-4 text-slate-400" />
                )}
                <strong className="font-mono text-xs">
                  [{row.stage_id}] {row.label}
                </strong>
              </div>
              {row.passed === false && (
                <span className="rounded bg-rose-200 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-rose-900">
                  failed
                </span>
              )}
            </div>
            <p className="mt-1.5 text-slate-700">{row.explanation}</p>
            {row.evidence?.length > 0 && (
              <ul className="mt-2 list-inside list-disc space-y-0.5 font-mono text-[11px] text-slate-600">
                {row.evidence.map((ev, i) => (
                  <li key={i}>{ev}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Counterfactual({ block }: { block: BacktestSymbolBlock }) {
  const cf = block.counterfactual!
  const isCF = cf.is_counterfactual
  return (
    <div className="border-b border-slate-100 bg-amber-50/40 px-5 py-4">
      <h3 className="text-sm font-semibold text-slate-800">
        {isCF ? 'Counterfactual — the trade you didn\'t take' : 'Trade plan'}
      </h3>
      {isCF && (
        <p className="mt-1 text-xs text-slate-600">
          The gates rejected this. Below is what the trade WOULD have looked
          like if you'd ignored the rejection. Compare against the forward walk
          to see whether the gates were right.
        </p>
      )}
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs sm:grid-cols-4">
        <div>
          <span className="text-slate-500">Entry:</span>{' '}
          <strong>{fmtINR(cf.best_buy_at)}</strong>
        </div>
        <div>
          <span className="text-slate-500">Stop:</span>{' '}
          <strong className="text-rose-700">{fmtINR(cf.stop_loss)}</strong>
        </div>
        <div>
          <span className="text-slate-500">T2 target:</span>{' '}
          <strong className="text-emerald-700">{fmtINR(cf.sell_target)}</strong>
        </div>
        <div>
          <span className="text-slate-500">Shares:</span>{' '}
          <strong>{cf.shares_to_buy}</strong>
        </div>
      </div>
    </div>
  )
}

function ForwardWalkPanel({ forward }: { forward: ForwardWalk }) {
  return (
    <div className="px-5 py-4">
      <h3 className="text-sm font-semibold text-slate-800">
        Forward walk — what actually happened
      </h3>
      <p className="mt-1 text-xs text-slate-600">
        Bars after as-of, applying stop &middot; T1 (sell 50% + raise to BE)
        &middot; T2 ladder. Stop-first on same-bar conflicts.
      </p>

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <Stat label="Entry" v={fmtINR(forward.entry_px)} />
        <Stat label="Stop" v={fmtINR(forward.stop_px)} cls="text-rose-700" />
        <Stat label="T1" v={fmtINR(forward.t1_px)} cls="text-amber-700" />
        <Stat label="T2" v={fmtINR(forward.t2_px)} cls="text-emerald-700" />
        <Stat
          label="Exit reason"
          v={<span className="font-semibold">{forward.exit_reason}</span>}
        />
        <Stat label="Exit day" v={`${forward.exit_day}`} />
        <Stat label="Exit avg" v={fmtINR(forward.exit_px_avg)} />
        <Stat
          label="Return"
          v={
            <span
              className={
                forward.return_pct >= 0 ? 'text-emerald-700' : 'text-rose-700'
              }
            >
              {fmtPct(forward.return_pct)}
            </span>
          }
        />
      </div>

      {forward.daily_path.length > 0 && (
        <ForwardChart forward={forward} />
      )}
    </div>
  )
}

function Stat({
  label,
  v,
  cls,
}: {
  label: string
  v: React.ReactNode
  cls?: string
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`font-mono text-sm font-bold ${cls ?? ''}`}>{v}</div>
    </div>
  )
}

// Lightweight SVG chart: closes line + stop/T1/T2 horizontal lines + event dots
function ForwardChart({ forward }: { forward: ForwardWalk }) {
  const W = 720
  const H = 200
  const pad = { l: 50, r: 12, t: 12, b: 22 }
  const path = forward.daily_path
  if (!path.length) return null

  const allYs = [
    ...path.map((b: ForwardWalkBar) => b.low),
    ...path.map((b: ForwardWalkBar) => b.high),
    forward.stop_px,
    forward.t1_px,
    forward.t2_px,
    forward.entry_px,
  ]
  const yMin = Math.min(...allYs)
  const yMax = Math.max(...allYs)
  const yRange = Math.max(1, yMax - yMin)
  const yPad = yRange * 0.05
  const yLo = yMin - yPad
  const yHi = yMax + yPad

  const x = (i: number) =>
    pad.l + (i * (W - pad.l - pad.r)) / Math.max(1, path.length - 1)
  const y = (v: number) =>
    pad.t + ((yHi - v) * (H - pad.t - pad.b)) / (yHi - yLo)

  const closeLine = path
    .map((b: ForwardWalkBar, i: number) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(b.close)}`)
    .join(' ')

  return (
    <div className="mt-4 overflow-x-auto rounded-md border border-slate-200 bg-slate-50/40 p-2">
      <svg width={W} height={H} className="block">
        {/* Horizontal threshold lines */}
        <line
          x1={pad.l}
          x2={W - pad.r}
          y1={y(forward.stop_px)}
          y2={y(forward.stop_px)}
          stroke="#fb7185"
          strokeDasharray="4 3"
        />
        <text x={pad.l - 4} y={y(forward.stop_px) + 4} fontSize="10" textAnchor="end" fill="#9f1239">
          stop {forward.stop_px.toFixed(0)}
        </text>
        <line
          x1={pad.l}
          x2={W - pad.r}
          y1={y(forward.t1_px)}
          y2={y(forward.t1_px)}
          stroke="#f59e0b"
          strokeDasharray="4 3"
        />
        <text x={pad.l - 4} y={y(forward.t1_px) + 4} fontSize="10" textAnchor="end" fill="#92400e">
          T1 {forward.t1_px.toFixed(0)}
        </text>
        <line
          x1={pad.l}
          x2={W - pad.r}
          y1={y(forward.t2_px)}
          y2={y(forward.t2_px)}
          stroke="#10b981"
          strokeDasharray="4 3"
        />
        <text x={pad.l - 4} y={y(forward.t2_px) + 4} fontSize="10" textAnchor="end" fill="#065f46">
          T2 {forward.t2_px.toFixed(0)}
        </text>
        <line
          x1={pad.l}
          x2={W - pad.r}
          y1={y(forward.entry_px)}
          y2={y(forward.entry_px)}
          stroke="#64748b"
        />
        <text x={pad.l - 4} y={y(forward.entry_px) + 4} fontSize="10" textAnchor="end" fill="#334155">
          entry {forward.entry_px.toFixed(0)}
        </text>

        {/* Close line */}
        <path d={closeLine} stroke="#4338ca" strokeWidth="1.5" fill="none" />

        {/* Event markers */}
        {path.map((b: ForwardWalkBar, i: number) => {
          if (!b.event) return null
          const color =
            b.event === 't1'
              ? '#f59e0b'
              : b.event === 't2'
              ? '#10b981'
              : b.event === 'stop'
              ? '#dc2626'
              : b.event === 'be_stop'
              ? '#dc2626'
              : '#6366f1'
          return (
            <g key={i}>
              <circle cx={x(i)} cy={y(b.close)} r={4} fill={color} />
              <text
                x={x(i)}
                y={y(b.close) - 8}
                fontSize="9"
                textAnchor="middle"
                fill={color}
              >
                {b.event}
              </text>
            </g>
          )
        })}

        {/* x-axis tick labels (first, mid, last) */}
        {[0, Math.floor(path.length / 2), path.length - 1].map((i) => (
          <text
            key={i}
            x={x(i)}
            y={H - 6}
            fontSize="9"
            textAnchor="middle"
            fill="#64748b"
          >
            {path[i].date}
          </text>
        ))}
      </svg>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Mode B — universe scan
// --------------------------------------------------------------------------- //

function ModeB({
  resp,
  expanded,
  onExpand,
}: {
  resp: BacktestResponse
  expanded: string | null
  onExpand: (sym: string) => void
}) {
  return (
    <div className="mt-6 space-y-6">
      <SelectedPicks resp={resp} expanded={expanded} onExpand={onExpand} />
      {/* Per-gate funnel at the bottom for diagnostic clarity */}
      <Funnel resp={resp} />
    </div>
  )
}

function Funnel({ resp }: { resp: BacktestResponse }) {
  const rows = resp.funnel ?? []
  const max = Math.max(...rows.map((r) => r.eval), 1)
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-800">Gate funnel</h2>
      <p className="mt-0.5 text-xs text-slate-600">
        How the universe narrows through each gate. Top failure reason shown
        on the right.
      </p>
      <div className="mt-4 space-y-2">
        {rows.map((r) => {
          const w = (r.eval / max) * 100
          return (
            <div key={r.stage_id}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <div className="font-mono">
                  [{r.stage_id}]{' '}
                  <span className="font-sans font-semibold text-slate-800">
                    {r.label}
                  </span>
                </div>
                <div className="font-mono text-slate-700">
                  eval {r.eval} · pass {r.pass} · fail {r.fail}
                </div>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <div className="h-3 flex-1 rounded bg-slate-100">
                  <div
                    className="h-3 rounded bg-emerald-500/70"
                    style={{ width: `${(r.pass / max) * 100}%` }}
                  />
                </div>
                <div className="w-20 text-right font-mono text-[11px] text-slate-500">
                  {Math.round(w)}%
                </div>
              </div>
              {r.top_reason && r.fail > 0 && (
                <div className="ml-1 mt-0.5 font-mono text-[10px] text-rose-700">
                  ↳ top fail: {r.top_reason}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SelectedPicks({
  resp,
  expanded,
  onExpand,
}: {
  resp: BacktestResponse
  expanded: string | null
  onExpand: (sym: string) => void
}) {
  const selected = resp.selected ?? []
  const s = resp.summary
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-slate-800">
          Selected picks (top {resp.assumptions.top_n})
        </h2>
        {s && s.n_picks > 0 && (
          <div className="text-xs text-slate-700">
            Hit-rate{' '}
            <strong>
              {s.hit_rate_pct != null ? `${s.hit_rate_pct}%` : '—'}
            </strong>{' '}
            · avg return{' '}
            <strong
              className={
                (s.avg_return_pct ?? 0) >= 0 ? 'text-emerald-700' : 'text-rose-700'
              }
            >
              {s.avg_return_pct != null ? fmtPct(s.avg_return_pct) : '—'}
            </strong>{' '}
            · sum{' '}
            <strong
              className={
                (s.sum_return_pct ?? 0) >= 0 ? 'text-emerald-700' : 'text-rose-700'
              }
            >
              {s.sum_return_pct != null ? fmtPct(s.sum_return_pct) : '—'}
            </strong>
          </div>
        )}
      </header>

      {selected.length === 0 ? (
        <p className="mt-3 text-sm text-slate-600">
          No picks on this date. {resp.regime?.passed === false
            ? 'Regime was halted — no buys issue.'
            : 'No survivors cleared all gates.'}
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {selected.map((p) => (
            <SelectedRow
              key={p.symbol}
              pick={p}
              isOpen={expanded === p.symbol}
              onToggle={() => onExpand(p.symbol)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function SelectedRow({
  pick,
  isOpen,
  onToggle,
}: {
  pick: BacktestSelected
  isOpen: boolean
  onToggle: () => void
}) {
  const f = pick.forward
  const ret = f?.return_pct ?? null
  return (
    <li className="rounded-lg border border-slate-200 bg-white">
      <button
        onClick={onToggle}
        className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50"
      >
        <div className="flex items-center gap-3">
          <span className="rounded bg-indigo-100 px-2 py-0.5 font-mono text-xs font-bold text-indigo-900">
            #{pick.rank}
          </span>
          <span className="font-mono text-sm font-bold">{pick.symbol}</span>
          <span className="text-xs text-slate-600">{pick.company}</span>
          <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">
            conf {pick.confirmation.score?.toFixed(2) ?? '—'}
          </span>
        </div>
        <div className="text-right">
          {ret != null ? (
            <span
              className={`font-mono text-sm font-bold ${
                ret >= 0 ? 'text-emerald-700' : 'text-rose-700'
              }`}
            >
              {fmtPct(ret)} · {f?.exit_reason}
            </span>
          ) : (
            <span className="font-mono text-xs text-slate-500">
              no forward data
            </span>
          )}
        </div>
      </button>
      {isOpen && f && (
        <div className="border-t border-slate-100 px-4 py-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
            <Stat label="Entry" v={fmtINR(f.entry_px)} />
            <Stat label="Stop" v={fmtINR(f.stop_px)} cls="text-rose-700" />
            <Stat label="T1" v={fmtINR(f.t1_px)} cls="text-amber-700" />
            <Stat label="T2" v={fmtINR(f.t2_px)} cls="text-emerald-700" />
            <Stat label="Exit reason" v={f.exit_reason} />
            <Stat label="Exit day" v={`${f.exit_day}`} />
            <Stat label="Exit avg" v={fmtINR(f.exit_px_avg)} />
            <Stat
              label="Return"
              v={
                <span
                  className={ret! >= 0 ? 'text-emerald-700' : 'text-rose-700'}
                >
                  {fmtPct(ret!)}
                </span>
              }
            />
          </div>
          <ForwardChart forward={f} />
        </div>
      )}
    </li>
  )
}

// --------------------------------------------------------------------------- //
// Mode C — Gate Timeline scan
// --------------------------------------------------------------------------- //

const SCAN_GATES: ScanGateId[] = ['U', 'I', 'HR', 'LT', 'CS', 'VD', 'BR']
const GATE_LABEL: Record<ScanGateId, string> = {
  U: 'Universe',
  I: 'Ingest',
  HR: 'Hard rejects',
  LT: 'Long-term flow',
  CS: 'Consolidation',
  VD: 'Volume/Divergence',
  BR: 'Breakout',
}

function ModeC({ resp }: { resp: BacktestResponse }) {
  const [focusedDate, setFocusedDate] = useState<string | null>(null)

  const timeline = resp.timeline ?? []
  const counts = resp.counts ?? {}
  const fullPasses = resp.full_passes ?? []
  const passDates = resp.pass_dates_by_gate ?? {}

  const focusedDay = timeline.find((d) => d.date === focusedDate) || null
  const focusedPass = fullPasses.find((p) => p.as_of === focusedDate) || null

  return (
    <div className="mt-6 space-y-6">
      {/* Header summary */}
      <div className="rounded-xl border border-violet-200 bg-violet-50 p-5">
        <h2 className="text-sm font-semibold text-violet-900">
          Gate Timeline · <span className="font-mono">{resp.symbol}</span>
        </h2>
        <p className="mt-1 text-xs text-violet-900/80">
          {resp.start} → {resp.end} · {resp.trading_days} trading days walked.
          Each cell below is one EOD bar. Green = gate passed, red = failed,
          grey = not reached (an earlier gate already killed it).
        </p>
        <p className="mt-2 text-xs text-violet-900/80">
          <strong>{fullPasses.length}</strong> day{fullPasses.length === 1 ? '' : 's'} cleared{' '}
          <strong>all gates</strong> in this window
          {fullPasses.length > 0 && ' — these are the dates where the live strategy would have alerted.'}
        </p>
      </div>

      {/* Timeline strip */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">Daily gate status</h3>
        <p className="mt-0.5 text-xs text-slate-600">
          Click any column to focus the day. Hover any cell to see the date.
        </p>
        <TimelineStrip
          timeline={timeline}
          onPick={(d) => setFocusedDate(focusedDate === d ? null : d)}
          focused={focusedDate}
        />
      </div>

      {/* VD pass list — the specific thing the user asked for */}
      <PassListPanel
        gateId="VD"
        dates={passDates['VD'] ?? []}
        timeline={timeline}
        fullPasses={fullPasses}
        onPick={(d) => setFocusedDate(d)}
      />

      {/* Also show the other "rare" gate */}
      <PassListPanel
        gateId="BR"
        dates={passDates['BR'] ?? []}
        timeline={timeline}
        fullPasses={fullPasses}
        onPick={(d) => setFocusedDate(d)}
      />

      {/* Vol-ratio sparkline */}
      <VolRatioSpark
        series={resp.vol_ratio_series ?? []}
        focused={focusedDate}
        onPick={(d) => setFocusedDate(d)}
      />

      {/* Focused-day detail panel */}
      {focusedDay && (
        <DayDetailPanel day={focusedDay} fullPass={focusedPass} />
      )}

      {/* Per-gate funnel at the bottom for diagnostic clarity */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">Per-gate hit count</h3>
        <p className="mt-0.5 text-xs text-slate-600">
          Across all {resp.trading_days} trading days scanned, how often each
          gate fired. Sharp drop = the chokepoint.
        </p>
        <table className="mt-3 w-full text-xs">
          <thead className="border-b text-left text-slate-600">
            <tr>
              <th className="py-1.5 font-medium">Gate</th>
              <th className="py-1.5 font-medium">Evaluated</th>
              <th className="py-1.5 font-medium">Passed</th>
              <th className="py-1.5 font-medium">Failed</th>
              <th className="py-1.5 font-medium">Pass rate</th>
            </tr>
          </thead>
          <tbody>
            {SCAN_GATES.filter((g) => counts[g]).map((g) => {
              const c = counts[g]
              const rate = c.eval > 0 ? Math.round((c.pass / c.eval) * 100) : 0
              return (
                <tr key={g} className="border-b border-slate-100">
                  <td className="py-1.5 font-mono">
                    [{g}] {GATE_LABEL[g]}
                  </td>
                  <td className="py-1.5 font-mono">{c.eval}</td>
                  <td className="py-1.5 font-mono text-emerald-700">{c.pass}</td>
                  <td className="py-1.5 font-mono text-rose-700">{c.fail}</td>
                  <td className="py-1.5 font-mono">{rate}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function TimelineStrip({
  timeline,
  onPick,
  focused,
}: {
  timeline: ScanDay[]
  onPick: (date: string) => void
  focused: string | null
}) {
  if (timeline.length === 0) return null
  const cellW = Math.max(3, Math.min(8, Math.floor(900 / timeline.length)))
  return (
    <div className="mt-3 overflow-x-auto">
      <div
        className="inline-grid"
        style={{
          gridTemplateColumns: `auto repeat(${timeline.length}, ${cellW}px)`,
          gridTemplateRows: `repeat(${SCAN_GATES.length}, 14px) 18px`,
          gap: '2px',
        }}
      >
        {/* Row labels + cells */}
        {SCAN_GATES.map((gate) => (
          <FragmentRow
            key={gate}
            gate={gate}
            timeline={timeline}
            cellW={cellW}
            focused={focused}
            onPick={onPick}
          />
        ))}

        {/* Bottom x-axis: first, middle, last date */}
        <div />
        {timeline.map((d, i) => {
          const showLabel =
            i === 0 || i === timeline.length - 1 || i === Math.floor(timeline.length / 2)
          return (
            <div
              key={`x-${d.date}`}
              className="text-[9px] text-slate-500"
              style={{ width: cellW, textAlign: 'center' }}
            >
              {showLabel ? d.date.slice(2, 7) : ''}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function FragmentRow({
  gate,
  timeline,
  cellW,
  focused,
  onPick,
}: {
  gate: ScanGateId
  timeline: ScanDay[]
  cellW: number
  focused: string | null
  onPick: (date: string) => void
}) {
  return (
    <>
      <div className="pr-2 text-right font-mono text-[10px] leading-[14px] text-slate-700">
        {gate}
      </div>
      {timeline.map((d) => {
        const v = d.gates[gate]
        const isFocused = focused === d.date
        const bg =
          v === true
            ? 'bg-emerald-500'
            : v === false
            ? 'bg-rose-500'
            : 'bg-slate-200'
        return (
          <button
            key={`${gate}-${d.date}`}
            title={`${d.date} · [${gate}] ${
              v === true ? 'passed' : v === false ? 'failed' : 'not reached'
            }${d.killed_at ? ` · killed at [${d.killed_at}]` : ''}`}
            onClick={() => onPick(d.date)}
            style={{ width: cellW, height: 14 }}
            className={`${bg} ${
              isFocused ? 'ring-2 ring-indigo-600 ring-offset-1' : ''
            } cursor-pointer rounded-[2px]`}
          />
        )
      })}
    </>
  )
}

function PassListPanel({
  gateId,
  dates,
  timeline,
  fullPasses,
  onPick,
}: {
  gateId: ScanGateId
  dates: string[]
  timeline: ScanDay[]
  fullPasses: { as_of: string }[]
  onPick: (date: string) => void
}) {
  const tlByDate = useMemo(() => {
    const m = new Map<string, ScanDay>()
    for (const d of timeline) m.set(d.date, d)
    return m
  }, [timeline])
  const fullPassSet = useMemo(() => new Set(fullPasses.map((p) => p.as_of)), [fullPasses])

  if (dates.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">
          Dates where [{gateId}] {GATE_LABEL[gateId]} passed
        </h3>
        <p className="mt-2 text-sm text-slate-600">
          None in this window. The gate is genuinely strict — if you want to see
          examples, try a different symbol or a wider date range (1+ year for
          mid-caps tends to surface a few).
        </p>
      </div>
    )
  }
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-800">
        Dates where [{gateId}] {GATE_LABEL[gateId]} passed
        <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 font-mono text-[10px] text-emerald-900">
          {dates.length}
        </span>
      </h3>
      <ul className="mt-3 divide-y divide-slate-100 text-sm">
        {dates.map((d) => {
          const row = tlByDate.get(d)
          const isFullPass = fullPassSet.has(d)
          const f = row?.features ?? {}
          return (
            <li key={d}>
              <button
                onClick={() => onPick(d)}
                className="flex w-full items-center justify-between gap-3 py-2 text-left hover:bg-slate-50"
              >
                <span className="flex items-center gap-3">
                  <span className="font-mono text-xs">{d}</span>
                  {isFullPass && (
                    <span className="rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
                      full pass
                    </span>
                  )}
                </span>
                <span className="font-mono text-[11px] text-slate-600">
                  {gateId === 'VD' && f.vol_ratio_5_50 != null
                    ? `ratio ${Math.round(f.vol_ratio_5_50 * 100)}%`
                    : ''}
                  {gateId === 'VD' && f.divergence_form
                    ? ` · ${f.divergence_form} div`
                    : ''}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function VolRatioSpark({
  series,
  focused,
  onPick,
}: {
  series: { date: string; ratio_5_50: number | null; vd_passed: boolean | null }[]
  focused: string | null
  onPick: (date: string) => void
}) {
  if (series.length === 0) return null
  const W = 720
  const H = 120
  const pad = { l: 40, r: 8, t: 8, b: 16 }
  const pts = series.filter((p) => p.ratio_5_50 != null) as {
    date: string
    ratio_5_50: number
    vd_passed: boolean | null
  }[]
  if (pts.length === 0) return null
  const ys = pts.map((p) => p.ratio_5_50)
  const yMin = Math.min(0.0, ...ys)
  const yMax = Math.max(1.0, ...ys)
  const x = (i: number) =>
    pad.l + (i * (W - pad.l - pad.r)) / Math.max(1, series.length - 1)
  const y = (v: number) =>
    pad.t + ((yMax - v) * (H - pad.t - pad.b)) / (yMax - yMin)
  const path = series
    .map((p, i) =>
      p.ratio_5_50 == null
        ? null
        : `${i === 0 || series[i - 1]?.ratio_5_50 == null ? 'M' : 'L'} ${x(i)} ${y(
            p.ratio_5_50,
          )}`,
    )
    .filter(Boolean)
    .join(' ')
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-800">
        5-day / 50-day volume ratio
      </h3>
      <p className="mt-0.5 text-xs text-slate-600">
        [VD]'s dry-up leg requires this below the dashed line (0.50). Green
        dots are days where [VD] fired in full (dry-up <em>and</em> bullish
        OBV-price divergence).
      </p>
      <svg width={W} height={H} className="mt-2 block">
        {/* threshold */}
        <line
          x1={pad.l}
          x2={W - pad.r}
          y1={y(0.5)}
          y2={y(0.5)}
          stroke="#f59e0b"
          strokeDasharray="4 3"
        />
        <text x={pad.l - 4} y={y(0.5) + 3} fontSize="9" textAnchor="end" fill="#92400e">
          0.50
        </text>
        <line
          x1={pad.l}
          x2={W - pad.r}
          y1={y(1.0)}
          y2={y(1.0)}
          stroke="#cbd5e1"
        />
        <text x={pad.l - 4} y={y(1.0) + 3} fontSize="9" textAnchor="end" fill="#64748b">
          1.00
        </text>
        <path d={path} stroke="#6366f1" strokeWidth="1.2" fill="none" />
        {series.map((p, i) => {
          if (p.ratio_5_50 == null) return null
          const passed = p.vd_passed === true
          const isFocused = focused === p.date
          return (
            <circle
              key={p.date}
              cx={x(i)}
              cy={y(p.ratio_5_50)}
              r={passed ? 3 : isFocused ? 3 : 1.2}
              fill={passed ? '#10b981' : isFocused ? '#4338ca' : '#94a3b8'}
              opacity={passed || isFocused ? 1 : 0.4}
              onClick={() => onPick(p.date)}
              style={{ cursor: 'pointer' }}
            >
              <title>{`${p.date} · ratio ${p.ratio_5_50.toFixed(2)}${
                passed ? ' · VD passed' : ''
              }`}</title>
            </circle>
          )
        })}
      </svg>
    </div>
  )
}

function DayDetailPanel({
  day,
  fullPass,
}: {
  day: ScanDay
  fullPass: { as_of: string; forward: ForwardWalk | null } | null
}) {
  return (
    <div className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-800">
        Focused day · <span className="font-mono">{day.date}</span>
        {day.killed_at && (
          <span className="ml-2 rounded bg-rose-200 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-rose-900">
            killed at [{day.killed_at}]
          </span>
        )}
        {!day.killed_at && (
          <span className="ml-2 rounded bg-emerald-600 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
            all gates passed
          </span>
        )}
      </h3>

      <div className="mt-3 flex flex-wrap gap-2">
        {SCAN_GATES.map((g) => {
          const v = day.gates[g]
          const bg =
            v === true
              ? 'bg-emerald-100 text-emerald-900 border-emerald-300'
              : v === false
              ? 'bg-rose-100 text-rose-900 border-rose-300'
              : 'bg-slate-100 text-slate-500 border-slate-200'
          return (
            <span
              key={g}
              className={`rounded border px-2 py-1 font-mono text-[11px] ${bg}`}
            >
              [{g}] {v === true ? '✓' : v === false ? '✗' : '–'}
            </span>
          )
        })}
      </div>

      {day.note && (
        <p className="mt-3 text-xs text-slate-600">{day.note}</p>
      )}

      {day.features?.vol_ratio_5_50 != null && (
        <p className="mt-2 text-xs text-slate-700">
          <strong>5d/50d vol ratio:</strong> {day.features.vol_ratio_5_50.toFixed(2)}
          {day.features.divergence_form && (
            <>
              {' · '}
              <strong>divergence:</strong> {day.features.divergence_form}
            </>
          )}
        </p>
      )}

      {fullPass?.forward && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-700">
            Forward walk from {fullPass.as_of}
          </h4>
          <ForwardWalkPanel forward={fullPass.forward} />
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Mode C universe — historical picks across the universe over a date range
// --------------------------------------------------------------------------- //

function ModeCUniverse({ resp }: { resp: BacktestResponse }) {
  const summary = resp.summary as UniverseSummary | undefined
  const picks = (resp.picks ?? []) as UniversePick[]
  const bySymbol = (resp.by_symbol ?? []) as UniverseBucket[]
  const byQuarter = (resp.by_quarter ?? []) as UniverseBucket[]
  const byMonth = (resp.by_month ?? []) as UniverseBucket[]
  const funnel = resp.funnel ?? []
  const [expandedRow, setExpandedRow] = useState<number | null>(null)

  const zeroPicks = (summary?.total_picks ?? 0) === 0

  return (
    <div className="mt-6 space-y-6">
      {/* Header */}
      <div className="rounded-xl border border-violet-200 bg-violet-50 p-5">
        <h2 className="text-sm font-semibold text-violet-900">
          Historical picks · Nifty 100 ({resp.universe_size} symbols)
        </h2>
        <p className="mt-1 text-xs text-violet-900/80">
          {resp.start} → {resp.end} · Every trading day was scanned. Below
          is every pick the live strategy would have alerted, in chronological
          order, with what would have happened over the {resp.assumptions.hold_days}-day hold.
        </p>
      </div>

      {/* Zero-picks explainer (only when scan returned 0 picks) */}
      {zeroPicks && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-5">
          <h3 className="text-sm font-semibold text-amber-900">
            Zero picks in this window — see the gate funnel at the bottom
          </h3>
          <p className="mt-1 text-xs text-amber-900/90">
            Each gate's per-symbol-per-day pass count is shown at the bottom
            of this page. A sharp drop tells you which gate is the chokepoint.
            If [VD] or [BR] is the cliff, the strategy is doing its strict
            job (quality over quantity). If [LT] or [CS] is the cliff, the
            universe lacked accumulation setups this window. Try the
            <strong> Advanced</strong> sliders above to see what one threshold
            change does.
          </p>
        </div>
      )}

      {/* Summary stats */}
      {summary && <UniverseSummaryPanel s={summary} />}

      {/* Quarterly breakdown */}
      {byQuarter.length > 0 && (
        <BreakdownTable title="By quarter" rows={byQuarter} keyLabel="Quarter" />
      )}

      {/* Most-picked symbols */}
      {bySymbol.length > 0 && (
        <SymbolLeaderboard rows={bySymbol.slice(0, 15)} />
      )}

      {/* Chronological picks table */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">
          Chronological picks{' '}
          <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">
            {picks.length}
          </span>
        </h3>
        {picks.length === 0 ? (
          <p className="mt-3 text-sm text-slate-600">
            No picks in this window. Either the regime was halted on most days,
            or no symbol passed all gates. Try a wider date range.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {picks.map((p, i) => {
              const r = p.forward?.return_pct ?? null
              const isOpen = expandedRow === i
              return (
                <li key={`${p.as_of}-${p.symbol}-${i}`}>
                  <button
                    onClick={() => setExpandedRow(isOpen ? null : i)}
                    className="flex w-full flex-wrap items-center justify-between gap-3 py-2 text-left hover:bg-slate-50"
                  >
                    <div className="flex flex-wrap items-center gap-3 text-xs">
                      <span className="font-mono text-slate-700">{p.as_of}</span>
                      <span className="rounded bg-indigo-100 px-1.5 py-0.5 font-mono font-bold text-indigo-900">
                        #{p.rank}
                      </span>
                      <span className="font-mono text-sm font-bold">{p.symbol}</span>
                      <span className="text-slate-600">{p.company}</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">
                        conf {p.confirmation_score?.toFixed(2) ?? '—'}
                      </span>
                    </div>
                    <div className="text-right text-xs">
                      {r != null ? (
                        <span
                          className={`font-mono font-bold ${
                            r >= 0 ? 'text-emerald-700' : 'text-rose-700'
                          }`}
                        >
                          {fmtPct(r)} · {p.forward?.exit_reason}
                        </span>
                      ) : (
                        <span className="font-mono text-slate-500">no fwd data</span>
                      )}
                    </div>
                  </button>
                  {isOpen && (
                    <div className="border-t border-slate-100 px-2 py-3">
                      {p.headline && (
                        <p className="mb-2 text-xs italic text-slate-600">
                          {p.headline}
                        </p>
                      )}
                      <div className="mb-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
                        <div>
                          <span className="text-slate-500">Entry day:</span>{' '}
                          <strong className="font-mono">{p.entry_date ?? '—'}</strong>
                        </div>
                        <div>
                          <span className="text-slate-500">Entry:</span>{' '}
                          <strong className="font-mono">{fmtINR(p.entry_px)}</strong>
                        </div>
                        <div>
                          <span className="text-slate-500">Stop:</span>{' '}
                          <strong className="font-mono text-rose-700">
                            {fmtINR(p.stop_px)}
                          </strong>
                        </div>
                        <div>
                          <span className="text-slate-500">Target (T2):</span>{' '}
                          <strong className="font-mono text-emerald-700">
                            {fmtINR(p.target_px)}
                          </strong>
                        </div>
                      </div>
                      {p.bonuses_fired.length > 0 && (
                        <div className="mb-2 text-[11px]">
                          <span className="text-slate-500">Bonuses fired:</span>{' '}
                          {p.bonuses_fired.map((b) => (
                            <span
                              key={b}
                              className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 font-mono text-amber-900"
                            >
                              {b}
                            </span>
                          ))}
                        </div>
                      )}
                      {p.forward && <ForwardWalkPanel forward={p.forward} />}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* Monthly breakdown — bottom for full historical record */}
      {byMonth.length > 0 && (
        <BreakdownTable title="By month" rows={byMonth} keyLabel="Month" />
      )}

      {/* Per-gate funnel — always at the bottom for diagnostic clarity */}
      {funnel.length > 0 && <UniverseFunnel rows={funnel} />}
    </div>
  )
}

function UniverseFunnel({ rows }: { rows: import('../types').BacktestFunnelRow[] }) {
  const max = Math.max(...rows.map((r) => r.eval), 1)
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-800">
        Where the universe drops out (per gate)
      </h3>
      <p className="mt-0.5 text-xs text-slate-600">
        Counts are symbol-days across the entire scan. Each gate's bar shows
        how many symbol-days <strong>passed</strong>. A sharp drop tells you
        which gate is the bottleneck.
      </p>
      <div className="mt-3 space-y-2">
        {rows.map((r) => {
          const passW = (r.pass / max) * 100
          return (
            <div key={r.stage_id}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <div className="font-mono">
                  [{r.stage_id}]{' '}
                  <span className="font-sans font-semibold text-slate-800">
                    {r.label}
                  </span>
                </div>
                <div className="font-mono text-slate-700">
                  eval {r.eval.toLocaleString()} · pass{' '}
                  <strong>{r.pass.toLocaleString()}</strong> · fail{' '}
                  {r.fail.toLocaleString()}
                </div>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <div className="h-3 flex-1 rounded bg-slate-100">
                  <div
                    className="h-3 rounded bg-emerald-500/80"
                    style={{ width: `${passW}%` }}
                  />
                </div>
                <div className="w-16 text-right font-mono text-[11px] text-slate-500">
                  {r.eval > 0 ? Math.round((r.pass / r.eval) * 100) : 0}%
                </div>
              </div>
              {r.top_reason && r.fail > 0 && (
                <div className="ml-1 mt-0.5 font-mono text-[10px] text-rose-700">
                  ↳ top fail: {r.top_reason}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


function UniverseSummaryPanel({ s }: { s: UniverseSummary }) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6">
      <SummaryCard label="Total picks" value={`${s.total_picks}`} />
      <SummaryCard label="Trading days" value={`${s.trading_days}`} />
      <SummaryCard
        label="Active days"
        value={`${s.active_days}`}
        hint={`${s.regime_halt_days} regime-halt`}
      />
      <SummaryCard label="Days with picks" value={`${s.days_with_picks}`} />
      <SummaryCard
        label="Hit rate"
        value={s.hit_rate_pct != null ? `${s.hit_rate_pct}%` : '—'}
        cls={s.hit_rate_pct != null && s.hit_rate_pct >= 50 ? 'text-emerald-700' : 'text-rose-700'}
      />
      <SummaryCard
        label="Avg return"
        value={s.avg_return_pct != null ? fmtPct(s.avg_return_pct) : '—'}
        cls={
          s.avg_return_pct != null && s.avg_return_pct >= 0 ? 'text-emerald-700' : 'text-rose-700'
        }
      />
    </div>
  )
}

function SummaryCard({
  label,
  value,
  hint,
  cls,
}: {
  label: string
  value: string
  hint?: string
  cls?: string
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-lg font-bold ${cls ?? 'text-slate-900'}`}>
        {value}
      </div>
      {hint && <div className="text-[10px] text-slate-500">{hint}</div>}
    </div>
  )
}

function BreakdownTable({
  title,
  rows,
  keyLabel,
}: {
  title: string
  rows: UniverseBucket[]
  keyLabel: string
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      <table className="mt-3 w-full text-xs">
        <thead className="border-b text-left text-slate-600">
          <tr>
            <th className="py-1.5 font-medium">{keyLabel}</th>
            <th className="py-1.5 font-medium">Picks</th>
            <th className="py-1.5 font-medium">Hit rate</th>
            <th className="py-1.5 font-medium">Avg return</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-slate-100">
              <td className="py-1.5 font-mono">{r.key}</td>
              <td className="py-1.5 font-mono">{r.n}</td>
              <td className="py-1.5 font-mono">
                {r.hit_rate_pct != null ? `${r.hit_rate_pct}%` : '—'}
              </td>
              <td
                className={`py-1.5 font-mono ${
                  r.avg_return_pct == null
                    ? ''
                    : r.avg_return_pct >= 0
                    ? 'text-emerald-700'
                    : 'text-rose-700'
                }`}
              >
                {r.avg_return_pct != null ? fmtPct(r.avg_return_pct) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SymbolLeaderboard({ rows }: { rows: UniverseBucket[] }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-800">Most-picked symbols</h3>
      <p className="mt-0.5 text-xs text-slate-600">
        The stocks the strategy returned to most often in this window.
      </p>
      <table className="mt-3 w-full text-xs">
        <thead className="border-b text-left text-slate-600">
          <tr>
            <th className="py-1.5 font-medium">Symbol</th>
            <th className="py-1.5 font-medium">Company</th>
            <th className="py-1.5 font-medium">Times picked</th>
            <th className="py-1.5 font-medium">Hit rate</th>
            <th className="py-1.5 font-medium">Avg return</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol} className="border-b border-slate-100">
              <td className="py-1.5 font-mono font-bold">{r.symbol}</td>
              <td className="py-1.5 text-slate-700">{r.company}</td>
              <td className="py-1.5 font-mono">{r.n}</td>
              <td className="py-1.5 font-mono">
                {r.hit_rate_pct != null ? `${r.hit_rate_pct}%` : '—'}
              </td>
              <td
                className={`py-1.5 font-mono ${
                  r.avg_return_pct == null
                    ? ''
                    : r.avg_return_pct >= 0
                    ? 'text-emerald-700'
                    : 'text-rose-700'
                }`}
              >
                {r.avg_return_pct != null ? fmtPct(r.avg_return_pct) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
