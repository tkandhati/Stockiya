"""Smart-money (VPA) read for the Coiling follow-up — Anna Coulling's Volume
Price Analysis, applied to a pick already under watch.

PRESENTATION / MONITORING ONLY. Nothing here changes selection, scoring, sizing,
or exits (PRINCIPLES.md; feedback: additive-over-redesign). It is consumed only
by `backend/pick_followup.py`, which is itself monitoring-only. Its one influence
on behaviour is a **bounded, reversible tilt** to the coiling *volume-add* axis —
and delivery % is a VOLUME-quality signal, not a fundamental, so that tilt honours
PRINCIPLES §8 ("don't override the volume signal with fundamentals"), unlike the
strictly context-only macro layer (`macro_context.py`).

WHY THIS EXISTS (owner ask)
---------------------------
The coiling table ranked each open pick purely on traded-volume figures (OBV-90d
slope + up/down-vol-90d). It ignored the one thing that separates real
accumulation from intraday churn — NSE **delivery %** — and the named VPA
footprints the rest of the system already computes. Anna Coulling's canonical
tell is exactly this: *moderate (non-spiking) volume + a high delivery % while
price consolidates = structural accumulation by "smart money" quietly taking
shares to delivery.* This module recognises that pattern and three siblings, and
honestly flags the opposite (distribution).

THE FOUR READS (all from data already on file — traces + NSE delivery)
----------------------------------------------------------------------
  1. structural_accumulation  moderate volume + high/decent delivery % + price
                              consolidating (the Coulling headline).
  2. quiet_accumulation       delivery% held above the band for N days, and/or a
                              multi-horizon delivery drift rising (spike-proof).
  3. no_supply                volume dried up while price held AND up-days still
                              dominate (absorption); the same dry-up with flat
                              up/down is *apathy*, not absorption — no credit.
  4. distribution_warning     delivery is churn, OR >=3 distribution days in 15,
                              OR OBV hemorrhaging, OR OBV-90d negative. The honest
                              counter-signal — forces confirmation to 0 so a
                              distributing name can never earn a smart-money boost.

CONTRACT
--------
`assess_smart_money` is a PURE function of the dicts handed to it (no I/O, no
network, never raises, deterministic). It degrades cleanly: with no delivery on
disk the delivery-led reads are simply absent and `confirmation == 0`, so the
caller's ranking is byte-identical to the pre-change behaviour. Reversible via
env `STOCKYA_SMART_MONEY=0` (then every read is empty and confirmation is 0).

Fix points (top of file):
    STRUCTURAL_VOL_SPIKE_MAX  volume ratio above which volume is a SPIKE (not the
                              "moderate volume" the structural read needs).
    STRUCTURAL_VOL_MIN        volume ratio below which the tape is essentially dead.
    STRUCTURAL_DELIV_MIN      delivery% floor for "high delivery" at the moderate band.
    STRUCTURAL_PRICE_FLAT_PCT price move (%) at/below which price is still consolidating.
    DRYUP_MIN_DAYS            dry-up streak (sessions) at/above which supply has dried up.
    NO_SUPPLY_DEMAND_MIN      right-edge up/down-vol ratio that makes a dry-up absorption.
    DIST_DAY_WARN             distribution-day count (in 15) at/above which the tape distributes.
    CONFIRMATION_CAP          hard cap on the blended bullish confirmation (0-1).
"""
from __future__ import annotations

import math
import os

# Reuse delivery's calibrated bands so the two modules never drift apart.
from .delivery import (
    STREAK_FULL_DAYS,
    STREAK_MIN_PCT,
    STREAK_NOTE_MIN,
    STRONG_DELIV_PCT,
    WEAK_DELIV_PCT,
)

# --------------------------------------------------------------------------- #
# Fix points
# --------------------------------------------------------------------------- #

# Volume "spike" ceiling — above this (5d-vs-50d, or today-vs-50d) the volume is a
# SPIKE, not the moderate participation Coulling's structural-accumulation read
# needs (smart money accumulates without lighting up the volume tape).
STRUCTURAL_VOL_SPIKE_MAX: float = 1.8
# ...and a floor: below this the tape is essentially dead — no real participation.
STRUCTURAL_VOL_MIN: float = 0.4
# Delivery% at/above which delivery is "high" enough to call structural
# accumulation while sitting in the moderate band (between WEAK and STRONG).
STRUCTURAL_DELIV_MIN: float = 50.0
# Price move (%) since suggestion at/below which price is still "consolidating"
# (the coil). Beyond this the move has begun — no longer quiet accumulation.
STRUCTURAL_PRICE_FLAT_PCT: float = 8.0

# Volume dry-up streak (trailing sessions below the 25th pct of 50d) at/above
# which supply has genuinely dried up.
DRYUP_MIN_DAYS: int = 3
# Right-edge up/down-vol ratio at/above which a dry-up is ABSORPTION (stealth
# demand), not apathy — mirrors pre_breakout_tag.STEALTH_DEMAND_MIN_RATIO intent,
# read off the 90d up/down ratio the coil row already carries.
NO_SUPPLY_DEMAND_MIN: float = 1.1

# Distribution-day cluster count (in 15 sessions) at/above which the tape is
# distributing. Mirrors PRINCIPLES §5 (>=3/15).
DIST_DAY_WARN: int = 3

# Hard cap on the blended bullish confirmation the caller may tilt on.
CONFIRMATION_CAP: float = 1.0

# Blend weights: structural accumulation dominates; the others contribute less.
_CONF_WEIGHTS = {
    "structural_accumulation": 1.0,
    "no_supply": 0.7,
    "quiet_accumulation": 0.6,
}
# How much the second-and-beyond bullish reads add on top of the dominant one.
_CONF_SECONDARY_FRAC: float = 0.3


def _is_num(x) -> bool:
    """True for a real, finite number (not None, not NaN, not bool)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _to_float(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _enabled() -> bool:
    """On by default; STOCKYA_SMART_MONEY=0 disables the whole read."""
    return os.environ.get("STOCKYA_SMART_MONEY", "1") != "0"


def _empty() -> dict:
    return {
        "signals": [],
        "headline": "—",
        "kind": "neutral",
        "confirmation": 0.0,
        "warning": False,
    }


def _volume_moderate(vol_today, vol_5_50) -> bool:
    """Volume is 'moderate' — not a spike, not dead. Prefer the steadier 5d/50d
    ratio; fall back to today/50d. Unknown -> treat as moderate (delivery, not the
    traded-volume ratio, is the star of this read; a rejected-day trace often has
    no volume ratio at all)."""
    v = vol_5_50 if _is_num(vol_5_50) else vol_today
    if not _is_num(v):
        return True
    return STRUCTURAL_VOL_MIN <= v <= STRUCTURAL_VOL_SPIKE_MAX


def _deliv_level_strength(latest_pct) -> float:
    """0 at/below the WEAK band, 1 at/above STRONG — how strong the level is."""
    if not _is_num(latest_pct):
        return 0.0
    span = STRONG_DELIV_PCT - WEAK_DELIV_PCT
    return _clamp01((latest_pct - WEAK_DELIV_PCT) / span) if span > 0 else 0.0


def assess_smart_money(
    feat: dict,
    delivery_adv: dict,
    *,
    price_change_pct=None,
    obv90=None,
    ud90=None,
) -> dict:
    """VPA smart-money read for one coiling row. Pure; never raises.

    Args:
        feat:         merged latest-trace features (pick_followup._merge_trace_features).
        delivery_adv: a delivery.delivery_advisory-shaped dict (or None / {}).
        price_change_pct: price move since suggestion (%), for the consolidation test.
        obv90/ud90:   the row's continuous OBV-90d slope % and up/down-vol-90d
                      (fall back to the trace features when not supplied).

    Returns {signals[], headline, kind, confirmation(0-1), warning(bool)}.
    `confirmation` feeds the caller's bounded coil-quality tilt; it is 0 whenever a
    distribution warning fires, when disabled, or when nothing bullish is found.
    """
    if not _enabled():
        return _empty()

    feat = feat or {}
    adv = delivery_adv or {}
    signals: list[dict] = []

    # --- shared inputs (trace features + the row's continuous figures) --------
    vol_today = _to_float(feat.get("vol_ratio_today_50d"))
    vol_5_50 = _to_float(feat.get("vol_ratio_5_50"))
    dryup = _to_float(feat.get("dry_up_streak_days_p25"))
    ud = ud90 if _is_num(ud90) else _to_float(feat.get("up_down_vol_ratio_90d"))
    infl = feat.get("obv_flow_inflection")
    dist = _to_float(feat.get("dist_day_count_15"))
    obv = obv90 if _is_num(obv90) else _to_float(feat.get("obv_90d_slope_pct"))

    deliv_ok = bool(adv.get("available"))
    latest_pct = _to_float(adv.get("latest_pct"))
    level = adv.get("level")
    trend = adv.get("trend")
    streak = _to_float(adv.get("accum_streak_days")) or 0.0
    drift = adv.get("accum_drift")

    # --- 1. Distribution warning (FIRST — it suppresses every bullish read) ---
    warn_reasons: list[str] = []
    if deliv_ok and level == "weak" and _is_num(latest_pct):
        warn_reasons.append(f"delivery {latest_pct:.0f}% — mostly intraday churn")
    if _is_num(dist) and dist >= DIST_DAY_WARN:
        warn_reasons.append(f"{int(dist)} distribution days in 15 sessions")
    if infl == "hemorrhaging":
        warn_reasons.append("OBV flow hemorrhaging (10d & 30d both negative)")
    if _is_num(obv) and obv < 0:
        warn_reasons.append(f"OBV-90d negative ({obv:+.0f}%)")
    warning = bool(warn_reasons)
    if warning:
        signals.append({
            "key": "distribution_warning",
            "label": "Distribution risk",
            "kind": "bearish",
            "strength": 1.0,
            "note": "Smart money may be leaving, not accumulating: "
                    + "; ".join(warn_reasons) + ".",
        })

    # --- 2. Structural accumulation (the Coulling headline) -------------------
    if deliv_ok and not warning:
        vol_moderate = _volume_moderate(vol_today, vol_5_50)
        deliv_high = _is_num(latest_pct) and (
            latest_pct >= STRONG_DELIV_PCT
            or (level in ("moderate", "strong") and latest_pct >= STRUCTURAL_DELIV_MIN)
        )
        price_flat = (not _is_num(price_change_pct)) or abs(price_change_pct) <= STRUCTURAL_PRICE_FLAT_PCT
        if vol_moderate and deliv_high and price_flat:
            s = _deliv_level_strength(latest_pct)
            if trend == "rising" or drift == "rising":
                s = _clamp01(s + 0.15)
            signals.append({
                "key": "structural_accumulation",
                "label": "Structural accumulation",
                "kind": "bullish",
                "strength": round(s, 2),
                "note": (
                    f"Moderate volume with delivery {latest_pct:.0f}% ({level})"
                    + (", rising" if trend == "rising" else "")
                    + " while price consolidates — smart money quietly taking shares "
                      "to delivery (Coulling)."
                ),
            })

    # --- 3. Quiet accumulation streak / drift --------------------------------
    if deliv_ok and not warning and (streak >= STREAK_NOTE_MIN or drift == "rising"):
        s = _clamp01(streak / STREAK_FULL_DAYS) if STREAK_FULL_DAYS else 0.0
        if drift == "rising":
            s = _clamp01(max(s, 0.5) + 0.1)
        parts: list[str] = []
        if streak >= STREAK_NOTE_MIN:
            parts.append(f"delivery held ≥{STREAK_MIN_PCT:.0f}% for {int(streak)} days")
        if drift == "rising":
            parts.append("delivery stacking up across 5d/15d/30d")
        signals.append({
            "key": "quiet_accumulation",
            "label": "Quiet accumulation",
            "kind": "bullish",
            "strength": round(s, 2),
            "note": "Quiet accumulation — " + "; ".join(parts) + ".",
        })

    # --- 4. No-supply / dry-up (absorption vs apathy) ------------------------
    if not warning and _is_num(dryup) and dryup >= DRYUP_MIN_DAYS:
        if _is_num(ud) and ud >= NO_SUPPLY_DEMAND_MIN:
            s = _clamp01(0.4 + 0.1 * (dryup - DRYUP_MIN_DAYS))
            signals.append({
                "key": "no_supply",
                "label": "No supply",
                "kind": "bullish",
                "strength": round(s, 2),
                "note": (
                    f"Volume dried up for {int(dryup)} sessions while up-days still "
                    f"dominate (up/down {ud:.2f}x) — supply absorbed, no selling pressure."
                ),
            })
        else:
            signals.append({
                "key": "dry_up_apathy",
                "label": "Dry-up (apathy)",
                "kind": "neutral",
                "strength": 0.0,
                "note": (
                    f"Volume dried up for {int(dryup)} sessions but up/down flat"
                    + (f" ({ud:.2f}x)" if _is_num(ud) else "")
                    + " — apathy, not absorption."
                ),
            })

    # --- Blend -> confirmation + headline ------------------------------------
    bull = [s for s in signals if s["kind"] == "bullish"]
    ranked = sorted(
        bull, key=lambda s: s["strength"] * _CONF_WEIGHTS.get(s["key"], 0.5), reverse=True
    )

    if warning or not ranked:
        confirmation = 0.0
    else:
        top = ranked[0]["strength"] * _CONF_WEIGHTS.get(ranked[0]["key"], 0.5)
        rest = sum(
            s["strength"] * _CONF_WEIGHTS.get(s["key"], 0.5) for s in ranked[1:]
        ) * _CONF_SECONDARY_FRAC
        confirmation = round(_clamp01(min(CONFIRMATION_CAP, top + rest)), 3)

    if warning:
        headline, kind = "Distribution risk", "bearish"
    elif ranked:
        headline, kind = ranked[0]["label"], "bullish"
    elif signals:
        headline, kind = signals[0]["label"], "neutral"
    else:
        headline, kind = "—", "neutral"

    return {
        "signals": signals,
        "headline": headline,
        "kind": kind,
        "confirmation": confirmation,
        "warning": warning,
    }
