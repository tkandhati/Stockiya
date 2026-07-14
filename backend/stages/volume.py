"""[VD] Volume + Divergence gate.

Two checks; at least ONE must pass (relaxed for Nifty 100, where genuine
volume dry-up below 50% of ADV50 is rare on always-liquid large-caps):

  1. Volume Dry-Up (Minervini):   adv(5) / adv(50) < 0.70
     5-day average volume materially below the 50-day average — supply
     thinning near support.

  2. Bullish OBV-price divergence over the last 20 bars:
       classic     = price made a lower low while OBV made a higher low
       flat-price  = price held +/-2 % while OBV climbed >= 2 %

Both checks still contribute to the margin score, so stocks that fire both
rank higher than stocks that fire only one.

Fix points:
    VOLUME_DRYUP_RATIO_MAX  : max recent/long ADV ratio (default 0.70)
    ADV_RECENT_WINDOW       : recent-volume window (default 5)
    ADV_LONG_WINDOW         : long-volume window (default 50)
    DIVERGENCE_LOOKBACK     : bars for divergence detection (default 20)
"""

from __future__ import annotations

from ..indicators import adv, obv_bullish_divergence, obv_flow_inflection
from ..pipeline import PipelineContext, StageResult

stage_id = "VD"

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

VOLUME_DRYUP_RATIO_MAX: float = 0.70   # tunable
ADV_RECENT_WINDOW: int = 5             # tunable
ADV_LONG_WINDOW: int = 50              # tunable
DIVERGENCE_LOOKBACK: int = 20          # tunable

# --------------------------------------------------------------------------- #
# OBV flow-velocity adjustment (pre-breakout inflection signal).
#
# Adds a bounded ±adjustment to the VD margin based on the 10d-vs-30d OBV
# slope inflection. Healing flow (long weak, short turning up) is the classic
# pre-breakout tell — reward it. Hemorrhaging flow (both windows negative)
# is bull-trap territory — penalize. Neutral leaves the margin unchanged.
#
# Bounded by VELOCITY_MARGIN_BONUS so it can't dominate the raw dry-up +
# divergence signal; it only tilts the ranking within a plausible band.
# --------------------------------------------------------------------------- #

VELOCITY_SHORT_WIN: int = 10           # tunable — recent slope window
VELOCITY_LONG_WIN: int = 30            # tunable — background slope window
# Advisory-sized bump: enough to tiebreak between strong candidates, too
# small to tip a marginal pick over the composite threshold. Reduced from
# 0.10 on 2026-07-14 after the Bajaj-Auto incident — a decision-sized bump
# on a single-bar-sensitive slope was admitting fragile pre-breakouts. See
# CHANGELOG 2026-07-14.
VELOCITY_MARGIN_BONUS: float = 0.05    # tunable — ±5% of [0,1] margin range


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    # +1 so we can exclude today's bar from the dry-up windows below.
    min_bars = max(ADV_LONG_WINDOW + 1, DIVERGENCE_LOOKBACK + 2)
    if df is None or df.empty or len(df) < min_bars:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {min_bars} bars)",
            fix_point="backend/stages/volume.py: ingest must produce enough bars",
        )

    # SETUP vs TRIGGER separation.
    # [VD] describes the quiet days BEFORE the breakout (supply dried up).
    # [BR] describes the breakout day itself (heavy volume).
    # If we include today's bar in the dry-up window, today's breakout volume
    # mathematically prevents [VD] from firing — the gates cannibalise each
    # other. So we evaluate the dry-up on the PRIOR bars only.
    prior_vol = df["Volume"].iloc[:-1]
    overrides: dict = getattr(ctx, "overrides", {}) or {}
    dryup_max = float(overrides.get("vd_dryup_ratio", VOLUME_DRYUP_RATIO_MAX))

    adv_recent = adv(prior_vol, ADV_RECENT_WINDOW)
    adv_long = adv(prior_vol, ADV_LONG_WINDOW)

    div = obv_bullish_divergence(df, lookback=DIVERGENCE_LOOKBACK)

    inflection, short_slope, long_slope = obv_flow_inflection(
        df["Close"], df["Volume"],
        short=VELOCITY_SHORT_WIN, long=VELOCITY_LONG_WIN,
    )

    ratio = None
    if adv_recent is not None and adv_long is not None and adv_long > 0:
        ratio = adv_recent / adv_long

    features = {
        "adv_5d": round(adv_recent, 0) if adv_recent is not None else None,
        "adv_50d": round(adv_long, 0) if adv_long is not None else None,
        "vol_ratio_5_50": round(ratio, 3) if ratio is not None else None,
        "divergence": {
            "is_bullish": div.is_bullish,
            "form": div.form,
            "price_low_early": div.price_low_early,
            "price_low_recent": div.price_low_recent,
            "obv_at_early_low": div.obv_at_early_low,
            "obv_at_recent_low": div.obv_at_recent_low,
            "detail": div.detail,
        },
        "obv_flow_inflection": inflection,
        "obv_slope_short_pct": (
            round(short_slope, 2) if short_slope is not None else None
        ),
        "obv_slope_long_pct": (
            round(long_slope, 2) if long_slope is not None else None
        ),
    }

    evidence: list[str] = []
    near_misses: list[str] = []

    # ---- Check 1: volume dry-up (evaluated on prior bars, not today) ----
    dryup_pass = ratio is not None and ratio < dryup_max
    if dryup_pass:
        evidence.append(
            f"prior 5d/50d vol ratio {ratio*100:.0f}% < {dryup_max*100:.0f}% "
            f"(supply thinning before breakout)"
        )
    elif ratio is None:
        near_misses.append("volume ratio unavailable")
    else:
        near_misses.append(
            f"prior 5d/50d vol ratio {ratio*100:.0f}% >= {dryup_max*100:.0f}% "
            f"(no dry-up)"
        )

    # ---- Check 2: bullish OBV-price divergence ----
    div_pass = bool(div.is_bullish)
    if div_pass:
        evidence.append(f"bullish OBV divergence ({div.form}): {div.detail}")
    else:
        near_misses.append(f"no bullish OBV-price divergence ({div.detail})")

    # ---- Decision + margin for ranker ----
    # OR semantics: pass if either check fires. Margin still averages both
    # so stocks hitting both rank above stocks hitting only one.
    passed = dryup_pass or div_pass
    margin = 0.0
    if passed:
        dryup_margin = (
            max(0.0, (dryup_max - ratio) / dryup_max)
            if ratio is not None else 0.0
        )
        div_margin = (
            1.0 if div.form == "classic"
            else (0.5 if div.form == "flat-price" else 0.0)
        )
        margin = (dryup_margin + div_margin) / 2.0

        # OBV flow-velocity adjustment. Bounded tilt within [0, 1].
        if inflection == "healing":
            margin = min(1.0, margin + VELOCITY_MARGIN_BONUS)
            evidence.append(
                f"flow inflection healing "
                f"(OBV {VELOCITY_SHORT_WIN}d {short_slope:+.1f}% > 0, "
                f"{VELOCITY_LONG_WIN}d {long_slope:+.1f}% < 0)"
            )
        elif inflection == "hemorrhaging":
            margin = max(0.0, margin - VELOCITY_MARGIN_BONUS)
            evidence.append(
                f"flow inflection hemorrhaging "
                f"(OBV {VELOCITY_SHORT_WIN}d {short_slope:+.1f}%, "
                f"{VELOCITY_LONG_WIN}d {long_slope:+.1f}%)"
            )

    return StageResult(
        stage_id=stage_id,
        passed=passed,
        score=round(margin, 4) if passed else 0.0,
        features=features,
        evidence=evidence if passed else near_misses,
        fix_point="backend/stages/volume.py — constants at top",
        reason=(
            "passed (dry-up OR bullish divergence)" if passed
            else "; ".join(near_misses)
        ),
    )
