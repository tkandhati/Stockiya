"""[U] Universe gate — is the ticker in our allowed scan universe?"""

from __future__ import annotations

from ..pipeline import PipelineContext, StageResult
from ..universe import VOLUME_UNIVERSE, VOLUME_UNIVERSE_LABEL, VOLUME_UNIVERSE_SET

stage_id = "U"


def run(ctx: PipelineContext) -> StageResult:
    in_universe = ctx.symbol in VOLUME_UNIVERSE_SET
    return StageResult(
        stage_id=stage_id,
        passed=in_universe,
        features={"in_universe": in_universe, "universe_size": len(VOLUME_UNIVERSE)},
        evidence=[
            f"{ctx.symbol} {'is' if in_universe else 'is NOT'} in {VOLUME_UNIVERSE_LABEL}"
        ],
        fix_point="backend/universe.py:VOLUME_UNIVERSE",
        reason=(
            "" if in_universe
            else f"{ctx.symbol} not in {VOLUME_UNIVERSE_LABEL} universe"
        ),
    )
