"""[S] Score & Rank — aggregator, not a per-ticker stage.

Called by the orchestrator after every ticker has been pipelined. Takes the
list of PipelineResults, computes the composite (0-100) per ticker, ranks
them, and marks the top-N (default 3) as `selected`.

Composite = sum_i (weight_i × stage_score_i) × 100 over scoring stages
            {LT, TT, MT, DD, BR}.

A ticker that failed a gate (U/I/HR) is ranked but never selected — it stays
in traces with composite_score = 0 so the RL dataset has negatives too.
"""

from __future__ import annotations

from typing import Iterable

from ..pipeline import STAGE_WEIGHTS, PipelineResult, append_final_trace

stage_id = "S"


def compute_composite(result: PipelineResult) -> float:
    """Composite in 0..100. Failed-gate tickers score 0."""
    if not result.passed_gates:
        return 0.0
    total = 0.0
    for stage_id_key, weight in STAGE_WEIGHTS.items():
        sr = result.stage_results.get(stage_id_key)
        if sr is None:
            continue
        total += weight * sr.score
    return round(total * 100, 2)


def rank_and_select(
    results: Iterable[PipelineResult],
    top_n: int = 3,
    min_composite: float = 60.0,
    today_iso: str = "",
) -> list[PipelineResult]:
    """In-place: set composite_score, selected, rank on every result.
    Returns the selected list (length 0..top_n).

    `min_composite` is the floor below which we'd rather show 0 picks than
    pad (per PRINCIPLES.md §2 "Quality over quantity").
    """
    results = list(results)
    for r in results:
        r.composite_score = compute_composite(r)

    results.sort(key=lambda r: r.composite_score, reverse=True)

    selected: list[PipelineResult] = []
    for i, r in enumerate(results):
        if (
            r.passed_gates
            and r.composite_score >= min_composite
            and len(selected) < top_n
        ):
            r.selected = True
            r.rank = len(selected) + 1
            selected.append(r)

    # Trace the final decision for every ticker, selected or not — RL needs
    # the negatives too.
    if today_iso:
        for r in results:
            append_final_trace(r, today_iso)

    return selected
