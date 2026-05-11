"""[BR] Breakout Triggers — 5% weight, timing signals.

Question: "Is there a fresh entry signal today?"

Three classical patterns from the volume canon:
  - Pocket Pivot (Morales/Kacher): up-day volume > prior 10 down-day volumes,
    while price is in a tight base — institutional buy point.
  - Volume Dry-Up (Minervini): pre-breakout supply exhaustion (5d avg < 50%
    of 50d avg on a tight base).
  - CAN SLIM breakout (O'Neil): at 20d high on ≥ 1.4× 50d avg volume.

Each fires = +0.33; all three = full score.
"""

from __future__ import annotations

from ..pipeline import PipelineContext, StageResult

stage_id = "BR"


def run(ctx: PipelineContext) -> StageResult:
    a = ctx.signals
    if a is None:
        return StageResult(stage_id=stage_id, passed=False, reason="no signals")

    pp = a.pocket_pivot_count_30d >= 1
    vdu = bool(a.volume_dry_up)
    canslim = bool(a.canslim_breakout)

    fired = sum([pp, vdu, canslim])
    score = fired / 3.0

    feats = {
        "pocket_pivot_count_30d": a.pocket_pivot_count_30d,
        "volume_dry_up": vdu,
        "canslim_breakout": canslim,
        "patterns_fired": fired,
    }

    evidence: list[str] = []
    if pp:
        evidence.append(f"Pocket Pivot ×{a.pocket_pivot_count_30d}")
    if vdu:
        evidence.append("Volume Dry-Up on tight base")
    if canslim:
        evidence.append("CAN SLIM breakout")
    if not evidence:
        evidence.append("no breakout triggers yet")

    return StageResult(
        stage_id=stage_id, passed=True, score=round(score, 4),
        features=feats, evidence=evidence,
        fix_point="backend/stages/breakouts.py",
    )
