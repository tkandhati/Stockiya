"""Entry-stage label ladder — advisory human-facing enum.

Mirror of `action_labels.py` but for the *entry moment* rather than the
holding lifecycle. Answers the question a user reads on a pick card:

    "Is this pick pre-breakout, at the pivot, freshly confirmed, or
     already extended? Should I enter, wait, or leave it alone?"

Two orthogonal ladders keep the honest:
  - `action_labels.py`       → what to do with a stock you HOLD.
  - `entry_stage_label.py`   → what stage the setup is at right NOW.

Advisory only — this file does not gate selection. The composite/BR gates
still own the picking decision. This module is a labeling layer so the UI
(and the user) can tell an accumulating base apart from a chased breakout
without eyeballing the chart.

Ladder (staged):

    DEEP_BASE                  ≥8% under 20d high, tightness > 12%
        │
        ▼
    BUILDING_BASE              -8% .. -2% under 20d high, tightness in [10%, 12%]
        │
        ▼
    COILED_PRE_BREAKOUT        -6% .. -2% under 20d high, tightness < 10%,
                                quiet volume (vol10/vol50 <= 1.0x)
        │
        ▼
    AT_PIVOT                   within +/-1.5% of 20d high, volume ordinary
    AT_PIVOT_NO_DEMAND         within +/-1.5% of 20d high, vol10/vol50 < 1.0x
        │                       (dry at the pivot -- no confirmation likely)
        ▼
    BREAKOUT_CONFIRMED_TODAY   BR gate passed on this bar (institutional trigger)
        │
        ▼
    POST_BREAKOUT_HEALTHY      0..+5% above SMA20, within 15 bars of trigger
        │
        ▼
    POST_BREAKOUT_EXTENDED     +5..+10% above SMA20, within 15 bars of trigger
        │
        ▼
    LATE_CHASE                 >+10% above SMA20 within 10 bars of trigger

    FAILED_BREAKOUT_RETEST     pct_gain_since_breakout <= -3% within 10 bars
    DATA_UNAVAILABLE           essential features missing

Fix points:
    Threshold constants at the top of this file are the *only* place
    band edges live. All callers pass raw features; the classifier
    decides which state applies.
"""
from __future__ import annotations

from typing import Optional

# --------------------------------------------------------------------------- #
# Public label constants
# --------------------------------------------------------------------------- #
DEEP_BASE = "DEEP_BASE"
BUILDING_BASE = "BUILDING_BASE"
COILED_PRE_BREAKOUT = "COILED_PRE_BREAKOUT"
AT_PIVOT = "AT_PIVOT"
AT_PIVOT_NO_DEMAND = "AT_PIVOT_NO_DEMAND"
BREAKOUT_CONFIRMED_TODAY = "BREAKOUT_CONFIRMED_TODAY"
POST_BREAKOUT_HEALTHY = "POST_BREAKOUT_HEALTHY"
POST_BREAKOUT_EXTENDED = "POST_BREAKOUT_EXTENDED"
LATE_CHASE = "LATE_CHASE"
FAILED_BREAKOUT_RETEST = "FAILED_BREAKOUT_RETEST"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"

# --------------------------------------------------------------------------- #
# Tunable thresholds — the ONLY place band edges live
# --------------------------------------------------------------------------- #
DEEP_BASE_BREAK_PCT: float = -8.0            # tunable — deeper than -8% under pivot
PRE_BREAKOUT_BREAK_PCT_HI: float = -2.0      # tunable — upper edge of pre-breakout
PRE_BREAKOUT_BREAK_PCT_LO: float = -8.0      # tunable — lower edge (matches DEEP_BASE
                                             # boundary; tightness distinguishes
                                             # COILED vs BUILDING inside this band).
AT_PIVOT_BAND_PCT: float = 1.5               # tunable — +/- band around 20d high
QUIET_VOL_RATIO: float = 1.0                 # tunable — vol10/vol50 threshold
TIGHT_BASE_PCT: float = 10.0                 # tunable — 25-bar (high/low-1)*100
LOOSE_BASE_PCT: float = 12.0                 # tunable — anything wider is DEEP_BASE

HEALTHY_ABOVE_SMA20_PCT: float = 5.0         # tunable — post-BR healthy ceiling
EXTENDED_ABOVE_SMA20_PCT: float = 10.0       # tunable — anything above is LATE_CHASE
FRESH_BREAKOUT_WINDOW_BARS: int = 15         # tunable — bars while post-BR classification applies
LATE_CHASE_WINDOW_BARS: int = 10             # tunable — "chase" only makes sense while fresh
FAILED_RETEST_PCT: float = -3.0              # tunable — pct_gain below this within window
FAILED_RETEST_WINDOW_BARS: int = 10          # tunable


def entry_stage_label(
    *,
    br_passed_today: bool = False,
    break_pct: Optional[float] = None,
    close_vs_sma20_pct: Optional[float] = None,
    vol_ratio_10d_50d: Optional[float] = None,
    tightness_25bar_pct: Optional[float] = None,
    days_since_breakout: Optional[int] = None,
    pct_gain_since_breakout: Optional[float] = None,
) -> str:
    """Classify the current setup stage from tape features.

    Parameters (all optional — function degrades gracefully):
      br_passed_today          True on the exact bar the breakout gate fired.
      break_pct                (close / 20d_high - 1) * 100. Negative = below pivot.
      close_vs_sma20_pct       (close / SMA20 - 1) * 100. Positive = above.
      vol_ratio_10d_50d        Mean(vol[-10:]) / Mean(vol[-50:]). <1.0 = drying up.
      tightness_25bar_pct      (25-bar high / 25-bar low - 1) * 100. Smaller = tighter.
      days_since_breakout      Bars since BR fired. 0 = today, None = never fired.
      pct_gain_since_breakout  (close / breakout_entry - 1) * 100 for held positions.

    Returns one of the public label constants; always returns a label so
    downstream renderers don't need null checks.

    Priority order (highest first):
      1. Fresh trigger day               → BREAKOUT_CONFIRMED_TODAY
      2. Held / recent post-BR position  → POST_BREAKOUT_* / LATE_CHASE / FAILED_*
      3. Pre-breakout tape               → AT_PIVOT_* / COILED / BUILDING / DEEP_BASE

    Fix points:
      Threshold constants at the top of this file. No caller ever hard-codes
      a band edge — always pass raw features, let this function decide.
    """
    # 1. Fresh trigger bar always wins — BR is the objective institutional
    #    fingerprint. Everything else is inference from tape shape.
    if br_passed_today:
        return BREAKOUT_CONFIRMED_TODAY

    # 2. Post-breakout classification (held position or picked earlier).
    #    Prefer close_vs_sma20 (the cleaner geometric signal); fall back to
    #    pct_gain_since_breakout when SMA20 unavailable.
    in_post_window = (
        days_since_breakout is not None
        and 0 < days_since_breakout <= FRESH_BREAKOUT_WINDOW_BARS
    )
    if in_post_window or pct_gain_since_breakout is not None:
        # Failed-retest first — a breakout that gave back its gains within
        # the window is more informative than any other post-BR label.
        if (
            pct_gain_since_breakout is not None
            and pct_gain_since_breakout <= FAILED_RETEST_PCT
            and (days_since_breakout is None
                 or days_since_breakout <= FAILED_RETEST_WINDOW_BARS)
        ):
            return FAILED_BREAKOUT_RETEST

        # Prefer close_vs_sma20 when available. It's a geometric fact about
        # the tape, not derived from a specific entry price that may not
        # match the user's actual fill.
        if close_vs_sma20_pct is not None:
            if (
                close_vs_sma20_pct > EXTENDED_ABOVE_SMA20_PCT
                and (days_since_breakout is None
                     or days_since_breakout <= LATE_CHASE_WINDOW_BARS)
            ):
                return LATE_CHASE
            if close_vs_sma20_pct > HEALTHY_ABOVE_SMA20_PCT:
                return POST_BREAKOUT_EXTENDED
            if close_vs_sma20_pct >= 0:
                return POST_BREAKOUT_HEALTHY
            # close below SMA20 while inside the post-BR window — a
            # weakening breakout that hasn't yet failed; treat as healthy
            # pullback rather than fabricating a new state.
            return POST_BREAKOUT_HEALTHY

        # Fallback: use pct_gain_since_breakout as a proxy for SMA20 distance.
        if pct_gain_since_breakout is not None:
            if (
                pct_gain_since_breakout > EXTENDED_ABOVE_SMA20_PCT
                and (days_since_breakout is None
                     or days_since_breakout <= LATE_CHASE_WINDOW_BARS)
            ):
                return LATE_CHASE
            if pct_gain_since_breakout > HEALTHY_ABOVE_SMA20_PCT:
                return POST_BREAKOUT_EXTENDED
            return POST_BREAKOUT_HEALTHY

    # 3. Pre-breakout / at-pivot classification. Requires break_pct.
    if break_pct is None:
        return DATA_UNAVAILABLE

    # AT_PIVOT band — top of the pre-breakout ladder. Volume decides whether
    # the pivot is likely to confirm or fail on no demand.
    if abs(break_pct) <= AT_PIVOT_BAND_PCT:
        if vol_ratio_10d_50d is not None and vol_ratio_10d_50d < QUIET_VOL_RATIO:
            return AT_PIVOT_NO_DEMAND
        return AT_PIVOT

    # Below pivot — how deep? Tightness decides coiled vs still-building.
    if break_pct < DEEP_BASE_BREAK_PCT:
        return DEEP_BASE

    if PRE_BREAKOUT_BREAK_PCT_LO <= break_pct <= PRE_BREAKOUT_BREAK_PCT_HI:
        # Classic pre-breakout zone. Tightness gates the "coiled" claim.
        if (
            tightness_25bar_pct is not None
            and tightness_25bar_pct <= TIGHT_BASE_PCT
        ):
            return COILED_PRE_BREAKOUT
        if (
            tightness_25bar_pct is not None
            and tightness_25bar_pct <= LOOSE_BASE_PCT
        ):
            return BUILDING_BASE
        # No tightness data — default to BUILDING_BASE (honest: we can't
        # claim "coiled" without base-width evidence).
        return BUILDING_BASE

    # break_pct > PRE_BREAKOUT_BREAK_PCT_HI but outside AT_PIVOT band --
    # this can only mean between -2% and +... which is inside pivot band
    # (handled) or above (would be POST_BREAKOUT_* territory but no
    # days_since_breakout was provided). Report the honest not-enough-context
    # state rather than fabricating one.
    return DATA_UNAVAILABLE
