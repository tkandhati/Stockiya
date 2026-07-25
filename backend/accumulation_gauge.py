"""5-color accumulation-strength gauge — advisory, read-only annotation.

WHAT THIS IS
------------
A single 1..5 "how strong is the accumulation, and how much adversity can it
absorb" gauge, rendered as a low->high horizontal bar on each position:

    1  RED      FLIPPED   #ef4444   already risky / at stop
    2  ORANGE   WARNING   #f97316   weakening — decide now
    3  YELLOW    CAUTION   #f59e0b   marginal — watch it
    4  GREEN    HEALTHY   #10b981   thesis intact — normal hold
    5  DK GREEN STRONG    #059669   strong accumulation — can absorb adversity

PURELY ADDITIVE. This module changes NO selection / sizing / exit decision.
It reads signals that `positions_view` / the daily trace already compute and
maps them to a colour + an adversity buffer. It is a PURE function set with
NO I/O — the caller supplies every input (so it is safe to import from the
backend, the tuner, or a test without touching disk or the network).

TWO FEEDERS, ONE RENDERER
-------------------------
* gauge_from_position(pos)          — LIVE (today's card). Colour spine is the
    already-computed trajectory.overall ladder (strong/stable/unknown/
    weakening/flipped); action_label / trajectory_flip / close<=stop only
    ESCALATE toward red or CONFIRM green — they never set a colour alone.
* gauge_from_trace_features(feat…)  — HISTORICAL (pick-a-date replay). There is
    no live trajectory for an arbitrary past day, so the colour comes from a
    deterministic 0-100 score over the validated per-date trace features.

Both return the SAME dict contract so one UI component renders either.

ADVERSITY BUFFER (dynamic, per stock)
-------------------------------------
"Strength it can face" is computed per stock, not a global setting:

    buffer_sessions ~= headroom_to_stop_% / atr_pct

where atr_pct is the stock's own ATR(14)/close (from the [CS] trace stage) and
headroom is (close - stop)/close. A high-volatility name burns its buffer
faster; the buffer grows as price climbs away from the stop. The buffer is then
bucketed into the user's review cadence (REVIEWS_PER_DAY) — the user checks
twice a day (afternoon + night), so a "session" ~= two checks. The bucket TEXT
is advisory phrasing, not a market prediction (see BUFFER_TEXT).

Fix points (tune here):
    LEVELS               — colour + label per level
    OVERALL_TO_LEVEL     — trajectory.overall -> base level (live spine)
    SCORE_WEIGHTS        — historical 0-100 component weights + thresholds
    SCORE_BANDS          — historical score -> level bands
    BUFFER_SESSION_BANDS — buffer sessions -> bucket
    BUFFER_TEXT          — bucket -> cadence-aware advisory text
    REVIEWS_PER_DAY      — how many times the user reviews (afternoon + night)
"""
from __future__ import annotations

from typing import Optional

# --------------------------------------------------------------------------- #
# Level vocabulary. 1 = weakest (red), 5 = strongest (dark green). The bar is
# rendered low->high (left->right) so index order here IS the bar order.
# --------------------------------------------------------------------------- #
LEVELS: dict[int, dict[str, str]] = {
    1: {"color": "#ef4444", "label": "FLIPPED"},   # red
    2: {"color": "#f97316", "label": "WARNING"},   # orange
    3: {"color": "#f59e0b", "label": "CAUTION"},   # yellow
    4: {"color": "#10b981", "label": "HEALTHY"},   # light green
    5: {"color": "#059669", "label": "STRONG"},    # dark green
}

# Live spine: trajectory.overall -> base level. Everything else only nudges.
OVERALL_TO_LEVEL: dict[str, int] = {
    "strong":    5,
    "stable":    4,
    "unknown":   3,
    "weakening": 2,
    "flipped":   1,
}

# The user reviews twice a day (afternoon + night). One trading "session" of
# buffer therefore spans ~2 of their checks. Not a controllable per-request
# setting — a single fix-point.
REVIEWS_PER_DAY: int = 2

# Buffer buckets, weakest -> strongest, keyed by sessions-to-stop.
# (lower_inclusive, bucket) — first match from the top wins.
BUFFER_SESSION_BANDS: list[tuple[float, str]] = [
    (4.0, "SKIP_SEVERAL"),
    (2.0, "SKIP_ONE"),
    (1.0, "NEXT_REVIEW"),
    (0.0001, "THIS_REVIEW"),
    (float("-inf"), "ACT_NOW"),
]

# Cadence-aware advisory text. ACT_NOW is cadence-independent; the rest pick a
# variant by REVIEWS_PER_DAY. Advisory phrasing, NOT a measured market claim.
BUFFER_TEXT: dict[str, object] = {
    "ACT_NOW":      "Exit at next open — don't wait for a scheduled check",
    "THIS_REVIEW":  {2: "Act on this check — don't skip the next",
                     1: "Act today — don't wait"},
    "NEXT_REVIEW":  {2: "Reassess at your next check today; decide by close",
                     1: "Decide by tomorrow's close"},
    "SKIP_ONE":     {2: "Hold to your next check — no extra looks",
                     1: "Hold to tomorrow — no extra looks"},
    "SKIP_SEVERAL": {2: "Can skip a check or two — normal hold",
                     1: "Fine to skip a day"},
}

# Per-level one-liner. `{buffer}` is filled with the cadence-aware buffer text.
LEVEL_MESSAGE: dict[int, str] = {
    5: "Strong accumulation — dips likely bought. {buffer}",
    4: "Thesis intact — normal hold. {buffer}",
    3: "Marginal — watch it. {buffer}",
    2: "Weakening — decide now. {buffer}",
    1: "Flipped or at stop — {buffer}",
}

# 9-state action_label escalators (advisory in positions_view). These can only
# pull the level DOWN toward red (confirming weakness the spine may lag) — never
# push it up. EXTEND_5D confirms strength (see _apply_escalators).
_REVIEW_CAP = 2       # REVIEW_WEAKNESS_CONFIRMED caps at WARNING
_MONITOR_CAP = 3      # MONITOR_EARLY_WEAKNESS / MAINTAIN_DRY_UP cap at CAUTION


# --------------------------------------------------------------------------- #
# Historical 0-100 score. Only validated per-date trace fields are used. Each
# component: (feature_key, weight, full_threshold, half_threshold). Missing
# features are dropped and the score renormalizes over present components, so a
# short/older trace still scores honestly instead of collapsing to zero.
# --------------------------------------------------------------------------- #
SCORE_WEIGHTS: list[tuple[str, float, float, float]] = [
    ("obv_90d_slope_pct",     30.0, 3.0,  0.0),    # LT: 3-month net OBV rise
    ("up_down_vol_ratio_90d", 30.0, 1.10, 1.0),    # LT: 3-month buy/sell balance
    ("ma150_slope_pct",       20.0, 0.0, -0.5),    # LT: trend template
    ("obv_slope_long_pct",    10.0, 0.0, -2.0),    # VD: mid-swing OBV flow
    ("cmf_21d",               10.0, 0.10, 0.0),    # money-flow (if present)
]

# score >= lower -> level. First match from the top wins.
SCORE_BANDS: list[tuple[float, int]] = [
    (80.0, 5),
    (65.0, 4),
    (45.0, 3),
    (25.0, 2),
    (0.0,  1),
]


# --------------------------------------------------------------------------- #
# Buffer math (pure)
# --------------------------------------------------------------------------- #
def buffer_sessions(
    close: Optional[float],
    stop: Optional[float],
    atr_pct: Optional[float],
) -> Optional[float]:
    """Approximate trading sessions of downside before the stop is hit.

    ~= headroom_to_stop_% / atr_pct. Returns 0.0 at/through the stop, and None
    when inputs are missing (unknown buffer — the UI shows the buffer as N/A
    rather than a fabricated number).
    """
    if close is None or stop is None or atr_pct is None:
        return None
    try:
        close = float(close)
        stop = float(stop)
        atr_pct = float(atr_pct)
    except (TypeError, ValueError):
        return None
    if close <= 0 or atr_pct <= 0:
        return None
    if close <= stop:
        return 0.0
    headroom_pct = (close - stop) / close * 100.0
    return headroom_pct / atr_pct


def _bucket_for_sessions(sessions: Optional[float]) -> str:
    if sessions is None:
        return "NEXT_REVIEW"   # unknown buffer -> neutral "check next time"
    for lower, bucket in BUFFER_SESSION_BANDS:
        if sessions >= lower:
            return bucket
    return "ACT_NOW"


def _buffer_text(bucket: str) -> str:
    entry = BUFFER_TEXT.get(bucket, BUFFER_TEXT["NEXT_REVIEW"])
    if isinstance(entry, dict):
        # Fall back to the once-a-day phrasing if cadence isn't 1 or 2.
        return entry.get(REVIEWS_PER_DAY, entry.get(1, ""))
    return entry


def _headroom_pct(close: Optional[float], stop: Optional[float]) -> Optional[float]:
    if close is None or stop is None:
        return None
    try:
        close = float(close)
        stop = float(stop)
    except (TypeError, ValueError):
        return None
    if close <= 0:
        return None
    return (close - stop) / close * 100.0


# --------------------------------------------------------------------------- #
# Shared renderer — turns a resolved level + buffer inputs into the dict
# contract both feeders return.
# --------------------------------------------------------------------------- #
def _render(
    level: int,
    *,
    close: Optional[float],
    stop: Optional[float],
    atr_pct: Optional[float],
    reasons: list[str],
    source: str,
    score: Optional[int] = None,
    as_of: Optional[str] = None,
) -> dict:
    level = max(1, min(5, int(level)))
    sess = buffer_sessions(close, stop, atr_pct)
    # A red level always reads as ACT_NOW regardless of nominal headroom — the
    # colour is the decision, the buffer only refines the message above red.
    bucket = "ACT_NOW" if level == 1 else _bucket_for_sessions(sess)
    text = _buffer_text(bucket)
    head = _headroom_pct(close, stop)

    meta = LEVELS[level]
    return {
        "level": level,
        "color": meta["color"],
        "label": meta["label"],
        "message": LEVEL_MESSAGE[level].format(buffer=text),
        "buffer_bucket": bucket,
        "buffer_text": text,
        "buffer_sessions": (round(sess, 1) if sess is not None else None),
        "headroom_pct": (round(head, 2) if head is not None else None),
        "atr_pct": (round(float(atr_pct), 3) if atr_pct is not None else None),
        "reviews_per_day": REVIEWS_PER_DAY,
        "reasons": reasons,
        "source": source,
        "score": score,
        "as_of": as_of,
    }


# --------------------------------------------------------------------------- #
# LIVE feeder — spine is trajectory.overall; escalators only nudge.
# --------------------------------------------------------------------------- #
def _apply_escalators(
    base: int,
    *,
    trajectory_flip: bool,
    action_label: str,
    entry_stage: str,
    at_or_below_stop: bool,
    reasons: list[str],
) -> int:
    level = base

    # Confirm strength: EXTEND_5D fires only when the thesis is intact; a
    # coiled pre-breakout is a strong-accumulation tell. Only lifts an already
    # healthy (>=4) reading to STRONG — never rescues a weakening one.
    if level >= 4 and (action_label == "EXTEND_5D"
                       or entry_stage == "COILED_PRE_BREAKOUT"):
        if level < 5:
            reasons.append(
                f"confirmed strong ({action_label or entry_stage})"
            )
        level = 5

    # Escalate toward red: soft-weakness labels cap the level even if the spine
    # lags. These only pull DOWN.
    if action_label == "REVIEW_WEAKNESS_CONFIRMED" and level > _REVIEW_CAP:
        reasons.append("capped by REVIEW_WEAKNESS_CONFIRMED")
        level = _REVIEW_CAP
    elif action_label in ("MONITOR_EARLY_WEAKNESS", "MAINTAIN_DRY_UP") and level > _MONITOR_CAP:
        reasons.append(f"capped by {action_label}")
        level = _MONITOR_CAP

    # Hard red override — last word.
    if trajectory_flip or at_or_below_stop or (action_label or "").startswith("EXIT_"):
        why = []
        if trajectory_flip:
            why.append("trajectory flipped")
        if at_or_below_stop:
            why.append("price at/through stop")
        if (action_label or "").startswith("EXIT_"):
            why.append(action_label)
        reasons.append("RED override: " + ", ".join(why))
        level = 1

    return level


def gauge_from_position(pos: dict, *, atr_pct: Optional[float] = None) -> dict:
    """LIVE gauge for an enriched position dict from positions_view.

    Reads only fields positions_view already sets. `atr_pct` (the stock's
    ATR(14)/close from its entry [CS] trace) is supplied by the caller for the
    adversity buffer; None -> buffer shows as unknown but the colour still
    renders.
    """
    traj = pos.get("trajectory") or {}
    overall = traj.get("overall") or "unknown"
    action_label = pos.get("action_label") or ""
    entry_stage = pos.get("entry_stage") or ""
    trajectory_flip = bool(pos.get("trajectory_flip")
                           or traj.get("exit_recommendation"))
    close = pos.get("current_price")
    stop = pos.get("stop_price")
    at_or_below_stop = (
        close is not None and stop is not None
        and float(stop) > 0 and float(close) <= float(stop)
    )

    reasons = [f"signal trajectory: {overall}"]
    if action_label:
        reasons.append(f"action: {action_label}")

    base = OVERALL_TO_LEVEL.get(overall, 3)
    level = _apply_escalators(
        base,
        trajectory_flip=trajectory_flip,
        action_label=action_label,
        entry_stage=entry_stage,
        at_or_below_stop=at_or_below_stop,
        reasons=reasons,
    )
    return _render(
        level, close=close, stop=stop, atr_pct=atr_pct,
        reasons=reasons, source="live",
    )


# --------------------------------------------------------------------------- #
# HISTORICAL feeder — deterministic 0-100 score over per-date trace features.
# --------------------------------------------------------------------------- #
def _score_features(feat: dict, reasons: list[str]) -> Optional[float]:
    awarded = 0.0
    possible = 0.0
    for key, weight, full_t, half_t in SCORE_WEIGHTS:
        val = feat.get(key)
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        possible += weight
        if val >= full_t:
            awarded += weight
            reasons.append(f"{key}={val:g} (strong)")
        elif val >= half_t:
            awarded += weight * 0.5
            reasons.append(f"{key}={val:g} (soft)")
        else:
            reasons.append(f"{key}={val:g} (weak)")
    if possible <= 0:
        return None
    return 100.0 * awarded / possible


def _level_from_score(score: float) -> int:
    for lower, level in SCORE_BANDS:
        if score >= lower:
            return level
    return 1


def gauge_from_trace_features(
    feat: dict,
    *,
    close: Optional[float],
    stop: Optional[float],
    entry: Optional[float] = None,
    atr_pct: Optional[float] = None,
    as_of: Optional[str] = None,
) -> dict:
    """HISTORICAL gauge from a merged per-date trace feature bag.

    `feat` is the union of stage `features` dicts from a run_<date>_<sym>.jsonl
    trace. `atr_pct` defaults to feat['atr_pct'] (the [CS] stage value) when not
    passed. Colour comes from the 0-100 score; a close at/through the stop
    forces red regardless of score.
    """
    reasons: list[str] = []
    if atr_pct is None:
        atr_pct = feat.get("atr_pct")

    score = _score_features(feat, reasons)
    if score is None:
        level = 3
        reasons.append("no scorable features in trace — defaulting to CAUTION")
        score_out: Optional[int] = None
    else:
        level = _level_from_score(score)
        score_out = int(round(score))

    at_or_below_stop = (
        close is not None and stop is not None
        and float(stop) > 0 and float(close) <= float(stop)
    )
    if at_or_below_stop:
        reasons.append("RED override: price at/through stop")
        level = 1

    return _render(
        level, close=close, stop=stop, atr_pct=atr_pct,
        reasons=reasons, source="historical", score=score_out, as_of=as_of,
    )
