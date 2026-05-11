"""[U] Universe gate — is the ticker in our allowed list (Nifty 100)?"""

from __future__ import annotations

from ..pipeline import PipelineContext, StageResult
from ..universe import UNIVERSE

stage_id = "U"


def run(ctx: PipelineContext) -> StageResult:
    in_universe = ctx.symbol in UNIVERSE
    return StageResult(
        stage_id=stage_id,
        passed=in_universe,
        features={"in_universe": in_universe, "universe_size": len(UNIVERSE)},
        evidence=[f"{ctx.symbol} {'is' if in_universe else 'is NOT'} in Nifty 100"],
        fix_point="backend/universe.py:UNIVERSE",
        reason="" if in_universe else f"{ctx.symbol} not in Nifty 100 universe",
    )
