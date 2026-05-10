"""Data-layer types returned by backend/analysis.py.

These are Pydantic models so they JSON-serialize cleanly when written to disk
by the nightly orchestrator. The middleware re-imports the same types via
`from backend.schemas import ...` and re-exposes them as part of its own API
contract (alongside additional middleware-only types like `Pick`).
"""

from typing import Literal, Optional
from pydantic import BaseModel


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
