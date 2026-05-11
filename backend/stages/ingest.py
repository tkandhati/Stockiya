"""[I] Ingest — fetch daily OHLCV, compute the AccumulationSignals once.

The AccumulationSignals object becomes the shared feature pool every
subsequent stage reads from. No stage recomputes math.
"""

from __future__ import annotations

from ..fetch import fetch_ohlcv, fetch_snapshot
from ..pipeline import PipelineContext, StageResult
from ..signals import compute

stage_id = "I"

# Minimum daily bars before signals are reliable. 200 = required for 200d MA.
MIN_BARS = 200


def run(ctx: PipelineContext) -> StageResult:
    snap = fetch_snapshot(ctx.symbol)
    current = snap.get("current")
    if not current:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_snapshot": False},
            fix_point="backend/yahoo.py:snapshot",
            reason="no current price from data source",
        )

    ohlcv = fetch_ohlcv(ctx.symbol)
    if ohlcv is None or ohlcv.empty:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_ohlcv": False},
            fix_point="backend/yahoo.py:history_ohlcv",
            reason="no OHLCV from data source",
        )

    bars = len(ohlcv)
    if bars < MIN_BARS:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"bars": bars, "min_required": MIN_BARS},
            fix_point="backend/stages/ingest.py:MIN_BARS",
            reason=f"only {bars} bars, need >={MIN_BARS}",
        )

    signals = compute(ohlcv, symbol=ctx.symbol)
    ctx.snapshot = snap
    ctx.ohlcv = ohlcv
    ctx.signals = signals

    return StageResult(
        stage_id=stage_id, passed=True,
        features={"bars": bars, "current": current},
        evidence=[f"{bars} daily bars · current ₹{current:.2f}"],
        fix_point="backend/stages/ingest.py:MIN_BARS",
    )
