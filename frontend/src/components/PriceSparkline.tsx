import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

interface Point {
  date: string
  close: number
}

export function PriceSparkline({ data }: { data: Point[] }) {
  if (!data.length) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
        No price history available.
      </div>
    )
  }
  const closes = data.map((d) => d.close)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const pad = (max - min) * 0.1 || 1

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <h3 className="font-semibold text-slate-900">6-month price</h3>
      <div className="mt-3 h-48">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: '#94a3b8' }}
              tickFormatter={(d) => d.slice(5)}
              minTickGap={32}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[min - pad, max + pad]}
              tick={{ fontSize: 11, fill: '#94a3b8' }}
              width={48}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              labelStyle={{ color: '#0f172a', fontSize: 12 }}
              contentStyle={{
                borderRadius: 8,
                border: '1px solid #e2e8f0',
                fontSize: 12,
              }}
              formatter={(v: number) => [`₹${v.toFixed(2)}`, 'Close']}
            />
            <Line
              type="monotone"
              dataKey="close"
              stroke="#0ea5e9"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
