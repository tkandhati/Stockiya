export type PriceTrendStatus = 'ready' | 'forming' | 'watch'

export interface PricePoint {
  date: string
  close: number
}

export interface PriceTrendCandidate {
  rank: number
  symbol: string
  company: string
  status: PriceTrendStatus
  score: number
  as_of: string
  close: number
  breakout_price: number
  distance_to_breakout_pct: number
  support_price: number
  ma20: number
  ma50: number
  ma150: number
  ma50_slope_10d_pct: number
  range_10d_pct: number
  atr_14d_pct: number
  higher_low_pct: number
  return_20d_pct: number
  reasons: string[]
  watchouts: string[]
  price_history: PricePoint[]
}

export interface PriceTrendResponse {
  generated_at: string
  as_of: string | null
  universe: string
  scan_limit: number
  scanned_count: number
  eligible_count: number
  skipped_count: number
  demo_mode: boolean
  methodology: 'price_only'
  candidates: PriceTrendCandidate[]
}

export interface PriceTrendLookupResponse {
  requested_symbol: string
  resolved_symbol: string | null
  price_history_available: boolean
  matches_strategy: boolean
  message: string
  candidate: PriceTrendCandidate | null
}
