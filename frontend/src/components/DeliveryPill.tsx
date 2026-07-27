import type { DeliveryInfo } from '../types'

/**
 * Advisory NSE delivery-% pill for pick + position cards.
 *
 * Delivery % = deliverable qty ÷ traded qty. High = shares actually taken to
 * delivery (real accumulation); low = intraday churn inflating raw volume.
 * Renders nothing when no delivery files are on disk (advisory, never blocks).
 */
const LEVEL_STYLE: Record<string, string> = {
  strong: 'bg-emerald-100 text-emerald-900 border-emerald-200',
  moderate: 'bg-amber-100 text-amber-900 border-amber-200',
  weak: 'bg-rose-100 text-rose-900 border-rose-200',
}

export function DeliveryPill({ delivery }: { delivery?: DeliveryInfo | null }) {
  if (!delivery || !delivery.available || delivery.latest_pct == null) return null

  const style = LEVEL_STYLE[delivery.level ?? 'moderate'] ?? LEVEL_STYLE.moderate
  const arrow =
    delivery.trend === 'rising' ? '↑' : delivery.trend === 'falling' ? '↓' : ''
  // Accumulation ladder: today, week, 15d, 30d.
  const longAvg = delivery.avg_30d ?? delivery.avg_20d
  const title = [
    `Delivery today ${delivery.latest_pct}% on ${delivery.latest_date}`,
    delivery.avg_5d != null ? `week avg ${delivery.avg_5d}%` : '',
    delivery.avg_15d != null ? `15-day avg ${delivery.avg_15d}%` : '',
    delivery.avg_30d != null ? `30-day avg ${delivery.avg_30d}%` : '',
    `${delivery.days} day(s) on record`,
    'Deliverable qty ÷ traded qty — high = real accumulation, low = intraday churn',
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${style}`}
      title={title}
    >
      <span className="font-semibold">
        Delivery {delivery.latest_pct.toFixed(0)}%{arrow}
      </span>
      {longAvg != null && (
        <span className="font-normal opacity-80">· 30d {longAvg.toFixed(0)}%</span>
      )}
      {delivery.level && <span className="opacity-75">{delivery.level}</span>}
    </span>
  )
}
