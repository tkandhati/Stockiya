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

export interface GateConfirmationStatus {
  // "hard_confirmed" = every listed soft leg passed on its own boolean check.
  // "composite_qualified" = the weighted composite S >= tau, but at least one
  // listed leg failed its own terms. UI must not claim "all gates passed" in
  // the composite-qualified case — that reads as a bug when a leg's own row
  // shows failing evidence.
  status: 'hard_confirmed' | 'composite_qualified'
  passed: string[]
  failed: string[]
  counts: { passed: number; total: number }
}

export interface VolumeEvent {
  kind: string
  direction: 'bullish' | 'bearish' | 'neutral'
  score: number
  label: string
  detail: string
  is_spike: boolean
  vol_ratio_50?: number | null
  quiet_ratio_5_50?: number | null
  close_location?: number | null
  price_change_pct?: number | null
  break_pct?: number | null
  breakdown_pct?: number | null
  close_vs_ma50_pct?: number | null
  ret_30d_pct?: number | null
  obv_20d_slope_pct?: number | null
  base_days?: number
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
  gate_confirmation_status?: GateConfirmationStatus
  volume_event?: VolumeEvent | null

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
  | 'exit_distribution'

export type SignalState =
  | 'strong'
  | 'stable'
  | 'weakening'
  | 'flipped'
  | 'unknown'

export interface IndicatorDelta {
  name: string
  label: string
  entry_value?: number | null
  current_value?: number | null
  state: SignalState
  description: string
}

export interface Trajectory {
  overall: SignalState
  indicators: IndicatorDelta[]
  headline: string
  exit_recommendation: boolean
}

export interface TimeStops {
  day_45: string
  day_90: string
  day_180: string
}

export type PositionOwnership = 'suggested' | 'paper' | 'live'

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
  // Q1 — expected T1 day
  expected_t1_date?: string
  expected_t1_trading_days?: number
  t1_status?: 'on_track' | 'overdue' | 'hit'
  days_to_expected_t1?: number
  // Q2 — signal trajectory since entry
  trajectory?: Trajectory | null
  // V1 — ownership + user-actual fill.
  // `entry_date` / `entry_price` / `shares_total` above are the *effective*
  // values (user's if given, scanner's otherwise); scanner_* fields carry
  // the raw scanner numbers so the UI can show both.
  ownership: PositionOwnership
  scanner_entry_date?: string
  scanner_entry_price?: number
  scanner_shares?: number
  user_entry_date?: string
  user_entry_price?: number | null
  user_shares?: number | null
  user_notes?: string
}

export interface TakePositionRequest {
  ownership: 'paper' | 'live'
  user_entry_date?: string
  user_entry_price?: number | null
  user_shares?: number | null
  user_notes?: string
}

export interface PositionsResponse {
  date_ist: string
  count: number
  positions: Position[]
}

// Closest-to-firing empty-state panel: one tabbed panel replaces the old
// NearMiss / EarlyVolumeSignal / ReadyToBreak trio. Four fields per row —
// every column earns its place.

export interface PulledDownBy {
  stage_id: string | null
  label: string
  current_margin: number
  weight: number
  reason: string
}

export interface ClosestRow {
  symbol: string
  company: string
  composite_score: number
  gap_to_tau: number
  pulled_down_by: PulledDownBy
}

export interface ClosestToFiring {
  accumulation: ClosestRow[]
  breakout: ClosestRow[]
  overall: ClosestRow[]
}

export interface PicksResponse {
  date: string
  generated_at: string
  source: 'pipeline'
  demo_mode: boolean
  regime?: Regime
  message?: string
  picks: Pick[]
  closest_to_firing?: ClosestToFiring
}

// --------------------------------------------------------------------------
// Backtest / Simulation
// --------------------------------------------------------------------------

export interface ThresholdDeviation {
  value: number
  canonical: number
}

export interface BacktestAssumptions {
  hold_days: number
  top_n: number
  capital: number
  fill_model: string
  stop_pct: number
  t1_pct: number
  t2_pct: number
  costs_modeled: boolean
  survivorship_note: string
  thresholds?: Record<string, number>
  thresholds_deviated?: Record<string, ThresholdDeviation>
}

export interface BacktestOverrides {
  hr_parabolic_30d_max_pct?: number
  hr_extended_vs_ma50_max?: number
  lt_obv_90d_slope_min?: number
  cs_atr_pct_max?: number
  vd_dryup_ratio?: number
  br_volume_mult?: number
}

export interface BacktestGateRow {
  stage_id: string
  label: string
  passed: boolean | null
  score?: number
  features?: Record<string, unknown>
  evidence: string[]
  reason: string
  fix_point?: string
  explanation: string
}

export interface ForwardWalkBar {
  day: number
  date: string
  open: number
  high: number
  low: number
  close: number
  event: string | null
}

export interface ForwardWalk {
  entry_date?: string
  entry_px: number
  stop_px: number
  t1_px: number
  t2_px: number
  exit_reason: string
  exit_day: number
  exit_px_avg: number
  return_pct: number
  hit_t1_day: number | null
  hit_t2_day: number | null
  hit_stop_day: number | null
  daily_path: ForwardWalkBar[]
}

export interface BacktestSymbolBlock {
  symbol: string
  in_universe?: boolean
  passed_all_gates?: boolean
  killing_gate?: string | null
  killing_gate_label?: string | null
  chain?: BacktestGateRow[]
  counterfactual?: Pick & { is_counterfactual?: boolean; error?: string }
  forward?: ForwardWalk | null
  snapshot?: { company?: string | null; sector?: string | null; industry?: string | null }
  error?: string
}

export interface BacktestFunnelRow {
  stage_id: string
  label: string
  eval: number
  pass: number
  fail: number
  top_reason: string
}

export interface BacktestSelected {
  rank: number | null
  symbol: string
  company?: string | null
  confirmation: Confirmation
  payload: Pick
  forward: ForwardWalk | null
}

export interface BacktestSummary {
  n_picks: number
  hit_rate_pct: number | null
  avg_return_pct: number | null
  sum_return_pct: number | null
}

export type ScanGateId = 'U' | 'I' | 'HR' | 'LT' | 'CS' | 'VD' | 'BR'

export interface ScanDay {
  date: string
  gates: Record<ScanGateId, boolean | null>
  killed_at?: string | null
  note?: string
  features?: {
    vol_ratio_5_50?: number | null
    divergence_form?: string | null
  }
}

export interface ScanCounts {
  [gateId: string]: { eval: number; pass: number; fail: number }
}

export interface ScanFullPass {
  as_of: string
  forward: ForwardWalk | null
}

export interface ScanResponse {
  mode: 'C'
  scope: 'symbol'
  symbol: string
  start: string
  end: string
  trading_days: number
  counts: ScanCounts
  timeline: ScanDay[]
  pass_dates_by_gate: Record<string, string[]>
  full_passes: ScanFullPass[]
  vol_ratio_series: Array<{
    date: string
    ratio_5_50: number | null
    vd_passed: boolean | null
  }>
  assumptions: BacktestAssumptions
  error?: string
}

// Mode C universe-scope: historical picks from the strategy over a range.
export interface StrategyPlan {
  t1_expected_days: number
  t2_expected_days: number
  t1_target_pct: number
  t2_target_pct: number
  stop_pct: number
  setup_strength: 'tight' | 'normal' | 'loose'
  rationale: string
}

export interface SetupWindows {
  lt_lookback_start: string | null
  base_start: string | null
  base_days: number
  dryup_start: string | null
  trigger_date: string
}

export interface PickMisses {
  available: boolean
  hit_t1?: boolean
  hit_t2?: boolean
  hit_stop?: boolean
  stopped_before_t1?: boolean
  mfe_pct?: number
  mae_pct?: number
  t1_delta_days?: number
  t2_delta_days?: number
}

export interface PriceBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
  ma50?: number | null
  ma150?: number | null
  ma200?: number | null
  atr14_pct?: number | null
  adv5?: number | null
  adv50?: number | null
  vol_ratio_5_50?: number | null
  obv?: number | null
  obv_30d_slope_pct?: number | null
  pct_above_ma150?: number | null
  ret_5d_pct?: number | null
  ret_30d_pct?: number | null
  rolling_high_20d?: number | null
  up_down?: number
}

export interface UniversePick {
  as_of: string
  entry_date: string | null
  exit_date: string | null
  rank: number | null
  symbol: string
  company: string | null
  sector: string | null
  confirmation_score: number
  confirmation_components?: Record<string, unknown>
  bonuses_fired: string[]
  headline: string | null
  entry_px: number | null
  stop_px: number | null
  target_px: number | null
  plan?: StrategyPlan
  windows?: SetupWindows
  forward: ForwardWalk | null
  gate_inputs?: Record<string, Record<string, unknown>>
  misses?: PickMisses
  price_history?: PriceBar[]
}

export interface UniverseSummary {
  trading_days: number
  regime_halt_days: number
  active_days: number
  days_with_picks: number
  total_picks: number
  unique_symbols_picked: number
  hit_rate_pct: number | null
  avg_return_pct: number | null
  sum_return_pct: number | null
}

export interface UniverseBucket {
  key?: string
  symbol?: string
  company?: string | null
  n: number
  avg_return_pct: number | null
  hit_rate_pct: number | null
}

export interface UniverseScanResponse {
  mode: 'C'
  scope: 'universe'
  symbol: null
  start: string
  end: string
  universe_size: number
  picks: UniversePick[]
  summary: UniverseSummary
  by_symbol: UniverseBucket[]
  by_quarter: UniverseBucket[]
  by_month: UniverseBucket[]
  funnel?: BacktestFunnelRow[]
  assumptions: BacktestAssumptions
  error?: string
}

export interface BacktestResponse {
  mode: 'A' | 'B' | 'C'
  as_of?: string
  regime?: Regime
  assumptions: BacktestAssumptions
  // Mode A/B fields:
  symbols?: BacktestSymbolBlock[]
  funnel?: BacktestFunnelRow[]
  selected?: BacktestSelected[]
  summary?: BacktestSummary | UniverseSummary
  // Mode C fields (single-symbol scope):
  scope?: 'symbol' | 'universe'
  symbol?: string | null
  start?: string
  end?: string
  trading_days?: number
  counts?: ScanCounts
  timeline?: ScanDay[]
  pass_dates_by_gate?: Record<string, string[]>
  full_passes?: ScanFullPass[]
  vol_ratio_series?: Array<{
    date: string
    ratio_5_50: number | null
    vd_passed: boolean | null
  }>
  // Mode C fields (universe scope):
  universe_size?: number
  picks?: UniversePick[]
  by_symbol?: UniverseBucket[]
  by_quarter?: UniverseBucket[]
  by_month?: UniverseBucket[]
  // Re-using `summary` for universe scan too (different shape; component picks)
  error?: string
  unresolved?: string[]
}

export interface BacktestRequest {
  as_of: string
  end?: string
  symbols?: string[]
  hold_days?: number
  top_n?: number
  capital?: number
  overrides?: BacktestOverrides
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
  volume_event?: VolumeEvent | null

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
