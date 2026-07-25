import type {
  BacktestRequest,
  BacktestResponse,
  DataHealthReport,
  PicksResponse,
  PositionAsOf,
  PositionDatesResponse,
  PositionsResponse,
  StockDetail,
  TakePositionRequest,
} from './types'

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

export function fetchPositions(): Promise<PositionsResponse> {
  return fetch('/api/positions').then(jsonOrThrow<PositionsResponse>)
}

export function takePosition(
  pickId: string,
  req: TakePositionRequest,
): Promise<PositionsResponse> {
  return fetch(`/api/positions/${encodeURIComponent(pickId)}/take`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  }).then(jsonOrThrow<PositionsResponse>)
}

export function declinePosition(pickId: string): Promise<PositionsResponse> {
  return fetch(`/api/positions/${encodeURIComponent(pickId)}/decline`, {
    method: 'POST',
  }).then(jsonOrThrow<PositionsResponse>)
}

export function fetchStockDetail(symbol: string): Promise<StockDetail> {
  return fetch(`/api/stock/${encodeURIComponent(symbol)}`).then(jsonOrThrow<StockDetail>)
}

// Dates a symbol has an on-disk trace for (newest first) — date-picker options.
export function getPositionDates(symbol: string): Promise<PositionDatesResponse> {
  return fetch(`/api/positions/${encodeURIComponent(symbol)}/dates`)
    .then(jsonOrThrow<PositionDatesResponse>)
}

// A position's accumulation card reconstructed as of a past date (file-only).
export function getPositionAsOf(symbol: string, date: string): Promise<PositionAsOf> {
  return fetch(
    `/api/positions/${encodeURIComponent(symbol)}/as_of/${encodeURIComponent(date)}`,
  ).then(jsonOrThrow<PositionAsOf>)
}

export function fetchDataHealth(): Promise<DataHealthReport> {
  return fetch('/api/health/data').then(jsonOrThrow<DataHealthReport>)
}

export function runBacktest(req: BacktestRequest): Promise<BacktestResponse> {
  return fetch('/api/backtest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  }).then(jsonOrThrow<BacktestResponse>)
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

// Format an ISO timestamp (e.g. picks `generated_at`) as an IST date + time.
export function fmtDateTimeIST(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Kolkata',
  }).format(d)
}
