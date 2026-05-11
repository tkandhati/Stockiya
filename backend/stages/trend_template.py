"""[TT] Trend Template — 15% weight, Minervini's structure check.

Question: "Is the price structure healthy?"

Per PRINCIPLES.md §0: 50d > 150d > 200d, all rising, price > 50d. We additionally
score the 200d slope so the signal is graded, not just 0/1.
"""

from __future__ import annotations

from ..pipeline import PipelineContext, StageResult

stage_id = "TT"


def run(ctx: PipelineContext) -> StageResult:
    a = ctx.signals
    if a is None:
        return StageResult(stage_id=stage_id, passed=False, reason="no signals")

    feats = {
        "minervini_template": a.minervini_template,
        "ma_50d": a.ma_50d,
        "ma_150d": a.ma_150d,
        "ma_200d": a.ma_200d,
        "ma_30w_slope_pct": a.ma_30w_slope_pct,
    }

    has_mas = all(x is not None for x in (a.ma_50d, a.ma_150d, a.ma_200d))
    stacked = has_mas and (a.ma_50d > a.ma_150d > a.ma_200d)
    last_close = ctx.snapshot.get("current") or 0
    price_above_50 = has_mas and last_close > (a.ma_50d or 0)

    # 4-component grade: stacking, price>50d, 30w slope positive, full template
    parts = [
        1.0 if stacked else 0.0,
        1.0 if price_above_50 else 0.0,
        1.0 if (a.ma_30w_slope_pct or 0) > 0 else 0.0,
        1.0 if a.minervini_template else 0.0,
    ]
    score = sum(parts) / len(parts)

    evidence: list[str] = []
    if stacked:
        evidence.append("50d > 150d > 200d (stacked)")
    if price_above_50:
        evidence.append(f"price ₹{last_close:.0f} > 50d MA ₹{a.ma_50d:.0f}")
    if (a.ma_30w_slope_pct or 0) > 0:
        evidence.append(f"30wMA rising ({a.ma_30w_slope_pct:+.1f}%)")
    if a.minervini_template:
        evidence.append("Minervini Trend Template = TRUE")
    if not evidence:
        evidence.append("trend structure incomplete")

    return StageResult(
        stage_id=stage_id, passed=True, score=round(score, 4),
        features=feats, evidence=evidence,
        fix_point="backend/stages/trend_template.py",
    )
