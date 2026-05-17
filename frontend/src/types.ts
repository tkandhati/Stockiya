export type Confidence = 'low' | 'medium' | 'high'

export interface Pick {
  symbol: string
  company: string
  current: number
  best_buy_at: number
  sell_target: number
  stop_loss: number
  headline: string         // one-line thesis for the card
  rationale: string        // multi-line for the detail page
  risk_headline: string    // one-line bear case for the card
  risks: string            // multi-line for the detail page
  confidence: Confidence
  upside_pct: number
  downside_pct: number
  entry_timing: 'early' | 'mid' | 'late' | 'missed' | 'unknown'
  wyckoff_phase:
    | 'accumulation'
    | 'markup'
    | 'distribution'
    | 'markdown'
    | 'indeterminate'
  weinstein_stage: WeinsteinStage
  target_window: TargetWindow
  reasoning: ReasoningPoint[]
  composite_score: number
  rank: number | null
}

export interface PicksResponse {
  date: string
  generated_at: string
  source: 'pipeline'
  demo_mode: boolean
  picks: Pick[]
}

export type AccumVerdict = 'accumulating' | 'neutral' | 'distributing' | 'unknown'
export type WyckoffPhase =
  | 'accumulation'
  | 'markup'
  | 'distribution'
  | 'markdown'
  | 'indeterminate'

export type EntryTiming = 'early' | 'mid' | 'late' | 'missed' | 'unknown'

export type WeinsteinStage =
  | 'stage_1_base'
  | 'stage_1_to_2'
  | 'stage_2_advance'
  | 'stage_3_top'
  | 'stage_4_decline'
  | 'undefined'

export interface TargetWindow {
  center_months: number
  tolerance_months: number
  label: string
  rationale: string
}

export interface ReasoningPoint {
  label: string
  value: string
  state: 'bullish' | 'neutral' | 'bearish'
  why: string
  verify: string
}

export interface StrategySignal {
  name: string
  state: 'bullish' | 'neutral' | 'bearish'
  value: number | null
  label: string
  description: string
}

export interface Accumulation {
  days_used: number
  verdict: AccumVerdict
  wyckoff_phase: WyckoffPhase
  entry_timing: EntryTiming
  entry_timing_note: string
  accum_score: number
  one_liner: string

  // Medium-term
  vol_recent_10d: number | null
  vol_avg_30d: number | null
  vol_avg_90d: number | null
  vol_trend_pct: number | null
  up_down_vol_ratio: number | null
  obv_slope_pct: number | null
  ad_line_slope_pct: number | null
  cmf_21d: number | null
  mfi_14d: number | null
  price_tightness_pct: number | null
  price_change_30d_pct: number | null
  vwap_60d: number | null
  price_vs_vwap_pct: number | null

  // Long-term (the investing-horizon lens)
  weinstein_stage: WeinsteinStage
  weinstein_note: string
  ma_30w: number | null
  ma_30w_slope_pct: number | null
  ma_50d: number | null
  ma_150d: number | null
  ma_200d: number | null
  minervini_template: boolean
  obv_slope_90d_pct: number | null
  obv_slope_180d_pct: number | null
  cmf_60d: number | null
  up_down_vol_ratio_90d: number | null
  base_length_days: number
  vol_qoq_growth_pct: number | null
  price_change_180d_pct: number | null

  // Patterns
  pocket_pivot_count_30d: number
  volume_dry_up: boolean
  canslim_breakout: boolean

  // Block + Bulk deals (NSE institutional trade records)
  block_deal_buy_count_30d: number
  block_deal_sell_count_30d: number
  block_deal_net_qty_ratio: number

  signals: StrategySignal[]
}

export type HealthStatus = 'ok' | 'warn' | 'error'
export type HealthOverall = 'green' | 'yellow' | 'red'

export interface HealthItem {
  id: string
  label: string
  path: string
  status: HealthStatus
  detail: string
  fix: string | null
  last_modified: string | null
}

export interface HealthGroup {
  name: string
  items: HealthItem[]
}

export interface DataHealthReport {
  overall: HealthOverall
  checked_at: string
  summary: { ok: number; warn: number; error: number; total: number }
  groups: HealthGroup[]
}

export interface StockDetail {
  symbol: string
  company: string
  sector: string | null
  industry: string | null
  current: number | null
  day_change_pct: number | null
  fifty_two_w_high: number | null
  fifty_two_w_low: number | null
  ma200: number | null
  return_3m_pct: number | null
  return_1y_pct: number | null
  accumulation: Accumulation
  history_6m: { date: string; close: number }[]
  pick_today: Pick | null
  demo_mode: boolean
}
