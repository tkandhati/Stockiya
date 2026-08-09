import type { PriceTrendLookupResponse, PriceTrendResponse } from './types'

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export function fetchPriceTrends(refresh = false): Promise<PriceTrendResponse> {
  const query = refresh ? '?refresh=true' : ''
  return fetch(`/api/price-trends${query}`).then(jsonOrThrow<PriceTrendResponse>)
}

export function lookupPriceTrend(symbol: string): Promise<PriceTrendLookupResponse> {
  const query = new URLSearchParams({ symbol })
  return fetch(`/api/price-trends/lookup?${query}`).then(
    jsonOrThrow<PriceTrendLookupResponse>,
  )
}
