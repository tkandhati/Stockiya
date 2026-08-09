"""Response models for the price-trend feature."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PriceTrendStatus = Literal["ready", "forming", "watch"]


class PricePoint(BaseModel):
    date: str
    close: float


class PriceTrendCandidate(BaseModel):
    rank: int = 0
    symbol: str
    company: str
    status: PriceTrendStatus
    score: float = Field(ge=0, le=100)
    as_of: str
    close: float
    breakout_price: float
    distance_to_breakout_pct: float
    support_price: float
    ma20: float
    ma50: float
    ma150: float
    ma50_slope_10d_pct: float
    range_10d_pct: float
    atr_14d_pct: float
    higher_low_pct: float
    return_20d_pct: float
    reasons: list[str]
    watchouts: list[str]
    price_history: list[PricePoint]


class PriceTrendResponse(BaseModel):
    generated_at: str
    as_of: str | None
    universe: str
    scan_limit: int
    scanned_count: int
    eligible_count: int
    skipped_count: int
    demo_mode: bool
    methodology: str = "price_only"
    candidates: list[PriceTrendCandidate]


class PriceTrendLookupResponse(BaseModel):
    requested_symbol: str
    resolved_symbol: str | None
    price_history_available: bool
    matches_strategy: bool
    message: str
    candidate: PriceTrendCandidate | None
