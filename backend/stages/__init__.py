"""Pipeline stages — gates-based spine.

Each per-ticker stage module exports `run(ctx) -> StageResult` with the
contract in `backend/pipeline.py`. The chain is `PER_TICKER_CHAIN` below.

Per-ticker chain (run in parallel by the orchestrator):

    universe        -> [U]   gate    In Nifty 100?
    ingest          -> [I]   gate    >=200 daily bars; populates ctx.ohlcv
    hard_rejects    -> [HR]  gate    Parabolic 30d / extended above 50d MA
                                     (avoids buying into institutional dumping)
    lt_flow         -> [LT]  gate    90-180 day institutional accumulation
                                     (admission-evidence window, not hold duration)
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
    accum_screen,
    accumulation,
    lt_flow,
    consolidation,
    volume,
    breakout,
    distribution_veto,
    regime,
    rank,
    hypothesis,
    render,
    outcome,
)

# Per-ticker chain — soft-gate composite spine (v3).
#
# Order matters: hard gates first (U, I, HR) so they short-circuit on failure.
# Then the two accumulation screens (ACS = cheap 45-bar range/vol check,
# AC = 180-bar range/vol + ADI positive divergence). After that the four
# legacy trend gates (LT / CS / VD / BR). All non-hard stages are SOFT: their
# failure contributes zero margin to the composite but does not stop the
# chain — the composite S = Σ wᵢ · mᵢ is the real selection surface.
#
# [DV] Distribution Veto runs LAST in the chain so it can see the full tape
# after [BR] and short-circuit selection if configured to block. In shadow
# mode (default) it always passes — trace-only. See distribution_veto.py.
PER_TICKER_CHAIN = [
    universe.run,
    ingest.run,
    hard_rejects.run,
    accum_screen.run,
    accumulation.run,
    lt_flow.run,
    consolidation.run,
    volume.run,
    breakout.run,
    distribution_veto.run,
]
