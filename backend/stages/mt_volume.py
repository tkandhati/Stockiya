"""[MT] Mid-Term Volume — 20% weight, today's confirmation.

Question: "Is the buying still happening *this month*?"

Per PRINCIPLES.md §0 "Medium-term (CONFIRMATION)":
  - Wyckoff = Accumulation or early Markup
  - OBV (30d) rising, CMF (21d) ≥ +0.10, U/D (30d) ≥ 1.4×
  - 10-day avg vol > 30-day avg vol
  - MFI (14d) in 50–80 healthy zone
  - Price above 60-day VWAP
"""

from __future__ import annotations

from ..pipeline import PipelineContext, StageResult

stage_id = "MT"

WYCKOFF_BUY_PHASES = ("accumulation", "markup")
OBV_30D_MIN_PCT = 5.0
CMF_21D_MIN = 0.10
UPDOWN_30D_MIN = 1.4
VOL_TREND_MIN_PCT = 0.0      # vol_trend_pct = (10d / 30d - 1) * 100
MFI_HEALTHY_LOW = 50.0
MFI_HEALTHY_HIGH = 80.0
VWAP_POSTURE_MIN_PCT = 0.0


def _grade_above(v, t):
    return 1.0 if (v is not None and v >= t) else 0.0


def run(ctx: PipelineContext) -> StageResult:
    a = ctx.signals
    if a is None:
        return StageResult(stage_id=stage_id, passed=False, reason="no signals")

    feats = {
        "wyckoff": a.wyckoff_phase,
        "obv_30d_pct": a.obv_slope_pct,
        "cmf_21d": a.cmf_21d,
        "updown_30d": a.up_down_vol_ratio,
        "vol_trend_pct": a.vol_trend_pct,
        "mfi_14d": a.mfi_14d,
        "price_vs_vwap_pct": a.price_vs_vwap_pct,
    }

    mfi_ok = (
        1.0 if (a.mfi_14d is not None and MFI_HEALTHY_LOW <= a.mfi_14d <= MFI_HEALTHY_HIGH)
        else 0.0
    )

    parts = [
        1.0 if a.wyckoff_phase in WYCKOFF_BUY_PHASES else 0.0,
        _grade_above(a.obv_slope_pct, OBV_30D_MIN_PCT),
        _grade_above(a.cmf_21d, CMF_21D_MIN),
        _grade_above(a.up_down_vol_ratio, UPDOWN_30D_MIN),
        _grade_above(a.vol_trend_pct, VOL_TREND_MIN_PCT),
        mfi_ok,
        _grade_above(a.price_vs_vwap_pct, VWAP_POSTURE_MIN_PCT),
    ]
    score = sum(parts) / len(parts)

    evidence: list[str] = []
    if a.wyckoff_phase in WYCKOFF_BUY_PHASES:
        evidence.append(f"Wyckoff {a.wyckoff_phase}")
    if a.obv_slope_pct is not None:
        evidence.append(f"OBV-30d {a.obv_slope_pct:+.0f}%")
    if a.cmf_21d is not None:
        evidence.append(f"CMF-21d {a.cmf_21d:+.2f}")
    if a.up_down_vol_ratio is not None:
        evidence.append(f"U/D-30d {a.up_down_vol_ratio:.2f}×")
    if a.mfi_14d is not None:
        evidence.append(f"MFI-14 {a.mfi_14d:.0f}")
    if a.price_vs_vwap_pct is not None:
        evidence.append(f"price vs VWAP {a.price_vs_vwap_pct:+.1f}%")

    return StageResult(
        stage_id=stage_id, passed=True, score=round(score, 4),
        features=feats, evidence=evidence,
        fix_point="backend/stages/mt_volume.py:thresholds",
    )
