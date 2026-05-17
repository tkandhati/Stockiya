import type { DataHealthReport, PicksResponse, StockDetail } from './types'

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body}` : ''}`)
  }
  return res.json() as Promise<T>
}

export function fetchPicks(): Promise<PicksResponse> {
  return fetch('/api/picks').then(jsonOrThrow<PicksResponse>)
}

export function refreshPicks(): Promise<PicksResponse> {
  return fetch('/api/picks/refresh', { method: 'POST' }).then(jsonOrThrow<PicksResponse>)
}

export function fetchStockDetail(symbol: string): Promise<StockDetail> {
  return fetch(`/api/stock/${encodeURIComponent(symbol)}`).then(jsonOrThrow<StockDetail>)
}

export function fetchDataHealth(): Promise<DataHealthReport> {
  return fetch('/api/health/data').then(jsonOrThrow<DataHealthReport>)
}

export function fmtINR(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(n)
}

export function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return '—'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(digits)}%`
}
