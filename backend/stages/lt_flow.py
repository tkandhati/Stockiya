"""[LT] Long-Term Institutional Flow gate.

The 6-month-hold evidence check. Before we accept any consolidation +
breakout signature, we require months of accumulation behind it.

Three checks; ALL must pass:
  1. OBV-90d slope >= +3 %             (sustained cumulative buying)
  2. Up/down volume ratio (90d) >= 1.1 (mild net buying over 3 months)
  3. 150d MA slope over 50 bars >= 0 % (long-term floor rising)

Cheap; runs early. Halts any stock that is technically tight today but
has no multi-month institutional fingerprint.

Fix points:
    OBV_90D_SLOPE_MIN     : OBV-90d slope minimum, %       (default 3.0)
    UPDOWN_90D_MIN        : up/down vol ratio minimum      (default 1.1)
    MA150_SLOPE_MIN       : 150d MA slope minimum, %       (default 0.0)
    MA150_SLOPE_LOOKBACK  : bars to look back for slope    (default 50)
"""

from __future__ import annotations

from ..indicators import obv, obv_slope_pct, sma_slope_pct, up_down_vol_ratio
from ..pipeline import PipelineContext, StageResult

stage_id = "LT"

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

OBV_90D_SLOPE_MIN: float = 3.0          # tunable
UPDOWN_90D_MIN: float = 1.1             # tunable
MA150_SLOPE_MIN: float = 0.0            # tunable
MA150_SLOPE_LOOKBACK: int = 50          # tunable


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    min_bars = 150 + MA150_SLOPE_LOOKBACK
    if df is None or df.empty or len(df) < min_bars:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {min_bars} bars)",
            fix_point="backend/stages/lt_flow.py: ingest must produce enough bars",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    obv_slope_min = float(overrides.get("lt_obv_90d_slope_min", OBV_90D_SLOPE_MIN))

    close = df["Close"]
    volume = df["Volume"]

    obv_series = obv(close, volume)
    obv90 = obv_slope_pct(obv_series, 90)
    ud90 = up_down_vol_ratio(close, volume, 90)
    ma_slope = sma_slope_pct(close, 150, MA150_SLOPE_LOOKBACK)

    features = {
        "obv_90d_slope_pct": round(obv90, 2) if obv90 is not None else None,
        "up_down_vol_ratio_90d": round(ud90, 3) if ud90 is not None else None,
        "ma150_slope_pct": round(ma_slope, 3) if ma_slope is not None else None,
    }

    evidence: list[str] = []
    failures: list[str] = []

    if obv90 is None:
        failures.append("OBV-90d slope unavailable")
    elif obv90 < obv_slope_min:
        failures.append(
            f"OBV-90d slope {obv90:+.1f}% < {obv_slope_min:.1f}% "
            "(insufficient long-term flow)"
        )
    else:
        evidence.append(f"OBV-90d slope {obv90:+.1f}% >= {obv_slope_min:.1f}%")

    if ud90 is None:
        failures.append("up/down vol ratio 90d unavailable")
    elif ud90 < UPDOWN_90D_MIN:
        failures.append(
            f"up/down vol 90d {ud90:.2f}x < {UPDOWN_90D_MIN:.2f}x "
            "(no 3-month net buying)"
        )
    else:
        evidence.append(f"up/down vol 90d {ud90:.2f}x >= {UPDOWN_90D_MIN:.2f}x")

    if ma_slope is None:
        failures.append("150d MA slope unavailable")
    elif ma_slope < MA150_SLOPE_MIN:
        failures.append(
            f"150d MA slope {ma_slope:+.2f}% < {MA150_SLOPE_MIN:.2f}% "
            "(long-term floor not rising)"
        )
    else:
        evidence.append(f"150d MA slope {ma_slope:+.2f}% >= {MA150_SLOPE_MIN:.2f}%")

    passed = len(failures) == 0
    margin = 0.0
    if passed and obv90 is not None and ud90 is not None and ma_slope is not None:
        # Normalize each into [0, 1]; "strong" long-term flow gets full credit.
        obv_margin = min(1.0, max(0.0, (obv90 - obv_slope_min) / 10.0))
        ud_margin = min(1.0, max(0.0, (ud90 - UPDOWN_90D_MIN) / 0.4))
        ma_margin = min(1.0, max(0.0, (ma_slope - MA150_SLOPE_MIN) / 5.0))
        margin = (obv_margin + ud_margin + ma_margin) / 3.0

    return StageResult(
        stage_id=stage_id,
        passed=passed,
        score=round(margin, 4) if passed else 0.0,
        features=features,
        evidence=evidence if passed else failures,
        fix_point="backend/stages/lt_flow.py — constants at top",
        reason=("passed all 3 checks" if passed else "; ".join(failures)),
    )
