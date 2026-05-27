"""[I] Ingest — fetch daily OHLCV; populate ctx.ohlcv + ctx.snapshot.

The gates-based spine reads indicators directly from `backend/indicators.py`
per stage; no shared AccumulationSignals dataclass is computed here.

Historical backfill: when `ctx.today_iso` is a past date, the OHLCV is
sliced so the pipeline sees only bars up to and including that date — the
lookahead-bias mitigation from PRINCIPLES Section 8. The snapshot's
`current` price is also overridden with the as-of close so the breakout
gate doesn't peek at today.
"""

from __future__ import annotations

from datetime import date as _date

import pandas as pd

from ..fetch import fetch_ohlcv, fetch_snapshot
from ..pipeline import PipelineContext, StageResult

stage_id = "I"

# Minimum daily bars before the long-term and consolidation gates are
# meaningful. 200 = required for the 200d MA used in ranking bonuses.
MIN_BARS = 200


def _slice_to_as_of(ohlcv: pd.DataFrame, as_of: _date) -> pd.DataFrame:
    """Drop any bars dated after `as_of`. Safe with tz-aware indices."""
    cutoff = pd.Timestamp(as_of)
    try:
        idx = ohlcv.index.normalize()
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        return ohlcv[idx <= cutoff]
    except Exception:
        # Last-resort row-by-row filter
        return ohlcv[[ts.date() <= as_of for ts in ohlcv.index]]


def run(ctx: PipelineContext) -> StageResult:
    snap = fetch_snapshot(ctx.symbol)

    ohlcv = fetch_ohlcv(ctx.symbol)
    if ohlcv is None or ohlcv.empty:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_ohlcv": False},
            fix_point="backend/yahoo.py:history_ohlcv",
            reason="no OHLCV from data source",
        )

    # Lookahead protection: if today_iso is a past date (backfill / backtest),
    # drop any bars after that date.
    as_of_used = None
    if ctx.today_iso:
        try:
            as_of = _date.fromisoformat(ctx.today_iso)
            ohlcv = _slice_to_as_of(ohlcv, as_of)
            as_of_used = as_of.isoformat()
        except ValueError:
            pass

    if ohlcv.empty:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"as_of": as_of_used, "has_ohlcv": False},
            fix_point="backend/stages/ingest.py:_slice_to_as_of",
            reason=f"no bars at or before as_of={as_of_used}",
        )

    bars = len(ohlcv)
    if bars < MIN_BARS:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"bars": bars, "min_required": MIN_BARS, "as_of": as_of_used},
            fix_point="backend/stages/ingest.py:MIN_BARS",
            reason=f"only {bars} bars, need >={MIN_BARS}",
        )

    # For historical backfill, snapshot's "current" was today's live price.
    # Override with the close of the as-of bar so downstream gates see the
    # correct historical price.
    as_of_close = float(ohlcv["Close"].iloc[-1])
    if as_of_used:
        snap = dict(snap)
        snap["current"] = as_of_close

    current = snap.get("current") or as_of_close
    if not current:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_snapshot": False, "as_of": as_of_used},
            fix_point="backend/yahoo.py:snapshot",
            reason="no current price from data source",
        )

    ctx.snapshot = snap
    ctx.ohlcv = ohlcv

    return StageResult(
        stage_id=stage_id, passed=True,
        features={"bars": bars, "current": current, "as_of": as_of_used},
        evidence=[
            f"{bars} daily bars · current ₹{current:.2f}"
            + (f" · as-of {as_of_used}" if as_of_used else "")
        ],
        fix_point="backend/stages/ingest.py:MIN_BARS",
    )
