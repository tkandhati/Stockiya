"""[LT] Long-Term Volume — 50% weight, the primary signal.

Question: "Have institutions been buying for months?"

Per PRINCIPLES.md §0 "Long-term (PRIMARY)":
  - Stan Weinstein Stage = stage_2_advance or stage_1_to_2
  - 30-week MA slope positive
  - OBV (90d) and OBV (180d) rising
  - Chaikin Money Flow (60d) ≥ +0.05
  - Up/down volume ratio (90d) ≥ 1.3
  - Base length ≥ 60 days
  - Quarter-over-quarter volume growth ≥ 15%
"""

from __future__ import annotations

from ..pipeline import PipelineContext, StageResult

stage_id = "LT"

# Thresholds — auditable per line of PRINCIPLES.md §0.
WEINSTEIN_BUY_STAGES = ("stage_2_advance", "stage_1_to_2")
MA_30W_SLOPE_MIN_PCT = 0.0
OBV_90D_MIN_PCT = 5.0
OBV_180D_MIN_PCT = 0.0
CMF_60D_MIN = 0.05
UPDOWN_90D_MIN = 1.3
BASE_DAYS_MIN = 60
VOL_QOQ_MIN_PCT = 15.0


def _bool_score(value: float | None, threshold: float, good_when_above: bool = True) -> float:
    """Map a single metric to a [0,1] sub-score. Above threshold → 1.0, far below → 0.0,
    linear in between over 1× the gap."""
    if value is None:
        return 0.0
    if good_when_above:
        if value >= threshold:
            return 1.0
        delta = threshold - value
        return max(0.0, 1.0 - delta / max(abs(threshold), 1.0))
    if value <= threshold:
        return 1.0
    delta = value - threshold
    return max(0.0, 1.0 - delta / max(abs(threshold), 1.0))


def run(ctx: PipelineContext) -> StageResult:
    a = ctx.signals
    if a is None:
        return StageResult(stage_id=stage_id, passed=False, reason="no signals")

    feats = {
        "weinstein_stage": a.weinstein_stage,
        "ma_30w_slope_pct": a.ma_30w_slope_pct,
        "obv_90d_pct": a.obv_slope_90d_pct,
        "obv_180d_pct": a.obv_slope_180d_pct,
        "cmf_60d": a.cmf_60d,
        "updown_90d": a.up_down_vol_ratio_90d,
        "base_days": a.base_length_days,
        "vol_qoq_pct": a.vol_qoq_growth_pct,
        "minervini_template": a.minervini_template,
    }

    # 7-metric average (Weinstein stage counts as a gate-style 0/1).
    parts = [
        1.0 if a.weinstein_stage in WEINSTEIN_BUY_STAGES else 0.0,
        _bool_score(a.ma_30w_slope_pct, MA_30W_SLOPE_MIN_PCT),
        _bool_score(a.obv_slope_90d_pct, OBV_90D_MIN_PCT),
        _bool_score(a.obv_slope_180d_pct, OBV_180D_MIN_PCT),
        _bool_score(a.cmf_60d, CMF_60D_MIN),
        _bool_score(a.up_down_vol_ratio_90d, UPDOWN_90D_MIN),
        _bool_score(float(a.base_length_days), float(BASE_DAYS_MIN)),
        _bool_score(a.vol_qoq_growth_pct, VOL_QOQ_MIN_PCT),
    ]
    score = sum(parts) / len(parts)

    evidence: list[str] = []
    if a.weinstein_stage in WEINSTEIN_BUY_STAGES:
        evidence.append(f"Weinstein {a.weinstein_stage}")
    if a.obv_slope_90d_pct is not None:
        evidence.append(f"OBV-90d {a.obv_slope_90d_pct:+.0f}%")
    if a.obv_slope_180d_pct is not None:
        evidence.append(f"OBV-180d {a.obv_slope_180d_pct:+.0f}%")
    if a.cmf_60d is not None:
        evidence.append(f"CMF-60d {a.cmf_60d:+.2f}")
    if a.up_down_vol_ratio_90d is not None:
        evidence.append(f"U/D-90d {a.up_down_vol_ratio_90d:.2f}×")
    if a.base_length_days:
        evidence.append(f"base {a.base_length_days}d")
    if a.vol_qoq_growth_pct is not None:
        evidence.append(f"QoQ vol {a.vol_qoq_growth_pct:+.0f}%")

    return StageResult(
        stage_id=stage_id,
        passed=True,
        score=round(score, 4),
        features=feats,
        evidence=evidence,
        fix_point="backend/stages/lt_volume.py:thresholds",
    )
