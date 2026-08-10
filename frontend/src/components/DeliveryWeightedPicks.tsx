import type { DeliveryAnalysisRow } from '../types'

// Delivery-led analysis — a FRESH, SCORING-NEUTRAL ranking (not a re-rank of the
// picks). The backend scores every hard-gate survivor today with NSE delivery
// weighted in as a first-class term and returns its own shortlist, so a
// strong-delivery name can appear here even if it isn't a volume pick. It never
// changes selection or any other section. When no delivery data is on disk the
// backend collapses this to plain composite order.
function driftGlyph(drift?: string | null): string {
  return drift === 'rising' ? '↑' : drift === 'falling' ? '↓' : '→'
}

export function DeliveryWeightedPicks({ rows }: { rows: DeliveryAnalysisRow[] }) {
  if (!rows || rows.length === 0) return null
  const anyDelivery = rows.some((r) => r.delivery_signal != null)

  return (
    <section className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50/40 p-5">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-sm font-semibold text-slate-900">
          Delivery-led analysis{' '}
          <span className="font-normal text-slate-500">— fresh ranking</span>
        </h3>
        <span className="text-xs text-slate-500">delivery-weighted · display-only</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        A <span className="font-medium">fresh</span> ranking of today&apos;s eligible field with
        NSE delivery weighted in — independent of the picks above. It does{' '}
        <span className="font-medium">not</span> change selection; a strong-delivery name can rank
        here even when it isn&apos;t a pick.
      </p>

      {!anyDelivery && (
        <p className="mt-3 text-xs text-slate-500">
          No delivery data on disk today — ranking falls back to composite score.
        </p>
      )}

      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
            <th className="py-2 pr-3 font-medium">#</th>
            <th className="py-2 pr-3 font-medium">Symbol</th>
            <th className="py-2 pr-3 font-medium">Base S</th>
            <th className="py-2 pr-3 font-medium">Delivery</th>
            <th className="py-2 pr-3 font-medium">Fresh</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => {
            const d = r.delivery
            return (
              <tr key={r.symbol} className="text-slate-700">
                <td className="py-2 pr-3 font-mono text-xs">{i + 1}</td>
                <td className="py-2 pr-3 font-mono text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold">{r.symbol.replace('.NS', '')}</span>
                    {r.in_picks && (
                      <span className="rounded bg-indigo-100 px-1 py-0.5 text-[9px] font-semibold uppercase text-indigo-700">
                        pick
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-slate-400">{r.company}</div>
                </td>
                <td className="py-2 pr-3 font-mono">{r.base_score.toFixed(3)}</td>
                <td className="py-2 pr-3">
                  {r.delivery_signal != null ? (
                    <span className="text-xs text-slate-600">
                      {Math.round(r.delivery_signal * 100)}%
                      {d?.available && (
                        <>
                          {' '}
                          {driftGlyph(d.accum_drift)}
                          {d.accum_streak_days ? ` · ${d.accum_streak_days}d` : ''}
                        </>
                      )}
                    </span>
                  ) : (
                    <span className="text-[10px] text-slate-400">no data</span>
                  )}
                </td>
                <td className="py-2 pr-3 font-mono font-semibold text-emerald-800">
                  {r.fresh_score.toFixed(3)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
