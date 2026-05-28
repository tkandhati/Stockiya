"""Signal trajectory — has the institutional setup gotten stronger or weaker
since we entered the position?

For each open position we re-compute the same indicators that fired the
gates at entry, compare them to the entry-time values stored in the trace
JSONL, and classify each indicator into one of four states:

    strong       improved meaningfully (>= 20 % above entry)
    stable       roughly unchanged (within +/- 50 % of entry)
    weakening    eroded toward zero (< 50 % of entry, still positive)
    flipped      crossed zero (negative now -- exit trigger)

The aggregate trajectory is worst-of: if ANY indicator flipped, the whole
position is DISTRIBUTION_FLIP and the action recommendation in positions_view
escalates to exit-at-next-open.

This operationalizes PRINCIPLES Section 4:
    "Exit immediately if any volume signal inverts."

The system already had this rule in the doc; this module is the rule
running in code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from .indicators import obv, obv_slope_pct, sma_slope_pct, up_down_vol_ratio
from .yahoo import history_ohlcv

SignalState = Literal["strong", "stable", "weakening", "flipped", "unknown"]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"


# Thresholds — tunable. Each is multiplicative on the entry-time value.
_STRONG_RATIO = 1.20        # current >= entry * 1.20 -> strong
_WEAKEN_RATIO = 0.50        # current <  entry * 0.50 -> weakening


# Order matters: worst state wins in aggregation.
_STATE_RANK = {
    "flipped":   0,  # worst -- triggers exit
    "weakening": 1,
    "unknown":   2,
    "stable":    3,
    "strong":    4,
}


@dataclass
class IndicatorDelta:
    name: str
    label: str                          # human-readable name
    entry_value: Optional[float]
    current_value: Optional[float]
    state: SignalState
    description: str                    # one-line for the UI


@dataclass
class TrajectoryReport:
    overall: SignalState
    indicators: list[IndicatorDelta] = field(default_factory=list)
    headline: str = ""
    exit_recommendation: bool = False   # True iff any indicator flipped

    def as_dict(self) -> dict:
        return {
            "overall": self.overall,
            "indicators": [
                {
                    "name": i.name,
                    "label": i.label,
                    "entry_value": i.entry_value,
                    "current_value": i.current_value,
                    "state": i.state,
                    "description": i.description,
                }
                for i in self.indicators
            ],
            "headline": self.headline,
            "exit_recommendation": self.exit_recommendation,
        }


# --------------------------------------------------------------------------- #
# Reading entry-time features from the trace JSONL
# --------------------------------------------------------------------------- #

def _load_entry_features(symbol: str, entry_date_iso: str) -> dict:
    """Pull the LT-stage features dict written at entry."""
    safe = symbol.replace("/", "_").replace(":", "_")
    p = _TRACES_DIR / f"run_{entry_date_iso}_{safe}.jsonl"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("stage") == "LT" and row.get("features"):
                return row["features"]
    return {}


# --------------------------------------------------------------------------- #
# Recomputing today's values from current OHLCV
# --------------------------------------------------------------------------- #

def _current_features(symbol: str) -> dict:
    """Recompute the LT-gate indicators from today's OHLCV."""
    df = history_ohlcv(symbol)
    if df is None or df.empty or len(df) < 200:
        return {}
    close = df["Close"]
    volume = df["Volume"]
    obv_series = obv(close, volume)
    return {
        "obv_90d_slope_pct": obv_slope_pct(obv_series, 90),
        "up_down_vol_ratio_90d": up_down_vol_ratio(close, volume, 90),
        "ma150_slope_pct": sma_slope_pct(close, 150, 50),
    }


# --------------------------------------------------------------------------- #
# Classifier
# --------------------------------------------------------------------------- #

def _classify_positive(entry: Optional[float], current: Optional[float]) -> SignalState:
    """For 'higher is better' metrics like OBV slope and MA slope."""
    if entry is None or current is None:
        return "unknown"
    if current < 0 < entry or (entry > 0 and current <= 0):
        return "flipped"
    if entry <= 0:
        # Entry was zero or negative; we shouldn't have entered, but classify
        # current relative to zero.
        return "strong" if current > 0 else "stable"
    if current >= entry * _STRONG_RATIO:
        return "strong"
    if current < entry * _WEAKEN_RATIO:
        return "weakening"
    return "stable"


def _classify_ratio(entry: Optional[float], current: Optional[float]) -> SignalState:
    """For ratio metrics where 1.0 is neutral; e.g. up/down vol ratio."""
    if entry is None or current is None:
        return "unknown"
    if current < 1.0 and entry > 1.0:
        return "flipped"
    if entry <= 1.0:
        return "strong" if current > 1.0 else "stable"
    # entry > 1.0; lift above 1 = how much over neutral
    entry_lift = entry - 1.0
    current_lift = current - 1.0
    if current_lift >= entry_lift * _STRONG_RATIO:
        return "strong"
    if current_lift < entry_lift * _WEAKEN_RATIO:
        return "weakening"
    return "stable"


def _aggregate(indicators: list[IndicatorDelta]) -> SignalState:
    """Worst state wins. Unknown only beats nothing -- if everyone is unknown,
    the overall is unknown; if even one indicator is computed, that wins."""
    if not indicators:
        return "unknown"
    states = [i.state for i in indicators]
    # If we have any concrete state, ignore "unknown" in aggregation
    concrete = [s for s in states if s != "unknown"]
    pool = concrete or states
    return min(pool, key=lambda s: _STATE_RANK[s])


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def compute_trajectory(symbol: str, entry_date_iso: str) -> TrajectoryReport:
    """Read entry features, recompute current features, classify, aggregate."""
    entry = _load_entry_features(symbol, entry_date_iso)
    if not entry:
        return TrajectoryReport(
            overall="unknown",
            headline=f"No entry trace found for {symbol} on {entry_date_iso}",
        )

    current = _current_features(symbol)
    if not current:
        return TrajectoryReport(
            overall="unknown",
            headline=f"Could not fetch current OHLCV for {symbol}",
        )

    indicators: list[IndicatorDelta] = []

    # 1. OBV-90d slope
    e = entry.get("obv_90d_slope_pct")
    c = current.get("obv_90d_slope_pct")
    state = _classify_positive(e, c)
    indicators.append(IndicatorDelta(
        name="obv_90d_slope_pct",
        label="OBV-90d slope",
        entry_value=e, current_value=c, state=state,
        description=_fmt_pct_delta("OBV-90d", e, c),
    ))

    # 2. Up/down vol ratio 90d
    e = entry.get("up_down_vol_ratio_90d")
    c = current.get("up_down_vol_ratio_90d")
    state = _classify_ratio(e, c)
    indicators.append(IndicatorDelta(
        name="up_down_vol_ratio_90d",
        label="Up/Down vol (90d)",
        entry_value=e, current_value=c, state=state,
        description=_fmt_ratio_delta("Up/Down vol 90d", e, c),
    ))

    # 3. 150d MA slope
    e = entry.get("ma150_slope_pct")
    c = current.get("ma150_slope_pct")
    state = _classify_positive(e, c)
    indicators.append(IndicatorDelta(
        name="ma150_slope_pct",
        label="150d MA slope",
        entry_value=e, current_value=c, state=state,
        description=_fmt_pct_delta("150d MA slope", e, c),
    ))

    overall = _aggregate(indicators)
    flipped_any = any(i.state == "flipped" for i in indicators)

    # Headline: pick the worst indicator and quote its delta
    worst = min(indicators, key=lambda i: _STATE_RANK[i.state])
    headline = worst.description

    return TrajectoryReport(
        overall=overall,
        indicators=indicators,
        headline=headline,
        exit_recommendation=flipped_any,
    )


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def _fmt_pct_delta(label: str, entry: Optional[float], current: Optional[float]) -> str:
    if entry is None or current is None:
        return f"{label}: unavailable"
    return f"{label}: {current:+.1f}% (was {entry:+.1f}% at entry)"


def _fmt_ratio_delta(label: str, entry: Optional[float], current: Optional[float]) -> str:
    if entry is None or current is None:
        return f"{label}: unavailable"
    return f"{label}: {current:.2f}x (was {entry:.2f}x at entry)"
