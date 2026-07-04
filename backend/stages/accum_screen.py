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

from ..indicators import adv, range_pct_window, vol_dryness_ratio
from ..pipeline import PipelineContext, StageResult

stage_id = "ACS"

# --------------------------------------------------------------------------- #
# Tunable thresholds (empirical defaults from window-sweep on cached bars)
# --------------------------------------------------------------------------- #

ACCUM_WINDOW: int = 20                # tunable — 20 was the only window with
                                      # all-checks pass-rate > 0 in sweep
TIGHT_RANGE_PCT_MAX: float = 0.10     # tunable — 8% too tight for mid-caps
VOLUME_DRY_MULT: float = 0.95         # tunable — strict 0.85 rarely fires
MIN_ADV_SHARES: float = 200_000       # tunable — liquidity floor
ADV_WINDOW: int = 50                  # tunable — window for ADV floor check


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    min_bars = 2 * ACCUM_WINDOW + 5    # need prior W for dryness comparison
    if df is None or df.empty or len(df) < min_bars:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {min_bars} bars)",
            fix_point="backend/stages/accum_screen.py: ACCUM_WINDOW",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    window = int(overrides.get("acs_window", ACCUM_WINDOW))
    range_max = float(overrides.get("acs_range_pct_max", TIGHT_RANGE_PCT_MAX))
    vol_dry_max = float(overrides.get("acs_vol_dry_mult", VOLUME_DRY_MULT))
    min_adv = float(overrides.get("acs_min_adv_shares", MIN_ADV_SHARES))

    range_pct = range_pct_window(df, window)
    vol_ratio = vol_dryness_ratio(df["Volume"], window)
    adv_long = adv(df["Volume"], ADV_WINDOW)

    features = {
        "window": window,
        "range_pct": round(range_pct, 4) if range_pct is not None else None,
        "vol_ratio": round(vol_ratio, 4) if vol_ratio is not None else None,
        "adv_50d": round(adv_long, 0) if adv_long is not None else None,
    }

    evidence: list[str] = []
    failures: list[str] = []

    # ---- Liquidity floor first — noisy signals on thin volume ----
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
            f"range {range_pct*100:.2f}% over {window}d > {range_max*100:.1f}% "
            f"(not coiling)"
        )
    else:
        evidence.append(
            f"range {range_pct*100:.2f}% over {window}d <= {range_max*100:.1f}%"
        )

    # ---- Check 2: volume dryness ----
    if vol_ratio is None:
        failures.append("vol_ratio unavailable")
    elif vol_ratio > vol_dry_max:
        failures.append(
            f"vol {vol_ratio:.2f}x prior {window}d > {vol_dry_max:.2f}x "
            f"(not drying up)"
        )
    else:
        evidence.append(
            f"vol {vol_ratio:.2f}x prior {window}d <= {vol_dry_max:.2f}x"
        )

    passed = len(failures) == 0

    # Margin score: how tight is the coil, how dry the volume — average of the
    # two normalized headrooms. Only meaningful when passed.
    margin = 0.0
    if passed and range_pct is not None and vol_ratio is not None:
        # Range: 0% range = full credit, at threshold = 0.
        range_margin = max(0.0, 1.0 - range_pct / range_max)
        # Volume: ratio 0 = full credit, at threshold = 0.
        vol_margin = max(0.0, 1.0 - vol_ratio / vol_dry_max)
        margin = (range_margin + vol_margin) / 2.0

    return StageResult(
        stage_id=stage_id,
        passed=passed,
        score=round(margin, 4) if passed else 0.0,
        features=features,
        evidence=evidence if passed else failures,
        fix_point="backend/stages/accum_screen.py — constants at top",
        reason=("passed both checks" if passed else "; ".join(failures)),
    )
