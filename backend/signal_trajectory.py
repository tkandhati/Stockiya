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

from .indicators import (
    adv,
    obv,
    obv_flow_inflection,
    obv_slope_pct,
    rolling_high,
    sma_slope_pct,
    up_down_vol_ratio,
    volume_spike_event,
)

SignalState = Literal["strong", "stable", "weakening", "flipped", "unknown"]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"


# Thresholds — tunable. Each is multiplicative on the entry-time value.
_STRONG_RATIO = 1.20        # current >= entry * 1.20 -> strong
_WEAKEN_RATIO = 0.50        # current <  entry * 0.50 -> weakening

# --------------------------------------------------------------------------- #
# Divergent-entry ("Pocket-Pivot / No-Supply-Test") exit rules — B1 override.
#
# Picks whose entry-time VD stage classified the tape as `obv_flow_inflection
# == "healing"` cannot use the standard "OBV rolls over -> exit" rule: their
# 30d OBV was already negative at entry. The healing-velocity override gives
# such picks a bounded grace window during which we only exit if the 10d/30d
# inflection actually deteriorates to "hemorrhaging" (short window rolls
# negative too, confirming the healing thesis failed).
#
# Fix points:
#     HEALING_GRACE_TRADING_DAYS  : sessions of grace before falling back to
#                                   the standard B1 rule (default 10)
# --------------------------------------------------------------------------- #

HEALING_GRACE_TRADING_DAYS: int = 10

# --------------------------------------------------------------------------- #
# Failed-breakout micro-stop (B1.5).
#
# A breakout that clears 20d resistance on 1.3x+ ADV50 and then closes back
# BELOW that same resistance within a handful of sessions — on volume that is
# itself heavier than average — is the institutional-distribution-into-FOMO
# pattern. Waiting for the -8% B2 stop gives back too much. This micro-stop
# fires strictly earlier than B2, only for BR-triggered picks, and only
# inside the small window where a genuine breakout must hold its level.
#
# Fix points:
#     FAILED_BR_WINDOW_TRADING_DAYS : session count from entry inside which
#                                     the rule is armed (default 5)
#     FAILED_BR_VOLUME_MULT         : today's volume / adv50 threshold for
#                                     "heavy" selling (default 1.0)
# --------------------------------------------------------------------------- #

FAILED_BR_WINDOW_TRADING_DAYS: int = 5
FAILED_BR_VOLUME_MULT: float = 1.0


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
    return _load_stage_features(symbol, entry_date_iso, "LT")


def _load_stage_features(symbol: str, entry_date_iso: str, stage_id: str) -> dict:
    """Pull one stage's features dict from the entry-day trace."""
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
            if row.get("stage") == stage_id and row.get("features"):
                return row["features"]
    return {}


# --------------------------------------------------------------------------- #
# Recomputing today's values from current OHLCV
# --------------------------------------------------------------------------- #

def _current_features(symbol: str) -> dict:
    """Recompute the LT-gate indicators from today's OHLCV."""
    from .yahoo import history_ohlcv

    df = history_ohlcv(symbol)
    if df is None or df.empty or len(df) < 200:
        return {}
    return _current_features_from_df(df)


def _current_features_from_df(df) -> dict:
    """Pure helper: same as _current_features but takes an OHLCV DataFrame.

    Split out so tests can build a synthetic df without a live fetch.
    """
    close = df["Close"]
    volume = df["Volume"]
    obv_series = obv(close, volume)
    event = volume_spike_event(df)
    inflection, s_short, s_long = obv_flow_inflection(close, volume)
    # Today's tape: close, volume, and the trailing 20d high excluding today
    # — needed by the failed-breakout micro-stop.
    last_close = float(close.iloc[-1]) if len(close) else None
    last_volume = float(volume.iloc[-1]) if len(volume) else None
    trailing_20d_high = rolling_high(df["High"], 20, exclude_today=True)
    adv50 = adv(volume, 50)
    return {
        "obv_90d_slope_pct": obv_slope_pct(obv_series, 90),
        "up_down_vol_ratio_90d": up_down_vol_ratio(close, volume, 90),
        "ma150_slope_pct": sma_slope_pct(close, 150, 50),
        "volume_event_kind": event.kind,
        "volume_event_direction": event.direction,
        "volume_event_score": event.score,
        "volume_event_label": event.label,
        "volume_event_detail": event.detail,
        "obv_flow_inflection": inflection,
        "obv_slope_short_pct": s_short,
        "obv_slope_long_pct": s_long,
        "last_close": last_close,
        "last_volume": last_volume,
        "trailing_20d_high": trailing_20d_high,
        "adv_50d": adv50,
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


def _classify_healing_flip(
    entry_inflection: Optional[str],
    current_inflection: Optional[str],
    trading_days_since_entry: int,
) -> SignalState:
    """Healing-velocity override for divergent (pre-breakout) entries.

    Semantics:
      - Entry inflection was "healing" (10d up while 30d weak) → we knew the
        30d flow was already negative when we bought. We CAN'T then use the
        standard rule "30d rolls over -> exit" because it never rolled up.
      - Within HEALING_GRACE_TRADING_DAYS sessions:
            current == "hemorrhaging" -> flipped (10d rolled negative;
                                                  healing thesis broken)
            current == "healing"      -> strong  (thesis intact and firming)
            current == "neutral"      -> stable  (mixed; hold)
      - Past grace window: any state that isn't clearly positive is treated
        as weakening — the coiled spring needed to release by now.

    For entries where inflection was NOT healing (i.e. "neutral" or absent),
    this classifier returns "unknown" and the standard indicators own the
    decision (no override).
    """
    if entry_inflection != "healing":
        return "unknown"
    if current_inflection == "hemorrhaging":
        return "flipped"
    if trading_days_since_entry > HEALING_GRACE_TRADING_DAYS:
        # Grace expired: standard B1 falls back into effect via other
        # indicators; here we downgrade to "weakening" so the pick loses its
        # divergent-entry benefit-of-the-doubt.
        if current_inflection == "healing":
            return "stable"
        return "weakening"
    if current_inflection == "healing":
        return "strong"
    if current_inflection == "neutral":
        return "stable"
    return "unknown"


def _classify_failed_breakout(
    *,
    resistance_20d_at_entry: Optional[float],
    current_close: Optional[float],
    current_volume: Optional[float],
    current_adv50: Optional[float],
    trading_days_since_entry: int,
) -> SignalState:
    """B1.5 failed-breakout micro-stop for BR-triggered picks.

    Fires "flipped" iff:
      - We are still within FAILED_BR_WINDOW_TRADING_DAYS sessions of entry;
      - The 20d resistance level captured at entry is known;
      - Today's close is BELOW that resistance;
      - AND today's volume >= FAILED_BR_VOLUME_MULT × ADV50 (heavy selling).

    Returns "unknown" outside the window or when inputs are missing — leaves
    the decision to the standard indicators + fixed -8% B2 stop.
    """
    if resistance_20d_at_entry is None or resistance_20d_at_entry <= 0:
        return "unknown"
    if current_close is None or current_volume is None or current_adv50 is None:
        return "unknown"
    if current_adv50 <= 0:
        return "unknown"
    if trading_days_since_entry > FAILED_BR_WINDOW_TRADING_DAYS:
        return "unknown"
    if trading_days_since_entry < 0:
        return "unknown"
    if current_close < resistance_20d_at_entry and (
        current_volume >= FAILED_BR_VOLUME_MULT * current_adv50
    ):
        return "flipped"
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

def compute_trajectory(
    symbol: str,
    entry_date_iso: str,
    *,
    trading_days_since_entry: Optional[int] = None,
) -> TrajectoryReport:
    """Read entry features, recompute current features, classify, aggregate.

    `trading_days_since_entry` is used only by the divergent-entry and
    failed-breakout indicators (both are window-bounded). If None, we assume
    day 0 — new picks — which means both windowed rules are armed.
    """
    entry_lt = _load_entry_features(symbol, entry_date_iso)
    if not entry_lt:
        return TrajectoryReport(
            overall="unknown",
            headline=f"No entry trace found for {symbol} on {entry_date_iso}",
        )
    entry_vd = _load_stage_features(symbol, entry_date_iso, "VD")
    entry_br = _load_stage_features(symbol, entry_date_iso, "BR")

    current = _current_features(symbol)
    if not current:
        return TrajectoryReport(
            overall="unknown",
            headline=f"Could not fetch current OHLCV for {symbol}",
        )

    return _build_report(
        entry_lt=entry_lt,
        entry_vd=entry_vd,
        entry_br=entry_br,
        current=current,
        trading_days_since_entry=trading_days_since_entry or 0,
    )


def _build_report(
    *,
    entry_lt: dict,
    entry_vd: dict,
    entry_br: dict,
    current: dict,
    trading_days_since_entry: int,
) -> TrajectoryReport:
    """Pure classifier — no I/O. Tests can drive this directly."""
    indicators: list[IndicatorDelta] = []

    # 1. OBV-90d slope
    e = entry_lt.get("obv_90d_slope_pct")
    c = current.get("obv_90d_slope_pct")
    state = _classify_positive(e, c)
    indicators.append(IndicatorDelta(
        name="obv_90d_slope_pct",
        label="OBV-90d slope",
        entry_value=e, current_value=c, state=state,
        description=_fmt_pct_delta("OBV-90d", e, c),
    ))

    # 2. Up/down vol ratio 90d
    e = entry_lt.get("up_down_vol_ratio_90d")
    c = current.get("up_down_vol_ratio_90d")
    state = _classify_ratio(e, c)
    indicators.append(IndicatorDelta(
        name="up_down_vol_ratio_90d",
        label="Up/Down vol (90d)",
        entry_value=e, current_value=c, state=state,
        description=_fmt_ratio_delta("Up/Down vol 90d", e, c),
    ))

    # 3. 150d MA slope
    e = entry_lt.get("ma150_slope_pct")
    c = current.get("ma150_slope_pct")
    state = _classify_positive(e, c)
    indicators.append(IndicatorDelta(
        name="ma150_slope_pct",
        label="150d MA slope",
        entry_value=e, current_value=c, state=state,
        description=_fmt_pct_delta("150d MA slope", e, c),
    ))

    # 4. Fast distribution/climax warning from the latest volume spike.
    # This is an early exit layer: it does not wait for 90-day indicators to
    # fully roll over when today's tape shows heavy selling pressure.
    event_direction = current.get("volume_event_direction")
    event_kind = current.get("volume_event_kind")
    event_score = current.get("volume_event_score")
    if event_direction == "bearish" and event_kind in ("bearish_distribution", "climax_warning"):
        indicators.append(IndicatorDelta(
            name="volume_spike_event",
            label="Latest volume event",
            entry_value=None,
            current_value=event_score,
            state="flipped",
            description=current.get("volume_event_detail") or "Bearish volume event.",
        ))

    # 5. Healing-velocity override for divergent (pre-breakout) entries.
    # Only meaningful if entry-time VD stage classified the tape as "healing";
    # otherwise the classifier returns "unknown" and the standard rules run.
    entry_infl = entry_vd.get("obv_flow_inflection")
    current_infl = current.get("obv_flow_inflection")
    if entry_infl == "healing":
        state = _classify_healing_flip(
            entry_infl, current_infl, trading_days_since_entry,
        )
        indicators.append(IndicatorDelta(
            name="obv_flow_inflection",
            label="OBV flow inflection (divergent entry)",
            entry_value=None,
            current_value=None,
            state=state,
            description=_fmt_inflection_delta(
                entry_infl, current_infl, trading_days_since_entry,
            ),
        ))

    # 6. Failed-breakout micro-stop (B1.5) for BR-triggered picks.
    # Only armed for the first FAILED_BR_WINDOW_TRADING_DAYS sessions and
    # only when the entry trace recorded a 20d resistance level.
    resistance = entry_br.get("resistance_20d")
    state = _classify_failed_breakout(
        resistance_20d_at_entry=resistance,
        current_close=current.get("last_close"),
        current_volume=current.get("last_volume"),
        current_adv50=current.get("adv_50d"),
        trading_days_since_entry=trading_days_since_entry,
    )
    if state != "unknown":
        indicators.append(IndicatorDelta(
            name="failed_breakout_micro_stop",
            label="Failed-breakout micro-stop (B1.5)",
            entry_value=resistance,
            current_value=current.get("last_close"),
            state=state,
            description=_fmt_failed_breakout(
                resistance,
                current.get("last_close"),
                current.get("last_volume"),
                current.get("adv_50d"),
                trading_days_since_entry,
                state,
            ),
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


def _fmt_inflection_delta(
    entry_inflection: Optional[str],
    current_inflection: Optional[str],
    days: int,
) -> str:
    """Human-readable description of the healing-velocity indicator."""
    if current_inflection == "hemorrhaging":
        return (
            f"Divergent entry: 10d OBV rolled negative on day {days} — "
            "healing thesis failed. Exit at next open."
        )
    if current_inflection == "healing":
        return f"Divergent entry: healing intact on day {days} (grace {HEALING_GRACE_TRADING_DAYS}d)."
    if current_inflection == "neutral":
        return f"Divergent entry: flow neutral on day {days} — hold within grace window."
    if days > HEALING_GRACE_TRADING_DAYS:
        return (
            f"Divergent entry: {HEALING_GRACE_TRADING_DAYS}d grace expired without "
            "confirmation — fall back to standard exit rules."
        )
    return f"Divergent entry: inflection unavailable on day {days}."


def _fmt_failed_breakout(
    resistance: Optional[float],
    close: Optional[float],
    volume: Optional[float],
    adv50: Optional[float],
    days: int,
    state: SignalState,
) -> str:
    """Human-readable description of the B1.5 micro-stop indicator."""
    if state == "flipped" and resistance and close and volume and adv50:
        return (
            f"Failed breakout on day {days}: close {close:.2f} < 20d high "
            f"{resistance:.2f} on {volume/adv50:.2f}x ADV50 "
            f"(>= {FAILED_BR_VOLUME_MULT:.2f}x). Exit at next open."
        )
    if resistance and close:
        return (
            f"Breakout holding above 20d high {resistance:.2f} "
            f"(close {close:.2f}) — micro-stop armed through day "
            f"{FAILED_BR_WINDOW_TRADING_DAYS}."
        )
    return "Failed-breakout micro-stop: inputs unavailable."
