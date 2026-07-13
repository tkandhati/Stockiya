import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
  AlertTriangle,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  Clock,
  Hand,
  ShieldAlert,
  TrendingUp,
} from 'lucide-react'
import type { Position, PositionAction, PositionOwnership } from '../types'
import { declinePosition, fmtINR, fmtPct, takePosition } from '../api'

/**
 * Single position card. Action is the headline; ladder is the body.
 *
 * Ownership (V1):
 *   suggested  → scanner emitted, user hasn't acted. Card shows Take / Decline.
 *   paper      → user marked "taking paper" — monitored, no capital risk claim.
 *   live       → user marked "taking live" — monitored, capital committed.
 *   (declined) → filtered out server-side; never rendered here.
 *
 * Action colour scheme:
 *   hold              → slate (neutral)
 *   tighten_stop_45   → amber (caution)
 *   exit_t1           → emerald (good news, partial profit)
 *   exit_t2           → emerald (great news, full target)
 *   exit_stop         → rose (loss, must act)
 *   exit_time_stop    → rose (loss/break-even, must act)
 *   exit_final        → rose (must act regardless)
 */
const ACTION_META: Record<PositionAction, {
  label: string
  tone: 'slate' | 'amber' | 'emerald' | 'rose'
  icon: React.ReactNode
}> = {
  hold: { label: 'Hold', tone: 'slate', icon: <Hand className="h-4 w-4" /> },
  tighten_stop_45: {
    label: 'Tighten stop',
    tone: 'amber',
    icon: <ShieldAlert className="h-4 w-4" />,
  },
  exit_t1: {
    label: 'Exit T1 (sell 50%)',
    tone: 'emerald',
    icon: <CheckCircle2 className="h-4 w-4" />,
  },
  exit_t2: {
    label: 'Exit T2 (sell remainder)',
    tone: 'emerald',
    icon: <CheckCircle2 className="h-4 w-4" />,
  },
  exit_stop: {
    label: 'Stop hit — exit',
    tone: 'rose',
    icon: <AlertTriangle className="h-4 w-4" />,
  },
  exit_time_stop: {
    label: 'Time stop — exit',
    tone: 'rose',
    icon: <Clock className="h-4 w-4" />,
  },
  exit_final: {
    label: 'Day-180 final exit',
    tone: 'rose',
    icon: <AlertCircle className="h-4 w-4" />,
  },
  exit_distribution: {
    label: 'Distribution flip — exit',
    tone: 'rose',
    icon: <AlertTriangle className="h-4 w-4" />,
  },
}

const TRAJECTORY_META: Record<
  'strong' | 'stable' | 'weakening' | 'flipped' | 'unknown',
  { label: string; tone: 'emerald' | 'slate' | 'amber' | 'rose'; arrow: string }
> = {
  strong:    { label: 'STRONG',    tone: 'emerald', arrow: 'up' },
  stable:    { label: 'STABLE',    tone: 'slate',   arrow: '-' },
  weakening: { label: 'WEAKENING', tone: 'amber',   arrow: 'down' },
  flipped:   { label: 'FLIPPED',   tone: 'rose',    arrow: 'down' },
  unknown:   { label: '—',         tone: 'slate',   arrow: '-' },
}

const OWNERSHIP_META: Record<PositionOwnership, { label: string; className: string }> = {
  suggested: {
    label: 'SUGGESTED',
    className: 'bg-slate-200 text-slate-800',
  },
  paper: {
    label: 'PAPER',
    className: 'bg-sky-100 text-sky-900',
  },
  live: {
    label: 'LIVE',
    className: 'bg-indigo-100 text-indigo-900',
  },
}

const TONE_BORDER = {
  slate: 'border-slate-200',
  amber: 'border-amber-300 ring-1 ring-amber-200',
  emerald: 'border-emerald-300 ring-1 ring-emerald-200',
  rose: 'border-rose-300 ring-1 ring-rose-200',
}

const TONE_PILL = {
  slate: 'bg-slate-100 text-slate-700',
  amber: 'bg-amber-100 text-amber-900',
  emerald: 'bg-emerald-100 text-emerald-900',
  rose: 'bg-rose-100 text-rose-900',
}

export function PositionCard({ position: p }: { position: Position }) {
  const meta = ACTION_META[p.action] ?? ACTION_META.hold
  const current = p.current_price ?? null
  const pnl = p.pnl_pct ?? null
  const isSuggested = p.ownership === 'suggested'

  return (
    <article
      className={`group block rounded-2xl border bg-white p-5 shadow-sm ${TONE_BORDER[meta.tone]}`}
    >
      <Link
        to={`/stock/${encodeURIComponent(p.symbol)}`}
        className="block transition hover:-translate-y-0.5"
      >
        {/* Heading + action pill */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-lg font-semibold text-slate-900">
                {p.company}
              </h3>
              <OwnershipBadge ownership={p.ownership} />
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-xs text-slate-500">
              <span>{p.symbol}</span>
              <span>· entered {p.entry_date}</span>
              <span>· {p.days_held}d held</span>
            </div>
          </div>
          <div
            className={`flex flex-shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${TONE_PILL[meta.tone]}`}
          >
            {meta.icon}
            <span>{meta.label}</span>
          </div>
        </div>

        {/* Action note */}
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm leading-snug ${TONE_PILL[meta.tone]}`}>
          {p.action_note}
        </p>

        {/* Price ladder */}
        <div className="mt-4 grid grid-cols-2 gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3 text-xs md:grid-cols-5">
          <Rung label="Entry" value={fmtINR(p.entry_price)} tone="slate" />
          <Rung
            label="Current"
            value={current != null ? fmtINR(current) : '—'}
            extra={pnl != null ? fmtPct(pnl) : undefined}
            tone={pnl == null ? 'slate' : pnl >= 0 ? 'emerald' : 'rose'}
          />
          <Rung
            label="Stop"
            value={fmtINR(p.stop_price)}
            tone="rose"
            extra={p.new_stop ? `→ ${fmtINR(p.new_stop)}` : undefined}
          />
          <Rung
            label="T1 (+8%)"
            value={fmtINR(p.t1_price)}
            tone={p.hit_t1 ? 'emerald' : 'amber'}
            extra={p.hit_t1 ? '✓ hit' : undefined}
          />
          <Rung label="T2 (+16%)" value={fmtINR(p.t2_price)} tone="indigo" />
        </div>

        {/* Q1 — expected T1 day + Q2 — trajectory pill */}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
          {p.expected_t1_date && p.t1_status && (
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono ${
                p.t1_status === 'hit'
                  ? 'bg-emerald-100 text-emerald-900'
                  : p.t1_status === 'overdue'
                  ? 'bg-amber-100 text-amber-900'
                  : 'bg-slate-100 text-slate-700'
              }`}
              title="Expected T1 day = entry + 21 trading days"
            >
              <CalendarClock className="h-3 w-3" />
              T1 expected by {p.expected_t1_date}
              {p.t1_status === 'overdue' && p.days_to_expected_t1 != null && (
                <span className="ml-1">
                  ({Math.abs(p.days_to_expected_t1)}d overdue)
                </span>
              )}
              {p.t1_status === 'hit' && <span className="ml-1">✓ hit</span>}
            </span>
          )}
          {p.trajectory && (
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
                TRAJECTORY_META[p.trajectory.overall].tone === 'emerald'
                  ? 'bg-emerald-100 text-emerald-900'
                  : TRAJECTORY_META[p.trajectory.overall].tone === 'amber'
                  ? 'bg-amber-100 text-amber-900'
                  : TRAJECTORY_META[p.trajectory.overall].tone === 'rose'
                  ? 'bg-rose-100 text-rose-900'
                  : 'bg-slate-100 text-slate-700'
              }`}
              title={p.trajectory.headline}
            >
              Signal: {TRAJECTORY_META[p.trajectory.overall].label}
            </span>
          )}
        </div>

        {/* Trajectory detail (only when something changed) */}
        {p.trajectory && p.trajectory.overall !== 'stable' && p.trajectory.overall !== 'unknown' && (
          <p className="mt-2 text-[11px] italic text-slate-600">
            {p.trajectory.headline}
          </p>
        )}

        {/* Time-stop dates + position size */}
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
          <span>d45 {p.time_stops.day_45}</span>
          <span>· d90 {p.time_stops.day_90}</span>
          <span>· d180 {p.time_stops.day_180}</span>
          <span className="ml-auto inline-flex items-center gap-1">
            <TrendingUp className="h-3 w-3" />
            {p.shares_total.toLocaleString('en-IN')} sh
          </span>
          <ArrowUpRight className="h-3.5 w-3.5 text-slate-400 transition group-hover:text-slate-700" />
        </div>

        {p.headline && (
          <p className="mt-3 border-t border-slate-100 pt-2 text-[11px] italic text-slate-500">
            {p.headline}
          </p>
        )}
      </Link>

      {/* Ownership footer — outside the <Link> so form controls don't navigate. */}
      {isSuggested ? (
        <SuggestedFooter position={p} />
      ) : (
        <TakenFooter position={p} />
      )}
    </article>
  )
}

// --------------------------------------------------------------------------
// Ownership badge — one-line marker showing suggested / paper / live.
// --------------------------------------------------------------------------

function OwnershipBadge({ ownership }: { ownership: PositionOwnership }) {
  const m = OWNERSHIP_META[ownership]
  return (
    <span
      className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-bold tracking-wide ${m.className}`}
      title={`Ownership: ${ownership}`}
    >
      {m.label}
    </span>
  )
}

// --------------------------------------------------------------------------
// Suggested footer — Take (paper/live) + Decline. "Take" opens an inline
// form with the scanner's numbers as placeholders; blank fields = accept
// scanner defaults.
// --------------------------------------------------------------------------

function SuggestedFooter({ position: p }: { position: Position }) {
  const [mode, setMode] = useState<'closed' | 'paper' | 'live'>('closed')
  const [entryDate, setEntryDate] = useState(p.scanner_entry_date || p.entry_date)
  const [entryPrice, setEntryPrice] = useState<string>('')
  const [shares, setShares] = useState<string>('')
  const [notes, setNotes] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()

  const takeMut = useMutation({
    mutationFn: (ownership: 'paper' | 'live') =>
      takePosition(p.pick_id, {
        ownership,
        user_entry_date: entryDate || '',
        user_entry_price: entryPrice ? Number(entryPrice) : null,
        user_shares: shares ? Number(shares) : null,
        user_notes: notes || '',
      }),
    onSuccess: (data) => {
      qc.setQueryData(['positions'], data)
      setMode('closed')
      setError(null)
    },
    onError: (err: Error) => setError(err.message || 'take failed'),
  })

  const declineMut = useMutation({
    mutationFn: () => declinePosition(p.pick_id),
    onSuccess: (data) => qc.setQueryData(['positions'], data),
    onError: (err: Error) => setError(err.message || 'decline failed'),
  })

  const submitTake = () => {
    if (mode === 'closed') return
    setError(null)
    takeMut.mutate(mode)
  }

  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      {mode === 'closed' ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-medium text-slate-500">
            Suggested by scanner. Did you take it?
          </span>
          <div className="ml-auto flex gap-1.5">
            <button
              type="button"
              onClick={() => setMode('paper')}
              className="rounded-md bg-sky-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-sky-700"
              disabled={takeMut.isPending || declineMut.isPending}
            >
              Take (paper)
            </button>
            <button
              type="button"
              onClick={() => setMode('live')}
              className="rounded-md bg-indigo-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700"
              disabled={takeMut.isPending || declineMut.isPending}
            >
              Take (live)
            </button>
            <button
              type="button"
              onClick={() => declineMut.mutate()}
              className="rounded-md border border-slate-300 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-100"
              disabled={takeMut.isPending || declineMut.isPending}
            >
              {declineMut.isPending ? 'Declining…' : 'Decline'}
            </button>
          </div>
          {error && (
            <p className="mt-1 w-full text-[11px] text-rose-700">{error}</p>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-[11px] font-semibold text-slate-700">
            Take ({mode}) — leave blank to accept scanner defaults
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <label className="text-[10px] uppercase tracking-wide text-slate-500">
              Entry date
              <input
                type="date"
                value={entryDate}
                onChange={(e) => setEntryDate(e.target.value)}
                className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-xs"
              />
            </label>
            <label className="text-[10px] uppercase tracking-wide text-slate-500">
              Entry price (₹)
              <input
                type="number"
                inputMode="decimal"
                min={0}
                step="0.01"
                value={entryPrice}
                onChange={(e) => setEntryPrice(e.target.value)}
                placeholder={p.scanner_entry_price?.toFixed(2) ?? ''}
                className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-xs"
              />
            </label>
            <label className="text-[10px] uppercase tracking-wide text-slate-500">
              Shares
              <input
                type="number"
                inputMode="numeric"
                min={0}
                step={1}
                value={shares}
                onChange={(e) => setShares(e.target.value)}
                placeholder={p.scanner_shares?.toString() ?? ''}
                className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-xs"
              />
            </label>
          </div>
          <label className="block text-[10px] uppercase tracking-wide text-slate-500">
            Notes (optional)
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. limit at ₹146.20, filled 14:32"
              className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 text-xs"
            />
          </label>
          {error && (
            <p className="text-[11px] text-rose-700">{error}</p>
          )}
          <div className="flex justify-end gap-1.5">
            <button
              type="button"
              onClick={() => { setMode('closed'); setError(null) }}
              className="rounded-md border border-slate-300 px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-100"
              disabled={takeMut.isPending}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submitTake}
              className={`rounded-md px-2.5 py-1 text-[11px] font-semibold text-white ${
                mode === 'live'
                  ? 'bg-indigo-600 hover:bg-indigo-700'
                  : 'bg-sky-600 hover:bg-sky-700'
              }`}
              disabled={takeMut.isPending}
            >
              {takeMut.isPending ? 'Saving…' : `Confirm ${mode}`}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// Taken footer — read-only summary of user's fill vs. scanner's.
// --------------------------------------------------------------------------

function TakenFooter({ position: p }: { position: Position }) {
  const userDate = p.user_entry_date && p.user_entry_date.length > 0 ? p.user_entry_date : null
  const userPrice = p.user_entry_price ?? null
  const userShares = p.user_shares ?? null
  const notes = p.user_notes ?? ''
  const diverges = userDate !== null || userPrice !== null || userShares !== null

  if (!diverges && !notes) {
    return null
  }

  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
        Your fill
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-700">
        {userDate && (
          <span>
            <span className="text-slate-500">date:</span> {userDate}
            {p.scanner_entry_date && p.scanner_entry_date !== userDate && (
              <span className="ml-1 text-slate-400">
                (scanner: {p.scanner_entry_date})
              </span>
            )}
          </span>
        )}
        {userPrice != null && (
          <span>
            <span className="text-slate-500">price:</span> {fmtINR(userPrice)}
            {p.scanner_entry_price != null &&
              Math.abs(userPrice - p.scanner_entry_price) > 0.005 && (
                <span className="ml-1 text-slate-400">
                  (scanner: {fmtINR(p.scanner_entry_price)})
                </span>
              )}
          </span>
        )}
        {userShares != null && (
          <span>
            <span className="text-slate-500">shares:</span>{' '}
            {userShares.toLocaleString('en-IN')}
            {p.scanner_shares != null && p.scanner_shares !== userShares && (
              <span className="ml-1 text-slate-400">
                (scanner: {p.scanner_shares.toLocaleString('en-IN')})
              </span>
            )}
          </span>
        )}
      </div>
      {notes && (
        <p className="mt-1 text-[11px] italic text-slate-500">{notes}</p>
      )}
    </div>
  )
}

function Rung({
  label,
  value,
  extra,
  tone,
}: {
  label: string
  value: string
  extra?: string
  tone: 'slate' | 'emerald' | 'amber' | 'rose' | 'indigo'
}) {
  const labelColor = {
    slate: 'text-slate-500',
    emerald: 'text-emerald-700',
    amber: 'text-amber-700',
    rose: 'text-rose-700',
    indigo: 'text-indigo-700',
  }[tone]
  const valueColor = {
    slate: 'text-slate-900',
    emerald: 'text-emerald-900',
    amber: 'text-amber-900',
    rose: 'text-rose-900',
    indigo: 'text-indigo-900',
  }[tone]
  return (
    <div>
      <div className={`text-[10px] font-semibold uppercase tracking-wide ${labelColor}`}>
        {label}
      </div>
      <div className={`font-semibold tabular-nums ${valueColor}`}>{value}</div>
      {extra && (
        <div className={`text-[10px] tabular-nums opacity-80 ${labelColor}`}>{extra}</div>
      )}
    </div>
  )
}
