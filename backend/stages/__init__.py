"""Pipeline stages — gates-based spine.

Each per-ticker stage module exports `run(ctx) -> StageResult` with the
contract in `backend/pipeline.py`. The chain is `PER_TICKER_CHAIN` below.

Per-ticker chain (run in parallel by the orchestrator):

    universe        -> [U]   gate    In Nifty 100?
    ingest          -> [I]   gate    >=200 daily bars; populates ctx.ohlcv
    hard_rejects    -> [HR]  gate    Parabolic 30d / extended above 50d MA
                                     (avoids buying into institutional dumping)
    lt_flow         -> [LT]  gate    3-6 month institutional accumulation
    consolidation   -> [CS]  gate    Tight base above 150d MA
    volume          -> [VD]  gate    Dry-up + bullish OBV-price divergence
    breakout        -> [BR]  gate    Resistance + 1.5x vol + upper-third close

Run by the orchestrator OUTSIDE the per-ticker chain:

    regime          -> [RG]  one-shot market gate (NIFTY 100)
    rank            -> [RK]  confirmation-strength ranker
    hypothesis      -> [H]   pick payload + position sizing
    render          -> [R]   JSON writer
    outcome         -> [O]   T+90 / T+180 reward

Replace any one file to swap that step's logic; nothing else changes.
"""

from . import (  # noqa: F401
    universe,
    ingest,
    hard_rejects,
    lt_flow,
    consolidation,
    volume,
    breakout,
    regime,
    rank,
    hypothesis,
    render,
    outcome,
)

PER_TICKER_CHAIN = [
    universe.run,
    ingest.run,
    hard_rejects.run,
    lt_flow.run,
    consolidation.run,
    volume.run,
    breakout.run,
]
