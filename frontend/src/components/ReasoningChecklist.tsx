import { useState } from 'react'
import { CheckCircle2, MinusCircle, XCircle, ChevronDown } from 'lucide-react'
import type { ReasoningPoint } from '../types'

interface Props {
  points: ReasoningPoint[]
  defaultOpen?: boolean
  compact?: boolean   // hide "verify" lines on the cards; show on detail
}

export function ReasoningChecklist({
  points,
  defaultOpen = false,
  compact = false,
}: Props) {
  const [open, setOpen] = useState(defaultOpen)

  if (!points.length) return null

  const bullish = points.filter((p) => p.state === 'bullish').length
  const total = points.length

  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left hover:bg-slate-50"
      >
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Reasoning checklist
          </span>
          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">
            {bullish}/{total} bullish
          </span>
        </div>
        <ChevronDown
          className={`h-4 w-4 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <ul className="space-y-2 border-t border-slate-100 px-4 py-3">
          {points.map((p, i) => (
            <li key={i} className="flex items-start gap-3 text-sm">
              <Icon state={p.state} />
              <div className="flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-medium text-slate-900">{p.label}</span>
                  <span className="font-mono text-xs text-slate-700">{p.value}</span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-slate-600">{p.why}</p>
                {!compact && (
                  <p className="mt-1 text-xs italic leading-relaxed text-indigo-700">
                    <span className="font-semibold not-italic">How to verify:</span>{' '}
                    {p.verify}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Icon({ state }: { state: ReasoningPoint['state'] }) {
  if (state === 'bullish') {
    return <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-600" />
  }
  if (state === 'bearish') {
    return <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-rose-600" />
  }
  return <MinusCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-slate-400" />
}
