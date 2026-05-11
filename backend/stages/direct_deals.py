"""[DD] Direct Deals — 10% weight, named institutional trades.

Question: "Did named institutions actually buy in the last 30 days?"

NSE block deals (>0.5% of equity capital) and bulk deals (>0.5% of volume)
are EOD-reported records of large trades by named counterparties. Unlike
OBV/CMF which infer institutional flow from aggregated volume, these are
literal records of fund/promoter transactions.

Score is a function of net buy ratio over the last 30 days, scaled into [0,1].
"""

from __future__ import annotations

from ..block_deals import aggregate_30d
from ..pipeline import PipelineContext, StageResult

stage_id = "DD"

# Net qty ratio = (buy_qty - sell_qty) / (buy_qty + sell_qty), in [-1, +1].
# +0.30 → strong net buy, -0.30 → strong net sell.
NET_RATIO_FULL_SCORE = 0.30
MIN_DEALS_FOR_SIGNAL = 2


def run(ctx: PipelineContext) -> StageResult:
    try:
        agg = aggregate_30d(ctx.symbol)
    except Exception as e:
        return StageResult(
            stage_id=stage_id, passed=True, score=0.0,
            features={"error": str(e)},
            evidence=["block-deal aggregation unavailable"],
            fix_point="backend/block_deals.py",
            reason=str(e),
        )

    total_deals = agg.buy_count + agg.sell_count
    ratio = agg.net_qty_ratio

    feats = {
        "buy_count": agg.buy_count,
        "sell_count": agg.sell_count,
        "buy_qty": agg.buy_qty,
        "sell_qty": agg.sell_qty,
        "net_qty_ratio": ratio,
        "days_used": agg.days_used,
    }

    # No deals in window → neutral (0.5), not bad, just no signal.
    if total_deals < MIN_DEALS_FOR_SIGNAL:
        return StageResult(
            stage_id=stage_id, passed=True, score=0.5,
            features=feats,
            evidence=[f"only {total_deals} block/bulk deal(s) in 30d — no signal"],
            fix_point="backend/stages/direct_deals.py:MIN_DEALS_FOR_SIGNAL",
        )

    # Map ratio (-1..+1) → score (0..1) with +0.30 = 1.0, -0.30 = 0.0, linear.
    clamped = max(-NET_RATIO_FULL_SCORE, min(NET_RATIO_FULL_SCORE, ratio))
    score = 0.5 + 0.5 * (clamped / NET_RATIO_FULL_SCORE)

    if ratio >= NET_RATIO_FULL_SCORE:
        verdict = f"net institutional BUY ({ratio:+.0%})"
    elif ratio <= -NET_RATIO_FULL_SCORE:
        verdict = f"net institutional SELL ({ratio:+.0%})"
    else:
        verdict = f"mixed ({ratio:+.0%})"

    return StageResult(
        stage_id=stage_id, passed=True, score=round(score, 4),
        features=feats,
        evidence=[
            f"{agg.buy_count} buys / {agg.sell_count} sells in 30d",
            verdict,
        ],
        fix_point="backend/stages/direct_deals.py:NET_RATIO_FULL_SCORE",
    )
