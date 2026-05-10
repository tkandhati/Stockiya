import { AlertTriangle } from 'lucide-react'

export function Disclaimer() {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
      <p>
        <span className="font-semibold">Educational use only.</span> Picks are
        algorithmic and <span className="font-semibold">not financial advice</span>.
        Markets are risky — always do your own research and consult a SEBI-registered
        advisor before investing.
      </p>
    </div>
  )
}
