"""[RK] Confirmation-strength ranker.

Operates on all per-ticker PipelineResults that cleared every gate.
Computes a confirmation score for each survivor:

    confirmation = sum(gate_margins) + BONUS_WEIGHT * bonus_signal_count

    gate_margins   = CS.score + VD.score + BR.score   (each in [0, 1])
    bonus signals  (each +1):
      - 50d MA > 150d MA > 200d MA aligned
      - OBV-90d slope >= +5 %
      - NSE block/bulk deals net-buy ratio >= 0.30 (last 30d, >=2 deals)
      - Pocket-pivot fires today (up day, vol > prior-10 max down-day vol)
      - Top RS rank vs other survivors  (proxy; full Nifty 100 RS later)

The pick with the highest confirmation score is rank #1. Top N selected
(default 3). Less-likely-false setups bubble to the top.

Fix points:
    BONUS_OBV_90D_MIN       : OBV-90d slope threshold (default 5.0)
    BONUS_BLOCK_DEAL_MIN    : net qty ratio threshold (default 0.30)
    BONUS_BLOCK_DEAL_MIN_COUNT : minimum number of deal events to qualify
    BONUS_RS_RANK_TOP_PCT   : top fraction of survivors for RS bonus
    BONUS_WEIGHT            : weight on bonus signals (default 0.5)
    TOP_N                   : how many picks per day (default 3)
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..indicators import ma_stack_aligned, obv, obv_slope_pct
from ..pipeline import PipelineResult


# --------------------------------------------------------------------------- #
# Tunable constants
# --------------------------------------------------------------------------- #

BONUS_OBV_90D_MIN: float = 5.0          # tunable
BONUS_BLOCK_DEAL_MIN: float = 0.30      # tunable
BONUS_BLOCK_DEAL_MIN_COUNT: int = 2     # tunable
BONUS_RS_RANK_TOP_PCT: float = 0.30     # tunable
BONUS_WEIGHT: float = 0.5               # tunable
TOP_N: int = 3                           # tunable


# --------------------------------------------------------------------------- #
# Bonus-signal helpers
# --------------------------------------------------------------------------- #

def _check_pocket_pivot_today(df: Optional[pd.DataFrame]) -> bool:
    """Today is an up day AND today's volume > max(down-day volumes in prior 10)."""
    if df is None or len(df) < 12:
        return False
    closes = df["Close"]
    vols = df["Volume"]
    if closes.iloc[-1] <= closes.iloc[-2]:
        return False
    prev = df.iloc[-11:-1]
    prev_deltas = prev["Close"].diff().fillna(0)
    prev_down_vols = prev["Volume"][prev_deltas < 0]
    if prev_down_vols.empty:
        return False
    return float(vols.iloc[-1]) > float(prev_down_vols.max())


def _block_deal_net_buy(symbol: str) -> Optional[float]:
    """Return net_qty_ratio if >= BONUS_BLOCK_DEAL_MIN_COUNT deals exist in 30d.

    None means insufficient block/bulk activity (neutral signal, not negative).
    """
    try:
        from ..block_deals import aggregate_30d
        deals = aggregate_30d(symbol)
        if (deals.buy_count + deals.sell_count) < BONUS_BLOCK_DEAL_MIN_COUNT:
            return None
        return deals.net_qty_ratio
    except Exception:
        return None


def _ret_90d(df: Optional[pd.DataFrame]) -> Optional[float]:
    """90-bar return as a fraction."""
    if df is None or len(df) < 91:
        return None
    p0 = float(df["Close"].iloc[-91])
    p1 = float(df["Close"].iloc[-1])
    if p0 <= 0:
        return None
    return (p1 / p0) - 1


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def rank_survivors(
    survivors: list[PipelineResult],
    *,
    top_n: int = TOP_N,
) -> list[PipelineResult]:
    """Compute confirmation scores and select the top N.

    Mutates each survivor in place: sets `confirmation_score`,
    `confirmation_components`, `selected`, `rank`.

    Returns the selected list, sorted #1 -> #N.
    """
    if not survivors:
        return []

    # ---- Pre-compute RS rank (90-day return within the survivor set) ----
    rets: list[Optional[float]] = []
    for r in survivors:
        rets.append(_ret_90d(r.ohlcv))

    indexed = [(i, v) for i, v in enumerate(rets) if v is not None]
    indexed.sort(key=lambda x: x[1], reverse=True)
    cutoff = max(1, int(len(indexed) * BONUS_RS_RANK_TOP_PCT))
    top_rs_indices = {i for i, _ in indexed[:cutoff]}

    # ---- Per-survivor confirmation ----
    for idx, r in enumerate(survivors):
        stages = r.stage_results
        # Gate margin sum from LT/CS/VD/BR
        margin = 0.0
        for gid in ("LT", "CS", "VD", "BR"):
            sr = stages.get(gid)
            if sr is not None:
                margin += float(sr.score or 0.0)

        bonuses_fired: list[str] = []
        df = r.ohlcv

        # 1. MA stack
        if df is not None and ma_stack_aligned(df["Close"]):
            bonuses_fired.append("MA stack 50>150>200")

        # 2. OBV-90d slope
        if df is not None:
            obv_series = obv(df["Close"], df["Volume"])
            slope = obv_slope_pct(obv_series, 90)
            if slope is not None and slope >= BONUS_OBV_90D_MIN:
                bonuses_fired.append(f"OBV-90d {slope:+.1f}% >= {BONUS_OBV_90D_MIN}%")

        # 3. Block / bulk deal net-buy
        deal_ratio = _block_deal_net_buy(r.symbol)
        if deal_ratio is not None and deal_ratio >= BONUS_BLOCK_DEAL_MIN:
            bonuses_fired.append(f"Block-deal net-buy {deal_ratio:+.2f}")

        # 4. Pocket-pivot today
        if _check_pocket_pivot_today(df):
            bonuses_fired.append("Pocket-pivot today")

        # 5. Top RS rank (proxy: vs other survivors)
        if idx in top_rs_indices:
            bonuses_fired.append("Top RS-rank vs survivors")

        bonus_count = len(bonuses_fired)
        confirmation = margin + BONUS_WEIGHT * bonus_count

        r.confirmation_score = round(confirmation, 4)
        r.confirmation_components = {
            "gate_margin_sum": round(margin, 4),
            "bonus_count": bonus_count,
            "bonus_weight": BONUS_WEIGHT,
            "bonuses_fired": bonuses_fired,
        }

    # ---- Sort + select ----
    survivors.sort(key=lambda x: x.confirmation_score, reverse=True)
    selected = survivors[:top_n]
    for rank, r in enumerate(selected, start=1):
        r.selected = True
        r.rank = rank

    return selected
