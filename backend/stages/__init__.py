"""Pipeline stages. Each module exports a `run(ctx) -> StageResult`.

The full chain in execution order:

    universe       → [U]   gate    Is the ticker in Nifty 100?
    ingest         → [I]   gate    Have we got >=200 daily bars?
    hard_rejects   → [HR]  gate    Wyckoff Distribution/Markdown, parabolic, etc.
    lt_volume      → [LT]  50%     Long-term institutional accumulation
    trend_template → [TT]  15%     Minervini 50>150>200 + slopes
    mt_volume      → [MT]  20%     This month's confirmation
    direct_deals   → [DD]  10%     NSE block + bulk deal aggregates
    breakouts      → [BR]  5%      Pocket pivot / VDU / CAN SLIM
    score          →               Composite weighted-sum + ranking
    hypothesis     →               Template-built rationale + exit plan
    render         →               Card JSON for the UI
    outcome        →               T+90/T+180 realized return (RL reward)

Replace any single file to swap that stage's logic; the orchestrator and
every other stage are untouched.
"""

from . import (  # noqa: F401
    universe,
    ingest,
    hard_rejects,
    lt_volume,
    trend_template,
    mt_volume,
    direct_deals,
    breakouts,
    score,
    hypothesis,
    render,
    outcome,
)

# Default pipeline order — what `run_pipeline()` runs end-to-end for one ticker.
PER_TICKER_CHAIN = [
    universe.run,
    ingest.run,
    hard_rejects.run,
    lt_volume.run,
    trend_template.run,
    mt_volume.run,
    direct_deals.run,
    breakouts.run,
]
