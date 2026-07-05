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
    adaptive_windows,
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

ACCUM_WINDOW_BASE: int = 20      # tunable — anchor for adaptive_windows()
ACCUM_WINDOW_MAX_CAP: int = 60   # tunable — cap for adaptive triplet
TIER2_BARS_REQUIRED: int = 180        # tunable — stability floor for ADI slope
TIGHT_RANGE_PCT_MAX: float = 0.10     # tunable
VOLUME_DRY_MULT: float = 0.95         # tunable
PRICE_SLOPE_MAX_ABS: float = 0.002    # tunable — normalized slope threshold
ADI_SLOPE_MIN: float = 0.005          # tunable — normalized slope threshold
MIN_ADV_SHARES: float = 200_000       # tunable — liquidity floor
ADV_WINDOW: int = 50                  # tunable


def _score_at_window(
    df, W: int, range_max: float, vol_dry_max: float,
    price_slope_max: float, adi_slope_min: float,
) -> tuple[bool, float, dict, list[str]]:
    """All three AC checks at one W. Returns (passed, margin, features, msgs).

    Pure — no side effects. Caller picks the winning window.
    """
    if len(df) < 2 * W + 5:
        return (False, 0.0,
                {"window": W}, [f"[W={W}] insufficient bars"])
    range_pct = range_pct_window(df, W)
    vol_ratio = vol_dryness_ratio(df["Volume"], W)
    price_slope = norm_slope(df["Close"], W)
    adi_series = adi(df)
    adi_slope = norm_slope(adi_series, W) if adi_series is not None else None
    div_strength = (
        adi_slope - price_slope
        if price_slope is not None and adi_slope is not None
        else None
    )
    feat = {
        "window": W,
        "range_pct": round(range_pct, 4) if range_pct is not None else None,
        "vol_ratio": round(vol_ratio, 4) if vol_ratio is not None else None,
        "price_slope": round(price_slope, 6) if price_slope is not None else None,
        "adi_slope": round(adi_slope, 6) if adi_slope is not None else None,
        "divergence_strength": round(div_strength, 6) if div_strength is not None else None,
    }
    fails: list[str] = []
    lines: list[str] = []
    # Check 1: tight range
    if range_pct is None:
        fails.append(f"[W={W}] range_pct unavailable")
    elif range_pct > range_max:
        fails.append(f"[W={W}] range {range_pct*100:.2f}% > {range_max*100:.1f}%")
    else:
        lines.append(f"[W={W}] range {range_pct*100:.2f}% <= {range_max*100:.1f}%")
    # Check 2: volume dryness
    if vol_ratio is None:
        fails.append(f"[W={W}] vol_ratio unavailable")
    elif vol_ratio > vol_dry_max:
        fails.append(f"[W={W}] vol {vol_ratio:.2f}x > {vol_dry_max:.2f}x")
    else:
        lines.append(f"[W={W}] vol {vol_ratio:.2f}x <= {vol_dry_max:.2f}x")
    # Check 3: ADI positive divergence
    if price_slope is None or adi_slope is None:
        fails.append(f"[W={W}] divergence unavailable")
    else:
        price_flat = abs(price_slope) <= price_slope_max
        adi_rising = adi_slope >= adi_slope_min
        if price_flat and adi_rising:
            lines.append(
                f"[W={W}] divergence: price {price_slope:+.4f} flat AND ADI {adi_slope:+.4f} rising"
            )
        else:
            reasons = []
            if not price_flat:
                reasons.append(f"price_slope {price_slope:+.4f} not flat")
            if not adi_rising:
                reasons.append(f"adi_slope {adi_slope:+.4f} not rising")
            fails.append(f"[W={W}] no divergence: " + "; ".join(reasons))
    passed = not fails
    margin = 0.0
    if passed and range_pct is not None and vol_ratio is not None and div_strength is not None:
        r_m = max(0.0, 1.0 - range_pct / range_max)
        v_m = max(0.0, 1.0 - vol_ratio / vol_dry_max)
        d_m = min(1.0, max(0.0, div_strength / (2 * adi_slope_min)))
        margin = (r_m + v_m + d_m) / 3.0
    return (passed, margin, feat, lines if passed else fails)


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    if df is None or df.empty or len(df) < TIER2_BARS_REQUIRED:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {TIER2_BARS_REQUIRED} bars)",
            fix_point="backend/stages/accumulation.py: TIER2_BARS_REQUIRED",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    windows_override = overrides.get("ac_windows")
    if windows_override:
        windows = tuple(int(w) for w in windows_override)
    else:
        # Adaptive per-ticker triplet, sized by realized ATR.
        windows = adaptive_windows(df, base=ACCUM_WINDOW_BASE, w_max=ACCUM_WINDOW_MAX_CAP)
    range_max = float(overrides.get("ac_range_pct_max", TIGHT_RANGE_PCT_MAX))
    vol_dry_max = float(overrides.get("ac_vol_dry_mult", VOLUME_DRY_MULT))
    price_slope_max = float(overrides.get("ac_price_slope_max_abs", PRICE_SLOPE_MAX_ABS))
    adi_slope_min = float(overrides.get("ac_adi_slope_min", ADI_SLOPE_MIN))
    min_adv = float(overrides.get("ac_min_adv_shares", MIN_ADV_SHARES))

    adv_long = adv(df["Volume"], ADV_WINDOW)

    # Liquidity floor — checked once, independent of window
    if adv_long is None:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"adv_50d": None, "windows_scanned": list(windows)},
            evidence=["adv(50) unavailable"],
            fix_point="backend/stages/accumulation.py — MIN_ADV_SHARES",
            reason="adv(50) unavailable",
        )
    if adv_long < min_adv:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"adv_50d": round(adv_long, 0), "windows_scanned": list(windows)},
            evidence=[f"adv(50) {adv_long:,.0f} < {min_adv:,.0f} liquidity floor"],
            fix_point="backend/stages/accumulation.py — MIN_ADV_SHARES",
            reason=f"adv(50) {adv_long:,.0f} < {min_adv:,.0f}",
        )
    liq_line = f"adv(50) {adv_long:,.0f} >= {min_adv:,.0f}"

    # Multi-window sweep — take the best margin
    per_window = [
        _score_at_window(df, W, range_max, vol_dry_max, price_slope_max, adi_slope_min)
        for W in windows
    ]
    any_passed = any(p for p, _, _, _ in per_window)

    if not any_passed:
        all_fails = [liq_line]
        for _, _, _, msgs in per_window:
            all_fails.extend(msgs)
        return StageResult(
            stage_id=stage_id, passed=False,
            features={
                "adv_50d": round(adv_long, 0),
                "windows_scanned": list(windows),
                "per_window": [f for _, _, f, _ in per_window],
            },
            evidence=all_fails,
            fix_point="backend/stages/accumulation.py — constants at top",
            reason="no window passed all 3 checks",
        )

    best_idx = max(range(len(per_window)), key=lambda i: per_window[i][1] if per_window[i][0] else -1.0)
    _, best_margin, best_feat, best_lines = per_window[best_idx]

    features = {
        "adv_50d": round(adv_long, 0),
        "windows_scanned": list(windows),
        "best_window": best_feat["window"],
        "range_pct": best_feat.get("range_pct"),
        "vol_ratio": best_feat.get("vol_ratio"),
        "price_slope": best_feat.get("price_slope"),
        "adi_slope": best_feat.get("adi_slope"),
        "divergence_strength": best_feat.get("divergence_strength"),
        "per_window": [f for _, _, f, _ in per_window],
    }

    return StageResult(
        stage_id=stage_id,
        passed=True,
        score=round(best_margin, 4),
        features=features,
        evidence=[liq_line] + best_lines,
        fix_point="backend/stages/accumulation.py — constants at top",
        reason=f"passed at W={best_feat['window']} (best of {list(windows)})",
    )
