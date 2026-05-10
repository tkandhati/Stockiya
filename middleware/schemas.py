"""API DTOs (Pydantic models) used by the middleware HTTP layer.

These are the contracts between middleware and frontend. They mirror the
backend-layer dataclasses (e.g. AccumulationSignals) but are versioned
independently because the API may evolve at a different pace than the
internal compute structures.
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
    stop_loss: float  # 10% below best_buy_at, computed
    headline: str = ""        # one-line thesis (≤120 chars) — for the card
    rationale: str            # bull case (multi-line) — for the detail page
    risk_headline: str = ""   # one-line bear case (≤120 chars) — for the card
    risks: str                # bear case (multi-line) — for the detail page
    confidence: Literal["low", "medium", "high"] = "medium"
    upside_pct: float
    downside_pct: float  # to stop-loss
    entry_timing: Literal["early", "mid", "late", "missed", "unknown"] = "unknown"
    wyckoff_phase: Literal["accumulation", "markup", "distribution", "markdown", "indeterminate"] = "indeterminate"
    weinstein_stage: Literal[
        "stage_1_base", "stage_1_to_2", "stage_2_advance",
        "stage_3_top", "stage_4_decline", "undefined",
    ] = "undefined"
    target_window: TargetWindowDTO
    reasoning: list[ReasoningPointDTO] = []


class PicksResponse(BaseModel):
    date: str
    generated_at: str
    source: Literal["llm", "fallback"]
    demo_mode: bool = False  # True = bundled fixtures (not real market data)
    picks: list[Pick]


class Peer(BaseModel):
    symbol: str
    company: str
    current: Optional[float] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    market_cap_cr: Optional[float] = None
    return_1y_pct: Optional[float] = None
    is_target: bool = False


class ValuationSignals(BaseModel):
    pe_vs_sector: Optional[Literal["cheap", "fair", "expensive"]] = None
    sector_pe_median: Optional[float] = None
    price_vs_200dma_pct: Optional[float] = None
    price_in_52w_band_pct: Optional[float] = None


class VolumeSignals(BaseModel):
    today: Optional[float] = None
    avg_30d: Optional[float] = None
    ratio: Optional[float] = None
    label: Literal["surge", "normal", "weak", "unknown"] = "unknown"


class StrategySignalDTO(BaseModel):
    name: str
    state: Literal["bullish", "neutral", "bearish"]
    value: Optional[float] = None
    label: str
    description: str


class AccumulationDTO(BaseModel):
    """Volume-strategy panel — the heart of the analysis."""
    days_used: int
    verdict: Literal["accumulating", "neutral", "distributing", "unknown"]
    wyckoff_phase: Literal["accumulation", "markup", "distribution", "markdown", "indeterminate"]
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

    # Block + Bulk deal activity (NSE institutional trade records)
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
    pe: Optional[float] = None
    pb: Optional[float] = None
    market_cap_cr: Optional[float] = None
    dividend_yield_pct: Optional[float] = None
    roe_pct: Optional[float] = None
    debt_to_equity: Optional[float] = None
    fifty_two_w_high: Optional[float] = None
    fifty_two_w_low: Optional[float] = None
    ma200: Optional[float] = None
    return_3m_pct: Optional[float] = None
    return_1y_pct: Optional[float] = None
    valuation: ValuationSignals
    volume: VolumeSignals
    accumulation: AccumulationDTO
    peers: list[Peer]
    history_6m: list[dict]
    pick_today: Optional[Pick] = None
    headwind: Optional[str] = None
    demo_mode: bool = False
