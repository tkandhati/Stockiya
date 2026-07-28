"""[LTV] Long-Term Distribution Veto — hard admission gate.

The anti-false-breakout guard. A stock can look like a textbook breakout on
price structure — above its 150/200d MAs, Weinstein Stage 2, tight base — while
its *cumulative* volume flow over the last quarter is NEGATIVE, i.e. institutions
have been net sellers into the rally. That is the classic false breakout: price
strength financed by distribution, not accumulation.

Concrete motivating case (Marico, 2026-07): Stage-2 advance, 60-day base,
Minervini template YES — but OBV-90d slope was -208%, +14% above its 200d MA,
at its 52-week high. It was being *distributed*, yet the soft-gate composite
still cleared τ on the base + short-term accumulation legs alone. This gate
closes that hole.

Base rule:

    OBV-90d slope < OBV_90D_DISTRIBUTION_MAX (default 0.0)  ->  candidate VETO

Symmetry note: we already EXIT a held position when OBV-90d turns down. It would
be incoherent to ENTER a new position while OBV-90d is already down — for a
stock that has *already advanced*.

Pre-breakout carve-out (the important nuance — see CHANGELOG 2026-07-28)
------------------------------------------------------------------------
The [AC] accumulation stage detects a SHORT-window (~20-60 bar) ADI positive
divergence — "quiet buying over the last month". A genuine Stage 1→2 base that
is only now turning up can therefore pass [AC] while its 90-DAY OBV slope is
still negative, purely because the 90-day window straddles the tail of the prior
downtrend. Vetoing that would amputate exactly the early pre-breakout setups the
strategy exists to find. "Distribution into strength" requires the *strength*:
Marico was extended and at highs; an early base is not.

So a negative-OBV-90d ticker is EXEMPT from the veto when ALL hold:
    1. [AC] passed AND AC.score >= AC_STRONG_MIN   (a genuine tight coil, not a
                                                     marginal one — mirrors
                                                     pipeline.TRIGGER_AC_MIN_SCORE)
    2. close <= 200d MA * (1 + EXT_ABOVE_SMA200_MAX)   (NOT extended — still at
                                                         the launch pad, so this
                                                         cannot be a distribution-
                                                         into-strength rally)
    3. OBV-30d slope > 0                            (recent flow has turned up —
                                                     accumulation has begun; this
                                                     is a base "healing", not a
                                                     dead one still bleeding)
Marico fails (1) and (2); a real early coil passes all three.

Why a hard gate and not a soft-gate weight: negative 3-month flow *in an already
advanced name* is not a "weak leg to be compensated" — it is a disqualifier.

Availability: [I] Ingest already hard-requires >=200 bars, so OBV-90d always has
full lookback here. If the slope is uncomputable we PASS (do not reject on
missing data). Runs AFTER [ACS]/[AC] so the carve-out can read the coil score.

Fix points:
    OBV_90D_DISTRIBUTION_MAX  : slope below which we veto, % (default 0.0).
    OBV_SLOPE_WINDOW          : lookback bars for the OBV slope (default 90).
    PREBREAKOUT_EXEMPT_ENABLED: master switch for the carve-out (default True).
    AC_STRONG_MIN             : AC score floor for "genuine coil" (default 0.6).
    EXT_ABOVE_SMA200_MAX      : max close-vs-200d-MA to count as "not extended"
                                (default 0.05 = +5%).
    OBV_RECENT_WINDOW         : recent-flow window for the "turned up" check
                                (default 30).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..indicators import obv, obv_norm_slope_pct, obv_slope_pct, sma
from ..pipeline import PipelineContext, StageResult

stage_id = "LTV"

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

OBV_90D_DISTRIBUTION_MAX: float = 0.0   # tunable — veto when OBV-90d slope < this
OBV_SLOPE_WINDOW: int = 90              # tunable

# Pre-breakout carve-out
PREBREAKOUT_EXEMPT_ENABLED: bool = True   # tunable — master switch
AC_STRONG_MIN: float = 0.6                # tunable — mirrors pipeline.TRIGGER_AC_MIN_SCORE
EXT_ABOVE_SMA200_MAX: float = 0.05        # tunable — "not extended" band vs 200d MA
OBV_RECENT_WINDOW: int = 30               # tunable — recent-flow window


def _prebreakout_exempt(
    df: pd.DataFrame,
    stage_results: dict,
    obv_series,
) -> tuple[bool, str]:
    """Return (is_exempt, reason). A genuine early coil is exempt from the veto.

    Reads the [AC] result already in stage_results. Degrades safely: if AC is
    absent (e.g. a diagnostic chain that didn't run it) the ticker is NOT exempt
    — the veto stays conservative.
    """
    if not PREBREAKOUT_EXEMPT_ENABLED:
        return False, ""

    ac = (stage_results or {}).get("AC")
    ac_score = float(getattr(ac, "score", 0.0) or 0.0) if ac is not None else 0.0
    if ac is None or not getattr(ac, "passed", False) or ac_score < AC_STRONG_MIN:
        return False, f"not a strong coil (AC score {ac_score:.2f} < {AC_STRONG_MIN:.2f})"

    close = float(df["Close"].iloc[-1])
    sma200 = sma(df["Close"], 200)
    if not (sma200 and sma200 > 0):
        return False, "200d MA unavailable"
    ext = close / sma200 - 1.0
    if ext > EXT_ABOVE_SMA200_MAX:
        return False, f"extended {ext*100:+.0f}% vs 200d MA (> {EXT_ABOVE_SMA200_MAX*100:.0f}%)"

    # Zero-crossing-safe: a base off a decline has OBV negative-but-rising, where
    # the ratio-based obv_slope_pct would give a spurious negative. Use the
    # regression-normalized slope so "turning up" has the correct sign.
    obv_recent = obv_norm_slope_pct(obv_series, OBV_RECENT_WINDOW)
    if obv_recent is None or obv_recent <= 0:
        return False, "recent OBV not turning up"

    return True, (
        f"early coil (AC {ac_score:.2f}), {ext*100:+.0f}% vs 200d MA, "
        f"OBV-{OBV_RECENT_WINDOW}d {obv_recent:+.0f}% turning up"
    )


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    if df is None or df.empty:
        # No tape = nothing to veto on. [I] Ingest already hard-gates on missing
        # data, so passing here never admits an unvetted ticker.
        return StageResult(
            stage_id=stage_id,
            passed=True,
            features={"skipped": True, "reason": "no ohlcv"},
            fix_point="backend/stages/lt_distribution_veto.py",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    dist_max = float(
        overrides.get("ltv_obv_90d_distribution_max", OBV_90D_DISTRIBUTION_MAX)
    )

    obv_series = obv(df["Close"], df["Volume"])
    # Decide on the zero-crossing-safe slope (correct sign even when OBV is
    # negative-but-rising off a base). Keep the legacy ratio in features only for
    # cross-referencing the card's "OBV (90d)" number — NOT for the decision.
    obv90 = obv_norm_slope_pct(obv_series, OBV_SLOPE_WINDOW)
    obv90_ratio = obv_slope_pct(obv_series, OBV_SLOPE_WINDOW)

    features: dict = {
        "obv_90d_norm_slope_pct": round(obv90, 2) if obv90 is not None else None,
        "obv_90d_slope_pct_ratio": round(obv90_ratio, 2) if obv90_ratio is not None else None,
        "distribution_max": dist_max,
    }

    if obv90 is None:
        # Not enough history to judge flow — defer to Ingest, do not reject.
        return StageResult(
            stage_id=stage_id,
            passed=True,
            features={**features, "skipped": True, "reason": "OBV-90d unavailable"},
            evidence=["OBV-90d slope unavailable — flow veto deferred"],
            fix_point="backend/stages/lt_distribution_veto.py",
        )

    if obv90 >= dist_max:
        return StageResult(
            stage_id=stage_id,
            passed=True,
            features=features,
            evidence=[f"OBV-90d slope {obv90:+.1f}% >= {dist_max:.1f}% — no long-term distribution"],
            fix_point="backend/stages/lt_distribution_veto.py — OBV_90D_DISTRIBUTION_MAX",
            reason="no long-term distribution footprint",
        )

    # obv90 < dist_max — candidate for veto. Check the pre-breakout carve-out
    # so we don't amputate a genuine early accumulation base.
    exempt, ex_reason = _prebreakout_exempt(df, ctx.stage_results, obv_series)
    features["prebreakout_exempt"] = exempt
    features["exempt_reason"] = ex_reason

    if exempt:
        return StageResult(
            stage_id=stage_id,
            passed=True,
            features=features,
            evidence=[
                f"OBV-90d slope {obv90:+.1f}% < {dist_max:.1f}% BUT genuine early "
                f"pre-breakout coil — {ex_reason}. Early accumulation (window "
                "straddles the prior decline), not distribution-into-strength."
            ],
            fix_point="backend/stages/lt_distribution_veto.py — pre-breakout carve-out",
            reason=f"early pre-breakout exemption ({ex_reason})",
        )

    return StageResult(
        stage_id=stage_id,
        passed=False,
        features=features,
        evidence=[
            f"OBV-90d slope {obv90:+.1f}% < {dist_max:.1f}% — institutions "
            "net-selling over 3 months (distribution into strength). "
            f"Not an early coil: {ex_reason}."
        ],
        fix_point="backend/stages/lt_distribution_veto.py — OBV_90D_DISTRIBUTION_MAX",
        reason=(
            f"long-term distribution: OBV-90d slope {obv90:+.1f}% < {dist_max:.1f}%"
        ),
    )
