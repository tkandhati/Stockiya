"""Early-Accumulation detector — the "genuine early breakout, slowly
accumulating" quality signal.

The [LTV] veto removes the *negative* case (institutions distributing into a
rally — the false breakout). This module scores the *positive* case the user
actually wants to own: a stock that is

    EARLY                       — still near its launch pad, price hasn't run,
                                  not extended above its moving averages, and
    SLOWLY + DURABLY ACCUMULATING — cumulative volume flow has been quietly
                                  positive for BOTH the last quarter (OBV-90d)
                                  AND half-year (OBV-180d), with steady net
                                  buying and a dry-up / divergence footprint
                                  (quiet re-accumulation, not a blow-off spike).

It is ADVISORY + PREFERENCE, never a hard gate:
  - the ranker (backend/stages/rank.py) turns a match into additive bonuses so
    these setups float to the top of the daily picks — nothing is excluded;
  - the pick payload carries the label so the card / reasoning checklist can
    show WHY a pick is a genuine early accumulation.

Deterministic and self-contained: it reads only the OHLCV frame plus the
already-computed stage results (VD / AC / CS). It does NOT depend on the
AccumulationSignals object (which the pipeline does not compute per-ticker), so
it is safe to call from inside the ranker.

Distinguishing SLOW from FAST: "slow" is the whole point. We require the quiet
footprint (VD dry-up / bullish OBV divergence, or a genuine AC accumulation
score) and durable multi-horizon OBV — a single-day volume ignition spike does
NOT earn this label, because a blow-off is fast, not slow.

Fix points (all thresholds live here — callers pass raw frames only):
    OBV_90D_MIN               OBV-90d slope must exceed this (default 0.0)
    OBV_180D_MIN              OBV-180d slope must exceed this (default 0.0)
    UPDOWN_90D_MIN            up/down vol ratio 90d floor (default 1.05)
    EARLY_RET_180_MAX         max 6-month return to still count as "early"
                              (default 0.30 = +30%)
    EARLY_EXT_ABOVE_SMA50_MAX max close-vs-50d-MA to count as "not extended"
                              (default 0.12 = +12%)
    AC_QUIET_MIN              AC score that counts as a quiet-accumulation
                              footprint when VD did not pass (default 0.5)
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from .indicators import obv, obv_norm_slope_pct, sma, up_down_vol_ratio

# --------------------------------------------------------------------------- #
# Tunable thresholds — the ONLY place these band edges live.
# --------------------------------------------------------------------------- #

OBV_90D_MIN: float = 0.0
OBV_180D_MIN: float = 0.0
UPDOWN_90D_MIN: float = 1.05
EARLY_RET_180_MAX: float = 0.30
EARLY_EXT_ABOVE_SMA50_MAX: float = 0.12
AC_QUIET_MIN: float = 0.5


def _stage_passed(stage_results: dict, sid: str) -> bool:
    sr = stage_results.get(sid) if stage_results else None
    return bool(sr is not None and getattr(sr, "passed", False))


def _stage_score(stage_results: dict, sid: str) -> float:
    sr = stage_results.get(sid) if stage_results else None
    if sr is None:
        return 0.0
    try:
        return float(getattr(sr, "score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def assess_early_accumulation(
    ohlcv: Optional[pd.DataFrame],
    stage_results: Optional[dict[str, Any]] = None,
) -> dict:
    """Return the early-accumulation assessment for one ticker.

    Shape:
        {
          "is_match":   bool,      # tier is "early" or "mid"
          "tier":       "early" | "mid" | None,
          "score":      float,     # [0,1] fraction of profile conditions met
          "reasons":    [str, ...] # human-readable, ordered strongest-first
          "features":   {...}      # the raw measurements, for the trace
        }

    Never raises: missing data degrades to is_match=False.
    """
    stage_results = stage_results or {}
    null = {
        "is_match": False,
        "tier": None,
        "score": 0.0,
        "reasons": [],
        "features": {},
    }
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 60:
        return null

    df = ohlcv
    close = df["Close"]
    volume = df["Volume"]
    last_close = float(close.iloc[-1])

    obv_series = obv(close, volume)
    # Zero-crossing-safe slope: a base recovering from a decline has OBV that is
    # negative-but-rising, where the ratio-based obv_slope_pct flips sign. The
    # normalized regression slope keeps the correct sign so genuine durable
    # accumulation isn't missed. Same convention as the [LTV] gate.
    obv90 = obv_norm_slope_pct(obv_series, 90)
    obv180 = obv_norm_slope_pct(obv_series, 180)
    ud90 = up_down_vol_ratio(close, volume, 90)
    sma50 = sma(close, 50)

    ret_180: Optional[float] = None
    if len(df) >= 181:
        base = float(close.iloc[-181])
        if base > 0:
            ret_180 = last_close / base - 1.0

    ext_above_sma50: Optional[float] = None
    if sma50 and sma50 > 0:
        ext_above_sma50 = last_close / sma50 - 1.0

    vd_pass = _stage_passed(stage_results, "VD")
    ac_pass = _stage_passed(stage_results, "AC")
    ac_score = _stage_score(stage_results, "AC")
    cs_pass = _stage_passed(stage_results, "CS")

    # ---- Condition set -------------------------------------------------- #
    # DURABLE slow accumulation: positive cumulative flow over BOTH horizons +
    # steady (not spiky) net buying + a quiet footprint.
    quiet_footprint = vd_pass or (ac_pass and ac_score >= AC_QUIET_MIN)

    c_obv90 = obv90 is not None and obv90 > OBV_90D_MIN
    c_obv180 = obv180 is not None and obv180 > OBV_180D_MIN
    c_ud90 = ud90 is not None and ud90 >= UPDOWN_90D_MIN
    c_quiet = bool(quiet_footprint)

    durable_slow = c_obv90 and c_obv180 and c_ud90 and c_quiet

    # EARLY / not extended: still near the launch pad.
    c_ret180 = ret_180 is not None and ret_180 <= EARLY_RET_180_MAX
    c_not_extended = (
        ext_above_sma50 is not None and ext_above_sma50 <= EARLY_EXT_ABOVE_SMA50_MAX
    )
    c_base = bool(cs_pass)
    not_extended_early = c_ret180 and c_not_extended and c_base

    conditions = [c_obv90, c_obv180, c_ud90, c_quiet, c_ret180, c_not_extended, c_base]
    score = round(sum(1 for c in conditions if c) / len(conditions), 3)

    if durable_slow and not_extended_early:
        tier: Optional[str] = "early"
    elif durable_slow:
        tier = "mid"
    else:
        tier = None

    # ---- Human-readable reasons (only the ones that fired) -------------- #
    reasons: list[str] = []
    if c_obv90 and c_obv180:
        reasons.append(
            f"durable OBV: +{obv90:.0f}% (90d) and +{obv180:.0f}% (180d) — "
            "quiet buying across both quarter and half-year"
        )
    if c_ud90:
        reasons.append(f"steady net buying: up/down vol 90d {ud90:.2f}x")
    if c_quiet:
        reasons.append(
            "quiet re-accumulation footprint (volume dry-up / bullish OBV "
            "divergence)" if vd_pass else f"accumulation footprint (AC {ac_score:.2f})"
        )
    if tier == "early":
        parts = []
        if ext_above_sma50 is not None:
            parts.append(f"{ext_above_sma50*100:+.0f}% vs 50d MA")
        if ret_180 is not None:
            parts.append(f"{ret_180*100:+.0f}% / 180d")
        reasons.append(
            "genuine early entry — not extended (" + ", ".join(parts) + ")"
            if parts else "genuine early entry — not extended"
        )

    features = {
        "obv_90d_norm_slope_pct": round(obv90, 2) if obv90 is not None else None,
        "obv_180d_norm_slope_pct": round(obv180, 2) if obv180 is not None else None,
        "up_down_vol_ratio_90d": round(ud90, 3) if ud90 is not None else None,
        "ret_180d": round(ret_180, 4) if ret_180 is not None else None,
        "ext_above_sma50": round(ext_above_sma50, 4) if ext_above_sma50 is not None else None,
        "quiet_footprint": c_quiet,
        "base_mature": c_base,
        "durable_slow": durable_slow,
        "not_extended_early": not_extended_early,
    }

    return {
        "is_match": tier is not None,
        "tier": tier,
        "score": score,
        "reasons": reasons,
        "features": features,
    }
