import type { Pick } from '../types'

// Delivery-weighted view — SCORING-NEUTRAL, presentation-only.
//
// The SAME canonical picks above, re-ordered by blending the NSE delivery
// accumulation signal into the DISPLAY score as a small ± tilt. It never
// changes which stocks are picked (the volume composite still owns selection)
// — it only answers "of today's picks, which carry the strongest sustained
// delivery-backed accumulation?". Delivery data is often absent behind the
// firewall; when it is, the tilt is 0 and this list matches the order above.
const DELIVERY_BLEND = 0.15 // delivery can move a pick's DISPLAY score by ±15%

function tiltOf(signal: number | null | undefined): number {
  if (signal == null) return 0
  return DELIVERY_BLEND * (2 * signal - 1) // signal 0..1 -> tilt -W..+W
}

function driftGlyph(drift?: string | null): string {
  return drift === 'rising' ? '↑' : drift === 'falling' ? '↓' : '→'
}

export function DeliveryWeightedPicks({ picks }: { picks: Pick[] }) {
  if (!picks || picks.length === 0) return null

  const rows = picks
    .map((p) => {
      const base = p.confirmation?.score ?? 0
      const d = p.delivery
      const sig = d?.available ? d.accum_signal ?? null : null
      const tilt = tiltOf(sig)
      return { p, base, d, sig, tilt, blended: base * (1 + tilt) }
    })
    .sort((a, b) => b.blended - a.blended)

  const anyDelivery = rows.some((r) => r.sig != null)

  return (
    <section className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50/40 p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-sm font-semibold text-slate-900">
          Delivery-weighted{' '}
          <span className="font-normal text-slate-500">— same picks, re-ranked</span>
        </h3>
        <span className="text-xs text-slate-500">
          delivery blended ±{Math.round(DELIVERY_BLEND * 100)}% · display-only
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        Does <span className="font-medium">not</span> change selection — the volume composite
        still chooses the picks above. This only tilts their display order by sustained NSE
        delivery (level + consecutive-day streak + multi-week drift).
      </p>

      {!anyDelivery && (
        <p className="mt-3 text-xs text-slate-500">
          No delivery data on disk today — order matches the picks above.
        </p>
      )}

      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
            <th className="py-2 pr-3 font-medium">#</th>
            <th className="py-2 pr-3 font-medium">Symbol</th>
            <th className="py-2 pr-3 font-medium">Base</th>
            <th className="py-2 pr-3 font-medium">Delivery</th>
            <th className="py-2 pr-3 font-medium">Tilt</th>
            <th className="py-2 pr-3 font-medium">Weighted</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => {
            const d = r.d
            const tiltPct = Math.round(r.tilt * 1000) / 10
            return (
              <tr key={r.p.symbol} className="text-slate-700">
                <td className="py-2 pr-3 font-mono text-xs">{i + 1}</td>
                <td className="py-2 pr-3 font-mono text-xs">
                  <div className="font-semibold">{r.p.symbol.replace('.NS', '')}</div>
                  {r.p.rank != null && (
                    <div className="text-[10px] text-slate-400">pick #{r.p.rank}</div>
                  )}
                </td>
                <td className="py-2 pr-3 font-mono">{r.base.toFixed(3)}</td>
                <td className="py-2 pr-3">
                  {d?.available ? (
                    <span className="text-xs text-slate-600">
                      {d.latest_pct != null ? `${Math.round(d.latest_pct)}%` : '—'}{' '}
                      {driftGlyph(d.accum_drift)}
                      {d.accum_streak_days ? ` · ${d.accum_streak_days}d` : ''}
                    </span>
                  ) : (
                    <span className="text-[10px] text-slate-400">no data</span>
                  )}
                </td>
                <td
                  className={`py-2 pr-3 font-mono text-xs ${
                    r.tilt > 0
                      ? 'text-emerald-700'
                      : r.tilt < 0
                      ? 'text-rose-600'
                      : 'text-slate-400'
                  }`}
                >
                  {r.tilt === 0 ? '0%' : `${tiltPct > 0 ? '+' : ''}${tiltPct}%`}
                </td>
                <td className="py-2 pr-3 font-mono font-semibold">{r.blended.toFixed(3)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
