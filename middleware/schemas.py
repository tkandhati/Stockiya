"""API DTOs (Pydantic models) used by the middleware HTTP layer.

Volume-only schema — no peers, no valuation, no headwind text.
"""

from typing import Literal, Optional
from pydantic import BaseModel


class TargetWindowDTO(BaseModel):
    center_months: float
    tolerance_months: float
    label: str
    rationale: str


class ReasoningPointDTO(BaseModel):
    """One auditable line under a pick — the user can verify this independently."""
    label: str
    value: str
    state: Literal["bullish", "neutral", "bearish"]
    why: str
    verify: str


class Pick(BaseModel):
    symbol: str
    company: str
    current: float
    best_buy_at: float
    sell_target: float
    stop_loss: float
    headline: str = ""
    rationale: str
    risk_headline: str = ""
    risks: str
    confidence: Literal["low", "medium", "high"] = "medium"
    upside_pct: float
    downside_pct: float
    entry_timing: Literal["early", "mid", "late", "missed", "unknown"] = "unknown"
    wyckoff_phase: Literal[
        "accumulation", "markup", "distribution", "markdown", "indeterminate"
    ] = "indeterminate"
    weinstein_stage: Literal[
        "stage_1_base", "stage_1_to_2", "stage_2_advance",
        "stage_3_top", "stage_4_decline", "undefined",
    ] = "undefined"
    target_window: TargetWindowDTO
    reasoning: list[ReasoningPointDTO] = []
    composite_score: float = 0.0
    rank: Optional[int] = None


class PicksResponse(BaseModel):
    date: str
    generated_at: str
    source: Literal["pipeline"] = "pipeline"
    demo_mode: bool = False
    picks: list[Pick]


class StrategySignalDTO(BaseModel):
    name: str
    state: Literal["bullish", "neutral", "bearish"]
    value: Optional[float] = None
    label: str
    description: str


class AccumulationDTO(BaseModel):
    """Volume-strategy panel — the heart of the stock-detail view."""
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

    # Block + Bulk deals (NSE institutional trade records)
    block_deal_buy_count_30d: int = 0
    block_deal_sell_count_30d: int = 0
    block_deal_net_qty_ratio: float = 0.0

    signals: list[StrategySignalDTO] = []


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
