import { Target, ShieldAlert, Clock, AlertOctagon } from 'lucide-react'
import { fmtINR, fmtPct } from '../api'
import type { Pick } from '../types'

type Tone = 'emerald' | 'rose' | 'slate' | 'amber'

interface Scenario {
  icon: React.ReactNode
  tone: Tone
  tag: string
  trigger: string
  action: string
  detail: string
  watch: string
}

/** Exit scenarios derived from PRINCIPLES.md §4. Every pick ships with all four. */
export function ExitScenarios({ pick }: { pick: Pick }) {
  const scenarios: Scenario[] = [
    {
      icon: <Target className="h-4 w-4" />,
      tone: 'emerald',
      tag: 'A · Target hit',
      trigger: `Price reaches ${fmtINR(pick.sell_target)} (or higher) on a closing basis.`,
      action: `Sell. Locks in ~${fmtPct(pick.upside_pct)} from the buy price.`,
      detail:
        'Optional refinement: trim 50% at target and trail the rest with a rising stop if the trend is still intact.',
      watch: 'Daily close — price-based, easy to track.',
    },
    {
      icon: <ShieldAlert className="h-4 w-4" />,
      tone: 'rose',
      tag: 'B1 · Volume distribution (PRIMARY)',
      trigger:
        'OBV (30d) rolls over into a downslope, OR Chaikin Money Flow (21d) crosses below zero, OR down-day volume starts dominating up-day volume (ratio < 0.85). Any one is enough.',
      action:
        'Exit at the next open. The institutional bid is leaving — do not wait for price to confirm.',
      detail:
        'Volume turns before price. We bought because institutions were accumulating; we sell the moment that signal inverts.',
      watch:
        'Refresh the detail page daily — the volume strategies update on every load.',
    },
    {
      icon: <ShieldAlert className="h-4 w-4" />,
      tone: 'rose',
      tag: 'B2 · Hard price stop (backstop)',
      trigger: `Price closes at or below ${fmtINR(pick.stop_loss)} (${fmtPct(
        pick.downside_pct,
      )} from buy), OR closes below the 200-day MA for two sessions in a row.`,
      action: 'Sell at the next open. Capital preservation overrides the thesis.',
      detail:
        'Stops do not move down. This is the price-based backstop in case the volume signal lags.',
      watch: 'Daily close vs stop and 200DMA — price-based, easy to track.',
    },
    {
      icon: <Clock className="h-4 w-4" />,
      tone: 'slate',
      tag: 'C · Time stop',
      trigger: '6 months have passed since entry and neither target nor stop has triggered.',
      action: 'Sell, even at a small profit or loss. Rebuild the thesis from scratch.',
      detail:
        'A directionless position is opportunity cost — capital tied up here is capital not earning the next setup.',
      watch: 'Calendar — note the entry date the moment you buy.',
    },
    {
      icon: <AlertOctagon className="h-4 w-4" />,
      tone: 'amber',
      tag: 'D · Hypothesis broken',
      trigger:
        'A material adverse event lands, the valuation case inverts, the catalyst fails to appear by the half-way mark, or a substantially better opportunity passes every gate.',
      action: 'Sell immediately at the next open, regardless of price. Then post-mortem.',
      detail:
        'Examples: SEBI/RBI action, fraud or auditor exit, major customer loss, debt-covenant breach, P/E re-rates above 1.15× sector median before target, expected earnings beat misses, or a ≥30% higher-upside name clears the four hypothesis gates.',
      watch:
        'News, exchange filings, earnings days, sector P/E vs. peers — the only manual one. Set Google Alerts on the company name and ticker.',
    },
  ]

  const toneClasses: Record<Tone, string> = {
    emerald: 'border-emerald-200 bg-emerald-50/60 text-emerald-900',
    rose: 'border-rose-200 bg-rose-50/60 text-rose-900',
    slate: 'border-slate-200 bg-slate-50/60 text-slate-900',
    amber: 'border-amber-200 bg-amber-50/60 text-amber-900',
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6">
      <header>
        <h3 className="font-semibold text-slate-900">Exit scenarios — when to sell</h3>
        <p className="mt-1 text-xs text-slate-500">
          Every pick ships with all four exits planned <em>before</em> entry. Whichever
          triggers first wins. Stops do not move down; targets only move up if the
          hypothesis strengthens.
        </p>
      </header>
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        {scenarios.map((s) => (
          <article
            key={s.tag}
            className={`rounded-xl border p-4 ${toneClasses[s.tone]}`}
          >
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider opacity-90">
              {s.icon}
              <span>{s.tag}</span>
            </div>

            <dl className="mt-3 space-y-2 text-xs leading-relaxed">
              <div>
                <dt className="font-semibold uppercase tracking-wide opacity-70">
                  Trigger
                </dt>
                <dd>{s.trigger}</dd>
              </div>
              <div>
                <dt className="font-semibold uppercase tracking-wide opacity-70">
                  Action
                </dt>
                <dd>{s.action}</dd>
              </div>
              <div>
                <dt className="font-semibold uppercase tracking-wide opacity-70">
                  How to monitor
                </dt>
                <dd>{s.watch}</dd>
              </div>
              <div className="pt-1 opacity-80">{s.detail}</div>
            </dl>
          </article>
        ))}
      </div>

      <p className="mt-4 rounded-lg bg-slate-50 px-4 py-3 text-xs text-slate-600">
        <span className="font-semibold">Tip:</span> A, B, and C are mechanical — set
        price alerts at the target and stop, and a calendar reminder 6 months from
        entry. D is judgment-based and the only one you have to actively watch news for.
      </p>
    </section>
  )
}
