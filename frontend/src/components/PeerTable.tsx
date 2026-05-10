import type { Peer } from '../types'
import { fmtCr, fmtNum, fmtPct } from '../api'

export function PeerTable({ peers }: { peers: Peer[] }) {
  if (!peers.length) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
        No peer data available.
      </div>
    )
  }
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-5 py-3">
        <h3 className="font-semibold text-slate-900">Peer comparison</h3>
        <p className="mt-0.5 text-xs text-slate-500">
          Same-sector peers, sorted by P/E (lower = cheaper). Highlighted row is the
          stock you're looking at.
        </p>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="px-5 py-2">Company</th>
            <th className="px-5 py-2 text-right">P/E</th>
            <th className="px-5 py-2 text-right">P/B</th>
            <th className="px-5 py-2 text-right">Market cap</th>
            <th className="px-5 py-2 text-right">1y return</th>
          </tr>
        </thead>
        <tbody>
          {peers.map((p) => (
            <tr
              key={p.symbol}
              className={`border-b border-slate-100 last:border-0 ${
                p.is_target ? 'bg-amber-50/60 font-medium' : ''
              }`}
            >
              <td className="px-5 py-2.5">
                <div className="text-slate-900">{p.company}</div>
                <div className="font-mono text-xs text-slate-500">{p.symbol}</div>
              </td>
              <td className="px-5 py-2.5 text-right tabular-nums">{fmtNum(p.pe)}</td>
              <td className="px-5 py-2.5 text-right tabular-nums">{fmtNum(p.pb)}</td>
              <td className="px-5 py-2.5 text-right tabular-nums">{fmtCr(p.market_cap_cr)}</td>
              <td
                className={`px-5 py-2.5 text-right tabular-nums ${
                  p.return_1y_pct == null
                    ? 'text-slate-400'
                    : p.return_1y_pct >= 0
                    ? 'text-emerald-700'
                    : 'text-rose-700'
                }`}
              >
                {fmtPct(p.return_1y_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
