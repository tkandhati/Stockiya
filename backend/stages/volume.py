"""[VD] Volume + Divergence gate.

Two checks; BOTH must pass:

  1. Volume Dry-Up (Minervini):   adv(5) / adv(50) < 0.50
     5-day average volume is less than half the 50-day average — supply
     exhausted near support.

  2. Bullish OBV-price divergence over the last 20 bars:
       classic     = price made a lower low while OBV made a higher low
       flat-price  = price held +/-2 % while OBV climbed >= 2 %

Fix points:
    VOLUME_DRYUP_RATIO_MAX  : max recent/long ADV ratio (default 0.50)
    ADV_RECENT_WINDOW       : recent-volume window (default 5)
    ADV_LONG_WINDOW         : long-volume window (default 50)
    DIVERGENCE_LOOKBACK     : bars for divergence detection (default 20)
"""

from __future__ import annotations

from ..indicators import adv, obv_bullish_divergence
from ..pipeline import PipelineContext, StageResult

stage_id = "VD"

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

VOLUME_DRYUP_RATIO_MAX: float = 0.50   # tunable
ADV_RECENT_WINDOW: int = 5             # tunable
ADV_LONG_WINDOW: int = 50              # tunable
DIVERGENCE_LOOKBACK: int = 20          # tunable


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    min_bars = max(ADV_LONG_WINDOW, DIVERGENCE_LOOKBACK + 2)
    if df is None or df.empty or len(df) < min_bars:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {min_bars} bars)",
            fix_point="backend/stages/volume.py: ingest must produce enough bars",
        )

    vol = df["Volume"]
    adv_recent = adv(vol, ADV_RECENT_WINDOW)
    adv_long = adv(vol, ADV_LONG_WINDOW)

    div = obv_bullish_divergence(df, lookback=DIVERGENCE_LOOKBACK)

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
    }

    evidence: list[str] = []
    failures: list[str] = []

    # ---- Check 1: volume dry-up ----
    if ratio is None:
        failures.append("volume ratio unavailable")
    elif ratio >= VOLUME_DRYUP_RATIO_MAX:
        failures.append(
            f"5d/50d vol ratio {ratio*100:.0f}% >= {VOLUME_DRYUP_RATIO_MAX*100:.0f}% "
            f"(no dry-up; institutions not yet quiet)"
        )
    else:
        evidence.append(
            f"5d/50d vol ratio {ratio*100:.0f}% < {VOLUME_DRYUP_RATIO_MAX*100:.0f}% "
            f"(supply exhausted)"
        )

    # ---- Check 2: bullish OBV-price divergence ----
    if not div.is_bullish:
        failures.append(f"no bullish OBV-price divergence ({div.detail})")
    else:
        evidence.append(f"bullish OBV divergence ({div.form}): {div.detail}")

    # ---- Decision + margin for ranker ----
    passed = len(failures) == 0
    margin = 0.0
    if passed and ratio is not None:
        dryup_margin = max(
            0.0, (VOLUME_DRYUP_RATIO_MAX - ratio) / VOLUME_DRYUP_RATIO_MAX
        )
        # Classic divergence is the stronger form; flat-price is partial credit.
        div_margin = (
            1.0 if div.form == "classic"
            else (0.5 if div.form == "flat-price" else 0.0)
        )
        margin = (dryup_margin + div_margin) / 2.0

    return StageResult(
        stage_id=stage_id,
        passed=passed,
        score=round(margin, 4) if passed else 0.0,
        features=features,
        evidence=evidence if passed else failures,
        fix_point="backend/stages/volume.py — constants at top",
        reason=("passed both checks" if passed else "; ".join(failures)),
    )
