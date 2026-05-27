// Type contracts for the Stockya UI.
// Pick shape mirrors backend/stages/hypothesis.py:build_pick_payload — the
// gates-based spine. Legacy fields are kept optional so the StockDetailPage
// can still render older cached picks without crashing during transition.

export type Confidence = 'low' | 'medium' | 'high'

// --------------------------------------------------------------------------
// New-spine sub-shapes
// --------------------------------------------------------------------------

export interface PricePlan {
  account_value: number
  entry: number
  stop: number
  t1: number
  t2: number
  shares_total: number
  shares_at_t1: number
  shares_at_t2: number
  risk_amount: number
  risk_pct_of_account: number
  notes?: string[]
}

export interface ExitStep {
  milestone_days: number
  action: string
  trigger: string
  new_stop?: number | null
  note?: string
}

export interface ExitSchedule {
  day_45: ExitStep
  day_90: ExitStep
  day_180: ExitStep
}

export interface Confirmation {
  score: number
  gate_margin_sum?: number
  bonus_count?: number
  bonus_weight?: number
  bonuses_fired?: string[]
}

export interface GatesEvidence {
  CS?: string[]
  VD?: string[]
  BR?: string[]
}

// --------------------------------------------------------------------------
// Pick — new + legacy fields all coexist; new shape is canonical.
// --------------------------------------------------------------------------

export interface Pick {
  symbol: string
  rank?: number | null
  trace_id?: string | null
  company?: string | null
  sector?: string | null
  industry?: string | null
  current_price?: number | null
  headline?: string
  confirmation?: Confirmation
  price_plan?: PricePlan
  exit_schedule?: ExitSchedule
  distribution_flip_exit?: string
  gates_evidence?: GatesEvidence

  // ---- Legacy aliases (still populated by build_pick_payload) ----
  best_buy_at?: number
  sell_target?: number
  stop_loss?: number
  upside_pct?: number
  downside_pct?: number
  shares_to_buy?: number

  // ---- Old-spine fields (no longer emitted; kept for StockDetailPage compat)
  current?: number
  rationale?: string
  risks?: string
  risk_headline?: string
  confidence?: Confidence
  entry_timing?: 'early' | 'mid' | 'late' | 'missed' | 'unknown'
  wyckoff_phase?:
    | 'accumulation'
    | 'markup'
    | 'distribution'
    | 'markdown'
    | 'indeterminate'
  weinstein_stage?: WeinsteinStage
  target_window?: TargetWindow
  reasoning?: ReasoningPoint[]
  composite_score?: number
}

// --------------------------------------------------------------------------
// Regime + envelope
// --------------------------------------------------------------------------

export interface RegimeCheck {
  symbol: string
  close?: number | null
  ma50?: number | null
  gap_pct?: number | null
  passed: boolean
  reason: string
}

export interface Regime {
  passed: boolean
  summary: string
  checks: RegimeCheck[]
}

// --------------------------------------------------------------------------
// Active positions (open holdings dashboard)
// --------------------------------------------------------------------------

export type PositionAction =
  | 'hold'
  | 'tighten_stop_45'
  | 'exit_t1'
  | 'exit_t2'
  | 'exit_stop'
  | 'exit_time_stop'
  | 'exit_final'

export interface TimeStops {
  day_45: string
  day_90: string
  day_180: string
}

export interface Position {
  pick_id: string
  trace_id: string
  symbol: string
  company: string
  entry_date: string
  days_held: number
  entry_price: number
  stop_price: number
  t1_price: number
  t2_price: number
  current_price?: number | null
  pnl_pct?: number | null
  status: 'open' | 'partial_t1' | string
  hit_t1: boolean
  hit_t1_date?: string
  shares_total: number
  shares_at_t1: number
  shares_at_t2: number
  confirmation_score?: number
  headline?: string
  action: PositionAction
  action_note: string
  new_stop?: number | null
  time_stops: TimeStops
}

export interface PositionsResponse {
  date_ist: string
  count: number
  positions: Position[]
}

export interface NearMissGate {
  stage_id: string
  label: string
  evidence?: string[]
  reason?: string | null
}

export interface NearMiss {
  symbol: string
  company: string
  passed_count: number
  passed_gates: NearMissGate[]
  failed_gate: NearMissGate
}

export interface PicksResponse {
  date: string
  generated_at: string
  source: 'pipeline'
  demo_mode: boolean
  regime?: Regime
  message?: string
  picks: Pick[]
  near_misses?: NearMiss[]
}

// --------------------------------------------------------------------------
// Stock-detail panel (unchanged; reads from the legacy AccumulationDTO)
// --------------------------------------------------------------------------

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

  // Long-term
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

  // Block + Bulk deals
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
