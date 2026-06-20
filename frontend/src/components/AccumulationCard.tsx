import {
  Activity,
  TrendingUp,
  TrendingDown,
  Minus,
  CheckCircle2,
} from 'lucide-react'
import type {
  Accumulation,
  AccumVerdict,
  EntryTiming,
  StrategySignal,
  WeinsteinStage,
  WyckoffPhase,
} from '../types'

const verdictTone: Record<AccumVerdict, string> = {
  accumulating: 'bg-emerald-100 text-emerald-800 ring-emerald-200',
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
  distributing: 'bg-rose-100 text-rose-800 ring-rose-200',
  unknown: 'bg-slate-100 text-slate-500 ring-slate-200',
}

const phaseLabel: Record<WyckoffPhase, string> = {
  accumulation: 'Accumulation',
  markup: 'Markup',
  distribution: 'Distribution',
  markdown: 'Markdown',
  indeterminate: 'Indeterminate',
}

const phaseTone: Record<WyckoffPhase, string> = {
  accumulation: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  markup: 'bg-sky-50 text-sky-700 border-sky-200',
  distribution: 'bg-rose-50 text-rose-700 border-rose-200',
  markdown: 'bg-rose-50 text-rose-700 border-rose-200',
  indeterminate: 'bg-slate-50 text-slate-600 border-slate-200',
}

const stageMeta: Record<
  WeinsteinStage,
  { label: string; tone: string }
> = {
  stage_1_base: { label: 'Stage 1 — Base', tone: 'border-slate-300 bg-slate-100 text-slate-800' },
  stage_1_to_2: { label: 'Stage 1 → 2 — Turning', tone: 'border-emerald-300 bg-emerald-50 text-emerald-900' },
  stage_2_advance: { label: 'Stage 2 — Advance', tone: 'border-emerald-400 bg-emerald-100 text-emerald-900' },
  stage_3_top: { label: 'Stage 3 — Top forming', tone: 'border-amber-300 bg-amber-50 text-amber-900' },
  stage_4_decline: { label: 'Stage 4 — Decline', tone: 'border-rose-400 bg-rose-100 text-rose-900' },
  undefined: { label: 'Stage —', tone: 'border-slate-200 bg-slate-50 text-slate-600' },
}

const timingMeta: Record<
  EntryTiming,
  { label: string; tone: string; emoji: string }
> = {
  early: {
    label: 'EARLY · pre-breakout (the spot)',
    tone: 'border-emerald-300 bg-emerald-50 text-emerald-900',
    emoji: '🎯',
  },
  mid: {
    label: 'MID · breakout in progress',
    tone: 'border-sky-300 bg-sky-50 text-sky-900',
    emoji: '🚀',
  },
  late: {
    label: 'LATE · price already run',
    tone: 'border-amber-300 bg-amber-50 text-amber-900',
    emoji: '⏱️',
  },
  missed: {
    label: 'MISSED · exit zone, not entry',
    tone: 'border-rose-300 bg-rose-50 text-rose-900',
    emoji: '🛑',
  },
  unknown: {
    label: 'WAIT · no institutional fingerprint yet',
    tone: 'border-slate-300 bg-slate-50 text-slate-700',
    emoji: '⏸️',
  },
}

export function AccumulationCard({ accum }: { accum: Accumulation }) {
  const score = Math.max(-1, Math.min(1, accum.accum_score))
  const meterPct = ((score + 1) / 2) * 100 // map -1..+1 → 0..100

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold text-slate-900">
            <Activity className="h-4 w-4 text-slate-400" />
            Volume signature — institutional footprint
          </h3>
          <p className="mt-1 text-xs text-slate-500">
            Primary lens. Don't invent a thesis — read the volume tape and follow what
            institutions are doing.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wide ${
              stageMeta[accum.weinstein_stage].tone
            }`}
            title="Stan Weinstein long-term stage classification"
          >
            {stageMeta[accum.weinstein_stage].label}
          </span>
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide ring-1 ${
              verdictTone[accum.verdict]
            }`}
          >
            {accum.verdict}
          </span>
          <span
            className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wide ${
              phaseTone[accum.wyckoff_phase]
            }`}
          >
            Wyckoff: {phaseLabel[accum.wyckoff_phase]}
          </span>
        </div>
      </header>

      {accum.weinstein_note && (
        <p className="mt-3 text-xs leading-relaxed text-slate-600">
          {accum.weinstein_note}
        </p>
      )}

      {/* Composite score meter */}
      <div className="mt-5">
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span className="flex items-center gap-1">
            <TrendingDown className="h-3 w-3" /> Distribution
          </span>
          <span className="font-mono text-slate-700">
            score {accum.accum_score >= 0 ? '+' : ''}
            {accum.accum_score.toFixed(2)}
          </span>
          <span className="flex items-center gap-1">
            Accumulation <TrendingUp className="h-3 w-3" />
          </span>
        </div>
        <div className="relative mt-1.5 h-3 w-full overflow-hidden rounded-full bg-gradient-to-r from-rose-200 via-slate-100 to-emerald-200">
          <div
            className="absolute top-0 h-full w-1 rounded bg-slate-900"
            style={{ left: `calc(${meterPct}% - 2px)` }}
            aria-label="composite score marker"
          />
        </div>
      </div>

      {/* Entry timing — the most actionable insight on the page */}
      <div
        className={`mt-5 rounded-xl border p-4 ${timingMeta[accum.entry_timing].tone}`}
      >
        <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider">
          <span aria-hidden>{timingMeta[accum.entry_timing].emoji}</span>
          <span>Entry timing — {timingMeta[accum.entry_timing].label}</span>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed">{accum.entry_timing_note}</p>
      </div>

      <p className="mt-4 text-sm leading-relaxed text-slate-700">
        {accum.one_liner}
      </p>

      {accum.volume_event && accum.volume_event.kind !== 'neutral' && (
        <div
          className={`mt-4 rounded-xl border p-4 ${
            accum.volume_event.direction === 'bullish'
              ? 'border-emerald-200 bg-emerald-50/70 text-emerald-950'
              : accum.volume_event.direction === 'bearish'
              ? 'border-rose-200 bg-rose-50/70 text-rose-950'
              : 'border-slate-200 bg-slate-50 text-slate-800'
          }`}
        >
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider">
            {accum.volume_event.direction === 'bearish' ? (
              <TrendingDown className="h-4 w-4" />
            ) : (
              <TrendingUp className="h-4 w-4" />
            )}
            <span>Early volume indication - {accum.volume_event.label}</span>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed">
            {accum.volume_event.detail}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] md:grid-cols-4">
            <EventMetric
              label="Volume"
              value={
                accum.volume_event.vol_ratio_50 == null
                  ? '-'
                  : `${accum.volume_event.vol_ratio_50.toFixed(2)}x ADV50`
              }
            />
            <EventMetric
              label="Prior quiet"
              value={
                accum.volume_event.quiet_ratio_5_50 == null
                  ? '-'
                  : `${accum.volume_event.quiet_ratio_5_50.toFixed(2)}x`
              }
            />
            <EventMetric
              label="Close location"
              value={
                accum.volume_event.close_location == null
                  ? '-'
                  : `${Math.round(accum.volume_event.close_location * 100)}%`
              }
            />
            <EventMetric
              label="OBV 20d"
              value={
                accum.volume_event.obv_20d_slope_pct == null
                  ? '-'
                  : `${accum.volume_event.obv_20d_slope_pct >= 0 ? '+' : ''}${accum.volume_event.obv_20d_slope_pct.toFixed(1)}%`
              }
            />
          </div>
        </div>
      )}

      {/* Pattern flags */}
      {(accum.pocket_pivot_count_30d > 0 ||
        accum.volume_dry_up ||
        accum.canslim_breakout) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {accum.pocket_pivot_count_30d > 0 && (
            <Flag tone="emerald" label={`Pocket Pivot ×${accum.pocket_pivot_count_30d}`} />
          )}
          {accum.volume_dry_up && <Flag tone="emerald" label="Volume Dry-Up (pre-breakout)" />}
          {accum.canslim_breakout && <Flag tone="emerald" label="CAN SLIM breakout today" />}
        </div>
      )}

      {/* LONG-TERM metrics — the investing horizon lens */}
      <div className="mt-5 rounded-lg border border-indigo-100 bg-indigo-50/40 p-4">
        <h4 className="text-xs font-bold uppercase tracking-wider text-indigo-900">
          Long-term lens (investing horizon)
        </h4>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-3">
          <Metric
            label="OBV (90d)"
            value={
              accum.obv_slope_90d_pct == null
                ? '—'
                : `${accum.obv_slope_90d_pct >= 0 ? '+' : ''}${accum.obv_slope_90d_pct.toFixed(0)}%`
            }
            tone={
              accum.obv_slope_90d_pct == null
                ? 'neutral'
                : accum.obv_slope_90d_pct >= 5
                ? 'good'
                : accum.obv_slope_90d_pct <= -5
                ? 'bad'
                : 'neutral'
            }
          />
          <Metric
            label="OBV (180d)"
            value={
              accum.obv_slope_180d_pct == null
                ? '—'
                : `${accum.obv_slope_180d_pct >= 0 ? '+' : ''}${accum.obv_slope_180d_pct.toFixed(0)}%`
            }
            tone={
              accum.obv_slope_180d_pct == null
                ? 'neutral'
                : accum.obv_slope_180d_pct >= 5
                ? 'good'
                : accum.obv_slope_180d_pct <= -5
                ? 'bad'
                : 'neutral'
            }
          />
          <Metric
            label="CMF (60d)"
            value={accum.cmf_60d == null ? '—' : accum.cmf_60d.toFixed(2)}
            tone={
              accum.cmf_60d == null
                ? 'neutral'
                : accum.cmf_60d >= 0.05
                ? 'good'
                : accum.cmf_60d <= -0.05
                ? 'bad'
                : 'neutral'
            }
          />
          <Metric
            label="Up/Down vol (90d)"
            value={
              accum.up_down_vol_ratio_90d == null
                ? '—'
                : `${accum.up_down_vol_ratio_90d.toFixed(2)}×`
            }
            tone={
              accum.up_down_vol_ratio_90d == null
                ? 'neutral'
                : accum.up_down_vol_ratio_90d >= 1.3
                ? 'good'
                : accum.up_down_vol_ratio_90d <= 0.77
                ? 'bad'
                : 'neutral'
            }
          />
          <Metric
            label="Base length"
            value={`${accum.base_length_days} sessions`}
            tone={accum.base_length_days >= 60 ? 'good' : 'neutral'}
          />
          <Metric
            label="QoQ vol growth"
            value={
              accum.vol_qoq_growth_pct == null
                ? '—'
                : `${accum.vol_qoq_growth_pct >= 0 ? '+' : ''}${accum.vol_qoq_growth_pct.toFixed(0)}%`
            }
            tone={
              accum.vol_qoq_growth_pct == null
                ? 'neutral'
                : accum.vol_qoq_growth_pct >= 15
                ? 'good'
                : accum.vol_qoq_growth_pct <= -15
                ? 'bad'
                : 'neutral'
            }
          />
          <Metric
            label="30-week MA slope"
            value={
              accum.ma_30w_slope_pct == null
                ? '—'
                : `${accum.ma_30w_slope_pct >= 0 ? '+' : ''}${accum.ma_30w_slope_pct.toFixed(1)}%`
            }
            tone={
              accum.ma_30w_slope_pct == null
                ? 'neutral'
                : accum.ma_30w_slope_pct >= 1
                ? 'good'
                : accum.ma_30w_slope_pct <= -1
                ? 'bad'
                : 'neutral'
            }
          />
          <Metric
            label="180d price change"
            value={
              accum.price_change_180d_pct == null
                ? '—'
                : `${accum.price_change_180d_pct >= 0 ? '+' : ''}${accum.price_change_180d_pct.toFixed(1)}%`
            }
            tone={
              accum.price_change_180d_pct == null
                ? 'neutral'
                : accum.price_change_180d_pct > 35
                ? 'bad'
                : accum.price_change_180d_pct > 0
                ? 'good'
                : 'neutral'
            }
          />
          <Metric
            label="Minervini Trend Template"
            value={accum.minervini_template ? 'Yes ✓' : 'No'}
            tone={accum.minervini_template ? 'good' : 'neutral'}
          />
        </dl>
      </div>

      {/* MEDIUM-TERM metrics grid */}
      <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-slate-100 pt-4 text-sm md:grid-cols-3">
        <Metric
          label="10d vs 30d volume"
          value={
            accum.vol_trend_pct == null
              ? '—'
              : `${accum.vol_trend_pct >= 0 ? '+' : ''}${accum.vol_trend_pct.toFixed(1)}%`
          }
          tone={
            accum.vol_trend_pct == null
              ? 'neutral'
              : accum.vol_trend_pct > 5
              ? 'good'
              : accum.vol_trend_pct < -5
              ? 'bad'
              : 'neutral'
          }
        />
        <Metric
          label="Up/Down vol ratio"
          value={accum.up_down_vol_ratio == null ? '—' : `${accum.up_down_vol_ratio.toFixed(2)}×`}
          tone={
            accum.up_down_vol_ratio == null
              ? 'neutral'
              : accum.up_down_vol_ratio >= 1.4
              ? 'good'
              : accum.up_down_vol_ratio <= 0.7
              ? 'bad'
              : 'neutral'
          }
        />
        <Metric
          label="OBV (30d)"
          value={
            accum.obv_slope_pct == null
              ? '—'
              : `${accum.obv_slope_pct >= 0 ? '+' : ''}${accum.obv_slope_pct.toFixed(1)}%`
          }
          tone={
            accum.obv_slope_pct == null
              ? 'neutral'
              : accum.obv_slope_pct >= 5
              ? 'good'
              : accum.obv_slope_pct <= -5
              ? 'bad'
              : 'neutral'
          }
        />
        <Metric
          label="Chaikin Money Flow (21d)"
          value={accum.cmf_21d == null ? '—' : accum.cmf_21d.toFixed(2)}
          tone={
            accum.cmf_21d == null
              ? 'neutral'
              : accum.cmf_21d >= 0.1
              ? 'good'
              : accum.cmf_21d <= -0.1
              ? 'bad'
              : 'neutral'
          }
        />
        <Metric
          label="MFI (14d)"
          value={accum.mfi_14d == null ? '—' : accum.mfi_14d.toFixed(0)}
          tone={
            accum.mfi_14d == null
              ? 'neutral'
              : accum.mfi_14d >= 80
              ? 'bad'
              : accum.mfi_14d >= 50
              ? 'good'
              : 'neutral'
          }
        />
        <Metric
          label="Price vs 60d VWAP"
          value={
            accum.price_vs_vwap_pct == null
              ? '—'
              : `${accum.price_vs_vwap_pct >= 0 ? '+' : ''}${accum.price_vs_vwap_pct.toFixed(1)}%`
          }
          tone={
            accum.price_vs_vwap_pct == null
              ? 'neutral'
              : accum.price_vs_vwap_pct >= 0
              ? 'good'
              : 'bad'
          }
        />
        <Metric
          label="20d price tightness"
          value={
            accum.price_tightness_pct == null
              ? '—'
              : `${accum.price_tightness_pct.toFixed(1)}%`
          }
          tone={
            accum.price_tightness_pct == null
              ? 'neutral'
              : accum.price_tightness_pct < 8
              ? 'good'
              : 'neutral'
          }
        />
        <Metric
          label="30d price change"
          value={
            accum.price_change_30d_pct == null
              ? '—'
              : `${accum.price_change_30d_pct >= 0 ? '+' : ''}${accum.price_change_30d_pct.toFixed(1)}%`
          }
          tone={
            accum.price_change_30d_pct == null
              ? 'neutral'
              : accum.price_change_30d_pct > 25
              ? 'bad'
              : accum.price_change_30d_pct > 0
              ? 'good'
              : 'neutral'
          }
        />
        <Metric
          label="A/D line slope (30d)"
          value={
            accum.ad_line_slope_pct == null
              ? '—'
              : `${accum.ad_line_slope_pct >= 0 ? '+' : ''}${accum.ad_line_slope_pct.toFixed(1)}%`
          }
          tone={
            accum.ad_line_slope_pct == null
              ? 'neutral'
              : accum.ad_line_slope_pct >= 5
              ? 'good'
              : accum.ad_line_slope_pct <= -5
              ? 'bad'
              : 'neutral'
          }
        />
      </dl>

      {/* Strategy-by-strategy breakdown */}
      {accum.signals.length > 0 && (
        <details className="mt-5 group" open>
          <summary className="cursor-pointer select-none text-xs font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-700">
            Strategies firing ({accum.signals.length})
          </summary>
          <ul className="mt-3 space-y-2">
            {accum.signals.map((s) => (
              <li
                key={s.name}
                className="flex items-start gap-3 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2 text-sm"
              >
                <SigIcon state={s.state} />
                <div className="flex-1">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-medium text-slate-900">{s.name}</span>
                    <span className="font-mono text-xs text-slate-600">{s.label}</span>
                  </div>
                  <p className="mt-0.5 text-xs leading-relaxed text-slate-600">
                    {s.description}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="mt-5 rounded-lg bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-600">
        <span className="font-semibold">Volume-first exit rule:</span> exit immediately if
        the picture inverts — OBV rolls over, CMF turns negative, or down-day volume
        starts dominating up-day volume. That is the institutional footprint leaving.
        {' '}
        <span className="text-slate-500">
          Based on {accum.days_used} sessions of price/volume data.
        </span>
      </p>
    </section>
  )
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: 'good' | 'bad' | 'neutral'
}) {
  const color = {
    good: 'text-emerald-700',
    bad: 'text-rose-700',
    neutral: 'text-slate-900',
  }[tone]
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`mt-0.5 font-medium tabular-nums ${color}`}>{value}</dd>
    </div>
  )
}

function EventMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/70 bg-white/60 px-2 py-1">
      <dt className="text-[10px] uppercase tracking-wide opacity-70">{label}</dt>
      <dd className="font-mono font-semibold">{value}</dd>
    </div>
  )
}

function Flag({
  tone,
  label,
}: {
  tone: 'emerald' | 'rose' | 'slate'
  label: string
}) {
  const tones = {
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    rose: 'border-rose-200 bg-rose-50 text-rose-800',
    slate: 'border-slate-200 bg-slate-50 text-slate-700',
  }[tone]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${tones}`}
    >
      <CheckCircle2 className="h-3 w-3" /> {label}
    </span>
  )
}

function SigIcon({ state }: { state: StrategySignal['state'] }) {
  if (state === 'bullish') return <TrendingUp className="mt-0.5 h-4 w-4 text-emerald-600" />
  if (state === 'bearish') return <TrendingDown className="mt-0.5 h-4 w-4 text-rose-600" />
  return <Minus className="mt-0.5 h-4 w-4 text-slate-400" />
}

