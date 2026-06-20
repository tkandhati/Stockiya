"""API DTOs (Pydantic models) used by the middleware HTTP layer.

Gates-based spine (PRINCIPLES Section 2). The Pick shape mirrors
`backend/stages/hypothesis.py:build_pick_payload`.

The stock-detail panel still uses the legacy AccumulationDTO so the rich
Wyckoff/Weinstein view continues to render — that view is independent of
the picker and reads from `backend/signals/__init__.py`.
"""

from typing import Literal, Optional

from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Pick payload — matches backend/stages/hypothesis.py
# --------------------------------------------------------------------------- #

class PricePlanDTO(BaseModel):
    account_value: float
    entry: float
    stop: float
    t1: float
    t2: float
    shares_total: int
    shares_at_t1: int
    shares_at_t2: int
    risk_amount: float
    risk_pct_of_account: float
    notes: list[str] = []


class ExitStepDTO(BaseModel):
    milestone_days: int
    action: str
    trigger: str
    new_stop: Optional[float] = None
    note: str = ""


class ExitScheduleDTO(BaseModel):
    day_45: ExitStepDTO
    day_90: ExitStepDTO
    day_180: ExitStepDTO


class ConfirmationDTO(BaseModel):
    score: float
    gate_margin_sum: Optional[float] = None
    bonus_count: Optional[int] = None
    bonus_weight: Optional[float] = None
    bonuses_fired: list[str] = []


class GatesEvidenceDTO(BaseModel):
    CS: list[str] = []
    VD: list[str] = []
    BR: list[str] = []


class VolumeEventDTO(BaseModel):
    kind: str = "neutral"
    direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    score: float = 0.0
    label: str = ""
    detail: str = ""
    is_spike: bool = False
    vol_ratio_50: Optional[float] = None
    quiet_ratio_5_50: Optional[float] = None
    close_location: Optional[float] = None
    price_change_pct: Optional[float] = None
    break_pct: Optional[float] = None
    breakdown_pct: Optional[float] = None
    close_vs_ma50_pct: Optional[float] = None
    ret_30d_pct: Optional[float] = None
    obv_20d_slope_pct: Optional[float] = None
    base_days: int = 0


class Pick(BaseModel):
    symbol: str
    rank: Optional[int] = None
    trace_id: Optional[str] = None
    company: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    current_price: Optional[float] = None
    headline: str = ""
    confirmation: ConfirmationDTO
    price_plan: PricePlanDTO
    exit_schedule: ExitScheduleDTO
    distribution_flip_exit: str = ""
    gates_evidence: GatesEvidenceDTO
    volume_event: Optional[VolumeEventDTO] = None

    # Legacy aliases — populated by build_pick_payload for transition-period
    # frontends that still expect the old field names.
    best_buy_at: Optional[float] = None
    sell_target: Optional[float] = None
    stop_loss: Optional[float] = None
    upside_pct: Optional[float] = None
    downside_pct: Optional[float] = None
    shares_to_buy: Optional[int] = None


# --------------------------------------------------------------------------- #
# Regime + envelope
# --------------------------------------------------------------------------- #

class RegimeCheckDTO(BaseModel):
    symbol: str
    close: Optional[float] = None
    ma50: Optional[float] = None
    gap_pct: Optional[float] = None
    passed: bool
    reason: str


class RegimeDTO(BaseModel):
    passed: bool
    summary: str
    checks: list[RegimeCheckDTO] = []


class NearMissGate(BaseModel):
    stage_id: str
    label: str
    evidence: list[str] = []
    reason: Optional[str] = None


class NearMiss(BaseModel):
    symbol: str
    company: str
    passed_count: int
    passed_gates: list[NearMissGate] = []
    failed_gate: NearMissGate


class EarlyVolumeSignal(BaseModel):
    symbol: str
    company: str
    direction: Literal["bullish", "bearish", "neutral"]
    kind: str
    score: float
    label: str
    detail: str
    event: VolumeEventDTO
    stage_reached: Optional[str] = None
    failed_gate: Optional[NearMissGate] = None
    selected: bool = False


class BRSubCheckDTO(BaseModel):
    name: str
    label: str
    current: Optional[float] = None
    threshold: Optional[float] = None
    passed: bool
    gap_pct: Optional[float] = None
    gap_detail: str = ""


class ReadyToBreak(BaseModel):
    symbol: str
    company: str
    lt_score: float = 0.0
    cs_score: float = 0.0
    vd_score: float = 0.0
    setup_strength: float = 0.0
    br_checks: list[BRSubCheckDTO] = []
    br_passing: int = 0
    br_total: int = 0
    br_reason: str = ""
    closeness_score: float = 0.0
    last_close: Optional[float] = None


class PicksResponse(BaseModel):
    date: str
    generated_at: str
    source: Literal["pipeline"] = "pipeline"
    demo_mode: bool = False
    regime: Optional[RegimeDTO] = None
    message: Optional[str] = None
    picks: list[Pick] = []
    near_misses: list[NearMiss] = []
    early_signals: list[EarlyVolumeSignal] = []
    ready_to_break: list[ReadyToBreak] = []


# --------------------------------------------------------------------------- #
# Stock-detail panel — still renders the legacy rich-volume narrative.
# Untouched by the gates-based rebuild.
# --------------------------------------------------------------------------- #

class StrategySignalDTO(BaseModel):
    name: str
    state: Literal["bullish", "neutral", "bearish"]
    value: Optional[float] = None
    label: str
    description: str


class AccumulationDTO(BaseModel):
    days_used: int
    verdict: Literal["accumulating", "neutral", "distributing", "unknown"]
    wyckoff_phase: Literal[
        "accumulation", "markup", "distribution", "markdown", "indeterminate"
    ]
    entry_timing: Literal["early", "mid", "late", "missed", "unknown"]
    entry_timing_note: str
    accum_score: float
    one_liner: str

    # Medium-term
    vol_recent_10d: Optional[float] = None
    vol_avg_30d: Optional[float] = None
    vol_avg_90d: Optional[float] = None
    vol_trend_pct: Optional[float] = None
    up_down_vol_ratio: Optional[float] = None
    obv_slope_pct: Optional[float] = None
    ad_line_slope_pct: Optional[float] = None
    cmf_21d: Optional[float] = None
    mfi_14d: Optional[float] = None
    price_tightness_pct: Optional[float] = None
    price_change_30d_pct: Optional[float] = None
    vwap_60d: Optional[float] = None
    price_vs_vwap_pct: Optional[float] = None

    # Long-term
    weinstein_stage: Literal[
        "stage_1_base", "stage_1_to_2", "stage_2_advance",
        "stage_3_top", "stage_4_decline", "undefined",
    ] = "undefined"
    weinstein_note: str = ""
    ma_30w: Optional[float] = None
    ma_30w_slope_pct: Optional[float] = None
    ma_50d: Optional[float] = None
    ma_150d: Optional[float] = None
    ma_200d: Optional[float] = None
    minervini_template: bool = False
    obv_slope_90d_pct: Optional[float] = None
    obv_slope_180d_pct: Optional[float] = None
    cmf_60d: Optional[float] = None
    up_down_vol_ratio_90d: Optional[float] = None
    base_length_days: int = 0
    vol_qoq_growth_pct: Optional[float] = None
    price_change_180d_pct: Optional[float] = None

    # Patterns
    pocket_pivot_count_30d: int = 0
    volume_dry_up: bool = False
    canslim_breakout: bool = False
    volume_event: Optional[VolumeEventDTO] = None

    # Block + Bulk deals (NSE institutional trade records)
    block_deal_buy_count_30d: int = 0
    block_deal_sell_count_30d: int = 0
    block_deal_net_qty_ratio: float = 0.0

    signals: list[StrategySignalDTO] = []


class TimeStopsDTO(BaseModel):
    day_45: str
    day_90: str
    day_180: str


class IndicatorDeltaDTO(BaseModel):
    name: str
    label: str
    entry_value: Optional[float] = None
    current_value: Optional[float] = None
    state: Literal["strong", "stable", "weakening", "flipped", "unknown"]
    description: str = ""


class TrajectoryDTO(BaseModel):
    overall: Literal["strong", "stable", "weakening", "flipped", "unknown"]
    indicators: list[IndicatorDeltaDTO] = []
    headline: str = ""
    exit_recommendation: bool = False


class Position(BaseModel):
    pick_id: str
    trace_id: str
    symbol: str
    company: str
    entry_date: str
    days_held: int
    entry_price: float
    stop_price: float
    t1_price: float
    t2_price: float
    current_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    status: str
    hit_t1: bool
    hit_t1_date: str = ""
    shares_total: int
    shares_at_t1: int
    shares_at_t2: int
    confirmation_score: float = 0.0
    headline: str = ""
    action: str
    action_note: str
    new_stop: Optional[float] = None
    time_stops: TimeStopsDTO
    # Q1 -- expected break-even / T1 day
    expected_t1_date: Optional[str] = None
    expected_t1_trading_days: Optional[int] = None
    t1_status: Optional[Literal["on_track", "overdue", "hit"]] = None
    days_to_expected_t1: Optional[int] = None
    # Q2 -- signal trajectory since entry
    trajectory: Optional[TrajectoryDTO] = None


class PositionsResponse(BaseModel):
    date_ist: str
    count: int
    positions: list[Position] = []


class StockDetail(BaseModel):
    symbol: str
    company: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    current: Optional[float] = None
    day_change_pct: Optional[float] = None
    fifty_two_w_high: Optional[float] = None
    fifty_two_w_low: Optional[float] = None
    ma200: Optional[float] = None
    return_3m_pct: Optional[float] = None
    return_1y_pct: Optional[float] = None
    accumulation: AccumulationDTO
    history_6m: list[dict]
    pick_today: Optional[Pick] = None
    demo_mode: bool = False
