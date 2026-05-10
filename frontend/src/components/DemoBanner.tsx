import { TestTube2 } from 'lucide-react'

/**
 * Loud banner shown whenever the backend is running with DEMO_MODE=1.
 * The prices, fundamentals, and history are HARD-CODED FIXTURES — not real
 * market data. Critical to call this out so the user doesn't act on
 * synthetic numbers.
 */
export function DemoBanner() {
  return (
    <div className="rounded-lg border-2 border-rose-400 bg-rose-50 px-4 py-3 text-sm text-rose-950 shadow-sm">
      <div className="flex items-start gap-3">
        <TestTube2 className="mt-0.5 h-5 w-5 flex-shrink-0 text-rose-600" />
        <div>
          <div className="font-bold uppercase tracking-wide text-rose-800">
            DEMO MODE — Synthetic data
          </div>
          <p className="mt-1 leading-relaxed">
            The prices, volumes, and fundamentals you see are <strong>bundled fixtures
            for UI development</strong>, not live market data. They are hand-coded
            approximations from earlier in the project, and{' '}
            <strong>do not reflect today's actual prices</strong>. Do not trade on
            them.
          </p>
          <p className="mt-2 text-xs text-rose-800">
            To use real data, run the backend with <code className="rounded bg-rose-100 px-1.5 py-0.5 font-mono">DEMO_MODE=0</code>{' '}
            on a network that allows access to{' '}
            <code className="rounded bg-rose-100 px-1.5 py-0.5 font-mono">query1.finance.yahoo.com</code>{' '}
            (your home Wi-Fi works; corporate networks often don't). The volume engine,
            picker, and exit logic are real — only the input data is synthetic.
          </p>
        </div>
      </div>
    </div>
  )
}
