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
import { fmtINR, fmtPct, runBacktest } from '../api'
import { Disclaimer } from '../components/Disclaimer'
import { RegimeBanner } from '../components/RegimeBanner'
import type {
  BacktestResponse,
  BacktestSelected,
  BacktestSymbolBlock,
  ForwardWalk,
  ForwardWalkBar,
  ScanDay,
  ScanGateId,
} from '../types'

/**
 * SimulationPage — backtest UI built on POST /api/backtest.
 *
 * Two modes, auto-selected by the form:
 *   - 1-2 symbols   → Mode A: per-symbol deep explanation + counterfactual
 *   - 0 or >2 syms  → Mode B: universe funnel + top-N + outcomes
 *
 * Explanation comes first. The numbers are secondary. The screen exists to
 * teach the user WHY each pick (or each rejection) happened.
 */

const MIN_AS_OF = '2022-01-01'
const TODAY_ISO = new Date().toISOString().slice(0, 10)

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

export function SimulationPage() {
  const [asOf, setAsOf] = useState<string>(lastTradingDayBefore(TODAY_ISO))
  const [scanMode, setScanMode] = useState<boolean>(false)
  const [endDate, setEndDate] = useState<string>(lastTradingDayBefore(TODAY_ISO))
  const [symbolsRaw, setSymbolsRaw] = useState<string>('')
  const [holdDays, setHoldDays] = useState<number>(20)
  const [topN, setTopN] = useState<number>(3)
  const [capital, setCapital] = useState<number>(100000)
  const [expanded, setExpanded] = useState<string | null>(null)

  const mut = useMutation({
    mutationFn: runBacktest,
  })

  const symbols = useMemo(() => {
    const parts = symbolsRaw
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)
    return parts
  }, [symbolsRaw])

  const scanRequiresOne = scanMode && symbols.length !== 1

  const onRun = () => {
    setExpanded(null)
    mut.mutate({
      as_of: asOf,
      end: scanMode ? endDate : undefined,
      symbols: symbols.length ? symbols : undefined,
      hold_days: holdDays,
      top_n: topN,
      capital,
    })
  }

  const resp = mut.data

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Simulation</h1>
          <p className="mt-1 text-sm text-slate-700">
            Run the live picker against a historical date. See what would have
            been alerted, why each rejection happened, and how the trade would
            have unfolded over the holding period.
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

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!scanRequiresOne) onRun()
        }}
        className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      >
        <div className="mb-4 flex items-center gap-3 rounded-lg border border-violet-200 bg-violet-50 p-3 text-xs">
          <input
            id="scanMode"
            type="checkbox"
            checked={scanMode}
            onChange={(e) => setScanMode(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-violet-600"
          />
          <label htmlFor="scanMode" className="cursor-pointer">
            <strong className="text-violet-900">Scan a date range</strong>{' '}
            <span className="text-slate-700">— walk every trading day, see which gates fired and when. Requires exactly one symbol.</span>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
          <label className="md:col-span-3">
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
              {scanMode ? 'Start' : 'As of'} <span className="text-rose-600">*</span>
            </span>
            <input
              type="date"
              required
              min={MIN_AS_OF}
              max={lastTradingDayBefore(TODAY_ISO)}
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
            />
            <span className="mt-1 block text-[11px] text-slate-500">
              Past date · {MIN_AS_OF} or later
            </span>
          </label>

          {scanMode && (
            <label className="md:col-span-3">
              <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
                End <span className="text-rose-600">*</span>
              </span>
              <input
                type="date"
                required
                min={asOf}
                max={lastTradingDayBefore(TODAY_ISO)}
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
              />
              <span className="mt-1 block text-[11px] text-slate-500">
                Scan walks every trading day from start to end
              </span>
            </label>
          )}

          <label className={scanMode ? 'md:col-span-2' : 'md:col-span-5'}>
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
              Symbols {scanMode && <span className="text-rose-600">*</span>}
            </span>
            <input
              type="text"
              placeholder={scanMode ? 'e.g. BAJAJ-AUTO.NS' : 'Blank = Nifty 100. e.g. INFY.NS'}
              value={symbolsRaw}
              onChange={(e) => setSymbolsRaw(e.target.value)}
              className={`mt-1 w-full rounded-md border px-3 py-2 font-mono text-sm ${
                scanRequiresOne ? 'border-rose-400 bg-rose-50' : 'border-slate-300'
              }`}
            />
            <span className="mt-1 block text-[11px] text-slate-500">
              {scanMode
                ? scanRequiresOne
                  ? 'Scan needs exactly one symbol'
                  : `Mode C (scan): ${symbols[0]}`
                : symbols.length === 0
                ? 'Mode B (universe scan)'
                : symbols.length <= 2
                ? `Mode A (deep explanation): ${symbols.join(', ')}`
                : `Mode B (${symbols.length} symbols)`}
            </span>
          </label>

          <label className="md:col-span-2">
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
              Hold days
            </span>
            <input
              type="number"
              min={1}
              max={180}
              value={holdDays}
              onChange={(e) => setHoldDays(Number(e.target.value))}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
            />
          </label>

          <label className="md:col-span-1">
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
              Top N
            </span>
            <input
              type="number"
              min={1}
              max={10}
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
            />
          </label>

          <label className="md:col-span-1">
            <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
              Capital ₹
            </span>
            <input
              type="number"
              min={1000}
              step={1000}
              value={capital}
              onChange={(e) => setCapital(Number(e.target.value))}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
            />
          </label>
        </div>

        <div className="mt-4 flex items-center justify-between">
          <p className="text-xs text-slate-500">
            Defaults filled in. Only the date is required — leave the rest
            blank and we'll assume sensible values (echoed in the result).
          </p>
          <button
            type="submit"
            disabled={mut.isPending || !asOf}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <PlayCircle className={`h-4 w-4 ${mut.isPending ? 'animate-pulse' : ''}`} />
            {mut.isPending ? 'Running…' : 'Run simulation'}
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
          <AssumptionsBanner resp={resp} />

          {resp.mode === 'A' && <ModeA resp={resp} />}
          {resp.mode === 'B' && (
            <ModeB
              resp={resp}
              expanded={expanded}
              onExpand={(sym) => setExpanded(expanded === sym ? null : sym)}
            />
          )}
          {resp.mode === 'C' && <ModeC resp={resp} />}
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
      <Funnel resp={resp} />
      <SelectedPicks resp={resp} expanded={expanded} onExpand={onExpand} />
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

      {/* Counts table */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">Per-gate hit count</h3>
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
