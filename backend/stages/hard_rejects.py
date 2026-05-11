"""[HR] Hard Rejects — fail-fast kill conditions.

Per PRINCIPLES.md §0 "Hard rejects":
  - Wyckoff Distribution or Markdown phase
  - Stan Weinstein Stage 4 decline
  - 30-week MA sloping down
  - OBV (90d) negative AND CMF (60d) negative
  - Parabolic 30-day moves (>+25%)

Any one fires → reject. Cheap to evaluate, so it runs before scoring stages
to short-circuit obviously-bad candidates.
"""

from __future__ import annotations

from ..pipeline import PipelineContext, StageResult

stage_id = "HR"

# Thresholds — replace numbers to retune without touching code.
PARABOLIC_30D_PCT = 25.0
MA_30W_SLOPE_FLOOR_PCT = -0.5
OBV_90D_FLOOR_PCT = 0.0
CMF_60D_FLOOR = 0.0


def run(ctx: PipelineContext) -> StageResult:
    a = ctx.signals
    if a is None:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason="no signals (ingest didn't run)",
            fix_point="backend/stages/ingest.py",
        )

    rejects: list[str] = []
    feats: dict = {
        "wyckoff": a.wyckoff_phase,
        "weinstein": a.weinstein_stage,
        "ma_30w_slope_pct": a.ma_30w_slope_pct,
        "obv_90d_pct": a.obv_slope_90d_pct,
        "cmf_60d": a.cmf_60d,
        "price_change_30d_pct": a.price_change_30d_pct,
    }

    if a.wyckoff_phase in ("distribution", "markdown"):
        rejects.append(f"Wyckoff = {a.wyckoff_phase}")
    if a.weinstein_stage == "stage_4_decline":
        rejects.append("Weinstein = Stage 4 decline")
    if a.ma_30w_slope_pct is not None and a.ma_30w_slope_pct < MA_30W_SLOPE_FLOOR_PCT:
        rejects.append(f"30wMA slope {a.ma_30w_slope_pct:+.1f}% (<{MA_30W_SLOPE_FLOOR_PCT}%)")
    obv90 = a.obv_slope_90d_pct or 0
    cmf60 = a.cmf_60d or 0
    if obv90 < OBV_90D_FLOOR_PCT and cmf60 < CMF_60D_FLOOR:
        rejects.append(f"OBV-90d {obv90:+.0f}% AND CMF-60d {cmf60:+.2f} both negative")
    if a.price_change_30d_pct is not None and a.price_change_30d_pct > PARABOLIC_30D_PCT:
        rejects.append(f"parabolic: +{a.price_change_30d_pct:.0f}% in 30d (>{PARABOLIC_30D_PCT}%)")

    passed = not rejects
    return StageResult(
        stage_id=stage_id,
        passed=passed,
        features=feats,
        evidence=rejects if rejects else ["no hard-reject conditions present"],
        fix_point="backend/stages/hard_rejects.py:PARABOLIC_30D_PCT",
        reason="; ".join(rejects) if rejects else "",
    )
