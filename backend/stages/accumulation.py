"""[AC] Accumulation — tier-2 full gate with ADI positive divergence.

Runs only on tickers that already passed [ACS] tier-1. Consumes 180 bars of
history. Purpose: confirm the coil-plus-quiet pattern is being driven by
underlying accumulation (smart-money footprint), not just a lull.

Three checks, all must pass on the last EOD bar:

  1. Range over W bars is tight             (re-check, same as [ACS])
  2. Volume in W bars is dry                (re-check, same as [ACS])
  3. ADI positive divergence:               (the new signal)
        price_slope over W    is ~flat
        AND ADI_slope over W  is rising

Together: coiling on shrinking volume WITH a rising accumulation/distribution
line = institutions absorbing without moving the tape. The "quiet buying"
signature.

Fix points:
    ACCUM_WINDOW              : bars for all slope/range checks (default 20)
    TIER2_BARS_REQUIRED       : minimum history for stable ADI + slope fit
    TIGHT_RANGE_PCT_MAX       : range as % of mean close (default 0.10)
    VOLUME_DRY_MULT           : recent vol vs prior vol (default 0.95)
    PRICE_SLOPE_MAX_ABS       : max |price slope| for "flat" (default 0.002)
    ADI_SLOPE_MIN             : min ADI slope for "rising" (default 0.005)

All logic is pure and deterministic. Same DataFrame in → same StageResult out.
Tuner imports this directly for backtesting/threshold sweeps.
"""

from __future__ import annotations

from ..indicators import (
    adi,
    adv,
    norm_slope,
    range_pct_window,
    vol_dryness_ratio,
)
from ..pipeline import PipelineContext, StageResult

stage_id = "AC"

# --------------------------------------------------------------------------- #
# Tunable thresholds (empirical defaults from window-sweep on cached bars)
# --------------------------------------------------------------------------- #

ACCUM_WINDOW: int = 20                # tunable — sweep-validated winner
TIER2_BARS_REQUIRED: int = 180        # tunable — stability floor for ADI slope
TIGHT_RANGE_PCT_MAX: float = 0.10     # tunable
VOLUME_DRY_MULT: float = 0.95         # tunable
PRICE_SLOPE_MAX_ABS: float = 0.002    # tunable — normalized slope threshold
ADI_SLOPE_MIN: float = 0.005          # tunable — normalized slope threshold
MIN_ADV_SHARES: float = 200_000       # tunable — liquidity floor
ADV_WINDOW: int = 50                  # tunable


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    if df is None or df.empty or len(df) < TIER2_BARS_REQUIRED:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {TIER2_BARS_REQUIRED} bars)",
            fix_point="backend/stages/accumulation.py: TIER2_BARS_REQUIRED",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    W = int(overrides.get("ac_window", ACCUM_WINDOW))
    range_max = float(overrides.get("ac_range_pct_max", TIGHT_RANGE_PCT_MAX))
    vol_dry_max = float(overrides.get("ac_vol_dry_mult", VOLUME_DRY_MULT))
    price_slope_max = float(overrides.get("ac_price_slope_max_abs", PRICE_SLOPE_MAX_ABS))
    adi_slope_min = float(overrides.get("ac_adi_slope_min", ADI_SLOPE_MIN))
    min_adv = float(overrides.get("ac_min_adv_shares", MIN_ADV_SHARES))

    range_pct = range_pct_window(df, W)
    vol_ratio = vol_dryness_ratio(df["Volume"], W)
    adv_long = adv(df["Volume"], ADV_WINDOW)

    price_slope = norm_slope(df["Close"], W)
    adi_series = adi(df)
    adi_slope = norm_slope(adi_series, W) if adi_series is not None else None

    divergence_strength = None
    if price_slope is not None and adi_slope is not None:
        divergence_strength = adi_slope - price_slope

    features = {
        "window": W,
        "range_pct": round(range_pct, 4) if range_pct is not None else None,
        "vol_ratio": round(vol_ratio, 4) if vol_ratio is not None else None,
        "price_slope": round(price_slope, 6) if price_slope is not None else None,
        "adi_slope": round(adi_slope, 6) if adi_slope is not None else None,
        "divergence_strength": (
            round(divergence_strength, 6) if divergence_strength is not None else None
        ),
        "adv_50d": round(adv_long, 0) if adv_long is not None else None,
    }

    evidence: list[str] = []
    failures: list[str] = []

    # ---- Liquidity floor ----
    if adv_long is None:
        failures.append("adv(50) unavailable")
    elif adv_long < min_adv:
        failures.append(
            f"adv(50) {adv_long:,.0f} < {min_adv:,.0f} liquidity floor"
        )
    else:
        evidence.append(f"adv(50) {adv_long:,.0f} >= {min_adv:,.0f}")

    # ---- Check 1: tight range ----
    if range_pct is None:
        failures.append("range_pct unavailable")
    elif range_pct > range_max:
        failures.append(
            f"range {range_pct*100:.2f}% over {W}d > {range_max*100:.1f}% (not coiling)"
        )
    else:
        evidence.append(
            f"range {range_pct*100:.2f}% over {W}d <= {range_max*100:.1f}%"
        )

    # ---- Check 2: volume dryness ----
    if vol_ratio is None:
        failures.append("vol_ratio unavailable")
    elif vol_ratio > vol_dry_max:
        failures.append(
            f"vol {vol_ratio:.2f}x prior {W}d > {vol_dry_max:.2f}x (not drying up)"
        )
    else:
        evidence.append(
            f"vol {vol_ratio:.2f}x prior {W}d <= {vol_dry_max:.2f}x"
        )

    # ---- Check 3: ADI positive divergence ----
    if price_slope is None or adi_slope is None:
        failures.append("divergence unavailable (slope inputs missing)")
    else:
        price_flat = abs(price_slope) <= price_slope_max
        adi_rising = adi_slope >= adi_slope_min

        if price_flat and adi_rising:
            evidence.append(
                f"divergence: price_slope {price_slope:+.4f} (|.| <= {price_slope_max}) "
                f"AND adi_slope {adi_slope:+.4f} (>= {adi_slope_min})"
            )
        else:
            reasons = []
            if not price_flat:
                reasons.append(f"price_slope {price_slope:+.4f} not flat (|.| > {price_slope_max})")
            if not adi_rising:
                reasons.append(f"adi_slope {adi_slope:+.4f} not rising (< {adi_slope_min})")
            failures.append("no divergence: " + "; ".join(reasons))

    passed = len(failures) == 0

    # Margin: average of the three normalized headrooms.
    margin = 0.0
    if (
        passed
        and range_pct is not None
        and vol_ratio is not None
        and divergence_strength is not None
    ):
        range_margin = max(0.0, 1.0 - range_pct / range_max)
        vol_margin = max(0.0, 1.0 - vol_ratio / vol_dry_max)
        # Divergence: strength / (2 * min threshold) saturates at 1.0
        div_margin = min(1.0, max(0.0, divergence_strength / (2 * adi_slope_min)))
        margin = (range_margin + vol_margin + div_margin) / 3.0

    return StageResult(
        stage_id=stage_id,
        passed=passed,
        score=round(margin, 4) if passed else 0.0,
        features=features,
        evidence=evidence if passed else failures,
        fix_point="backend/stages/accumulation.py — constants at top",
        reason=("passed all 3 checks" if passed else "; ".join(failures)),
    )
