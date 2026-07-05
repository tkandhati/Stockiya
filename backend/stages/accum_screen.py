"""[ACS] Accumulation Screen — tier-1 cheap gate.

Runs on ~45 bars. No cumulative indicators, no ADI computation. Purpose:
decide whether a ticker is worth pulling the full 180 bars for [AC] tier-2
analysis. Every ticker in the universe hits this gate every day.

Two checks, both must pass on the last EOD bar:

  1. Range over the last W bars is tight        (coiling)
  2. Volume in the last W bars is dry           (institutions absorbing)

Together: a stock coiling on shrinking volume — the pre-condition for a
Wyckoff-style accumulation base. Cheap enough to run universe-wide daily.

Fix points:
    ACCUM_WINDOW              : bars in the tightness window (default 20)
    TIGHT_RANGE_PCT_MAX       : range as % of mean close (default 0.10)
    VOLUME_DRY_MULT           : recent vol vs prior vol (default 0.95)
    MIN_ADV_SHARES            : liquidity floor before we trust the signal

All logic is pure and deterministic. No I/O, no network, no LLM. Same
DataFrame in → same StageResult out. Tuner imports this directly.
"""

from __future__ import annotations

from ..indicators import adaptive_windows, adv, range_pct_window, vol_dryness_ratio
from ..pipeline import PipelineContext, StageResult

stage_id = "ACS"

# --------------------------------------------------------------------------- #
# Tunable thresholds
#
# Multi-window scan: each ticker gets its own triplet of windows, sized by
# realized volatility (see indicators.adaptive_windows). High-vol stocks
# compress in shorter timeframes and get windows like (5, 10, 20); low-vol
# stocks build longer bases and get windows like (20, 40, 60). We sweep all
# three and take the max margin — provably non-decreasing vs single-W.
#
# Set `acs_windows` in overrides (backtest) to force a specific triplet.
# --------------------------------------------------------------------------- #

ACCUM_WINDOW_BASE: int = 20           # tunable — anchor for adaptive_windows()
ACCUM_WINDOW_MAX_CAP: int = 60        # tunable — used for min-bars gating
TIGHT_RANGE_PCT_MAX: float = 0.10     # tunable — 8% too tight for mid-caps
VOLUME_DRY_MULT: float = 0.95         # tunable — strict 0.85 rarely fires
MIN_ADV_SHARES: float = 200_000       # tunable — liquidity floor
ADV_WINDOW: int = 50                  # tunable — window for ADV floor check


def _score_at_window(
    df, window: int, range_max: float, vol_dry_max: float,
) -> tuple[bool, float, dict, list[str]]:
    """Compute tight-range + vol-dryness pass/fail + margin at one W.

    Returns (passed, margin, features, evidence_or_failures).
    Pure — no side effects; caller decides which window's result wins.
    """
    if len(df) < 2 * window + 5:
        return (False, 0.0,
                {"window": window, "range_pct": None, "vol_ratio": None},
                [f"insufficient bars for W={window}"])
    range_pct = range_pct_window(df, window)
    vol_ratio = vol_dryness_ratio(df["Volume"], window)
    feat = {
        "window": window,
        "range_pct": round(range_pct, 4) if range_pct is not None else None,
        "vol_ratio": round(vol_ratio, 4) if vol_ratio is not None else None,
    }
    fails: list[str] = []
    lines: list[str] = []
    if range_pct is None:
        fails.append(f"[W={window}] range_pct unavailable")
    elif range_pct > range_max:
        fails.append(f"[W={window}] range {range_pct*100:.2f}% > {range_max*100:.1f}%")
    else:
        lines.append(f"[W={window}] range {range_pct*100:.2f}% <= {range_max*100:.1f}%")
    if vol_ratio is None:
        fails.append(f"[W={window}] vol_ratio unavailable")
    elif vol_ratio > vol_dry_max:
        fails.append(f"[W={window}] vol {vol_ratio:.2f}x > {vol_dry_max:.2f}x")
    else:
        lines.append(f"[W={window}] vol {vol_ratio:.2f}x <= {vol_dry_max:.2f}x")
    passed = not fails
    margin = 0.0
    if passed and range_pct is not None and vol_ratio is not None:
        margin = (
            max(0.0, 1.0 - range_pct / range_max)
            + max(0.0, 1.0 - vol_ratio / vol_dry_max)
        ) / 2.0
    return (passed, margin, feat, lines if passed else fails)


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    # Anchor min-bars to the widest window the adaptive scan could pick, so
    # short-history tickers fail fast rather than get a partial answer at some
    # windows but not others.
    min_bars = 2 * ACCUM_WINDOW_MAX_CAP + 5
    if df is None or df.empty or len(df) < min_bars:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {min_bars} bars)",
            fix_point="backend/stages/accum_screen.py: ACCUM_WINDOW_MAX_CAP",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    windows_override = overrides.get("acs_windows")
    if windows_override:
        windows = tuple(int(w) for w in windows_override)
    else:
        # Adaptive per-ticker triplet, sized by realized ATR.
        windows = adaptive_windows(df, base=ACCUM_WINDOW_BASE, w_max=ACCUM_WINDOW_MAX_CAP)
    range_max = float(overrides.get("acs_range_pct_max", TIGHT_RANGE_PCT_MAX))
    vol_dry_max = float(overrides.get("acs_vol_dry_mult", VOLUME_DRY_MULT))
    min_adv = float(overrides.get("acs_min_adv_shares", MIN_ADV_SHARES))

    adv_long = adv(df["Volume"], ADV_WINDOW)

    # ---- Liquidity floor — checked once, independent of window ----
    liquidity_fail: list[str] = []
    liquidity_line: list[str] = []
    if adv_long is None:
        liquidity_fail.append("adv(50) unavailable")
    elif adv_long < min_adv:
        liquidity_fail.append(f"adv(50) {adv_long:,.0f} < {min_adv:,.0f} liquidity floor")
    else:
        liquidity_line.append(f"adv(50) {adv_long:,.0f} >= {min_adv:,.0f}")

    if liquidity_fail:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"adv_50d": round(adv_long, 0) if adv_long is not None else None,
                      "windows_scanned": list(windows)},
            evidence=liquidity_fail,
            fix_point="backend/stages/accum_screen.py — MIN_ADV_SHARES",
            reason="; ".join(liquidity_fail),
        )

    # ---- Multi-window sweep — take the best margin ----
    per_window: list[tuple[bool, float, dict, list[str]]] = [
        _score_at_window(df, W, range_max, vol_dry_max) for W in windows
    ]
    any_passed = any(p for p, _, _, _ in per_window)

    if not any_passed:
        # Report every W's failure so the trader sees the whole surface.
        all_fails = liquidity_line[:]
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
            fix_point="backend/stages/accum_screen.py — constants at top",
            reason="no window passed both checks",
        )

    # Pick the window with the highest margin among those that passed.
    best_idx = max(range(len(per_window)), key=lambda i: per_window[i][1] if per_window[i][0] else -1.0)
    _, best_margin, best_feat, best_lines = per_window[best_idx]

    features = {
        "adv_50d": round(adv_long, 0),
        "windows_scanned": list(windows),
        "best_window": best_feat["window"],
        "range_pct": best_feat["range_pct"],
        "vol_ratio": best_feat["vol_ratio"],
        "per_window": [f for _, _, f, _ in per_window],
    }
    evidence = liquidity_line + best_lines

    return StageResult(
        stage_id=stage_id,
        passed=True,
        score=round(best_margin, 4),
        features=features,
        evidence=evidence,
        fix_point="backend/stages/accum_screen.py — constants at top",
        reason=f"passed at W={best_feat['window']} (best of {list(windows)})",
    )
