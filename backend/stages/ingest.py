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
from typing import Optional

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


def _recompute_snapshot_from_ohlcv(snap: dict, ohlcv: "pd.DataFrame") -> dict:
    """Replace today-leaking fields (ma50, ma200, 52w high/low, return_*) with
    values computed from the as-of-sliced OHLCV. Display fields (company,
    sector, industry) and the now-overridden `current` are preserved.

    Used only in backtest mode so the downstream stages and any UI rendering
    see point-in-time snapshot values instead of live ones.
    """
    snap = dict(snap)
    closes = ohlcv["Close"].dropna()
    vols = ohlcv["Volume"].dropna() if "Volume" in ohlcv.columns else None

    def _f(x):
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f

    snap["ma50"] = _f(closes.tail(50).mean()) if len(closes) >= 50 else None
    snap["ma200"] = _f(closes.tail(200).mean()) if len(closes) >= 200 else None

    # 52-week window = last ~252 bars or whatever we have if less
    last_252 = closes.tail(252) if len(closes) > 252 else closes
    snap["fifty_two_w_high"] = _f(last_252.max()) if not last_252.empty else None
    snap["fifty_two_w_low"] = _f(last_252.min()) if not last_252.empty else None

    if len(closes) >= 65:
        r3 = (closes.iloc[-1] / closes.iloc[-65] - 1) * 100
        snap["return_3m_pct"] = round(r3, 2) if r3 == r3 else None
    else:
        snap["return_3m_pct"] = None

    if len(last_252) >= 2:
        r1 = (last_252.iloc[-1] / last_252.iloc[0] - 1) * 100
        snap["return_1y_pct"] = round(r1, 2) if r1 == r1 else None
    else:
        snap["return_1y_pct"] = None

    if vols is not None and not vols.empty:
        snap["vol_today"] = _f(vols.iloc[-1])
        snap["vol_avg30"] = _f(vols.tail(30).mean()) if len(vols) >= 30 else None

    # day_change_pct = (today close vs prior close)
    if len(closes) >= 2:
        prev = _f(closes.iloc[-2])
        cur = _f(closes.iloc[-1])
        if prev and cur:
            snap["day_change_pct"] = round((cur - prev) / prev * 100, 2)
        else:
            snap["day_change_pct"] = None
    return snap


def run(ctx: PipelineContext) -> StageResult:
    # Resolve as_of first so we can scope the OHLCV fetch.
    as_of: Optional[_date] = None
    if ctx.today_iso:
        try:
            as_of = _date.fromisoformat(ctx.today_iso)
        except ValueError:
            as_of = None
    as_of_iso = as_of.isoformat() if as_of else None
    is_backtest = as_of is not None

    snap = fetch_snapshot(ctx.symbol)

    # Backtest mode: fetch a window ending at as_of so we don't rely on
    # "live window then slice". Live mode: fetch the default ~2y ending today.
    ohlcv = fetch_ohlcv(ctx.symbol, end=as_of_iso) if is_backtest else fetch_ohlcv(ctx.symbol)
    if ohlcv is None or ohlcv.empty:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_ohlcv": False, "as_of": as_of_iso},
            fix_point="backend/yahoo.py:history_ohlcv",
            reason="no OHLCV from data source",
        )

    # Defensive slice (handles live windows or oversized backtest fetches).
    if as_of is not None:
        ohlcv = _slice_to_as_of(ohlcv, as_of)

    if ohlcv.empty:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"as_of": as_of_iso, "has_ohlcv": False},
            fix_point="backend/stages/ingest.py:_slice_to_as_of",
            reason=f"no bars at or before as_of={as_of_iso}",
        )

    bars = len(ohlcv)
    if bars < MIN_BARS:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"bars": bars, "min_required": MIN_BARS, "as_of": as_of_iso},
            fix_point="backend/stages/ingest.py:MIN_BARS",
            reason=f"only {bars} bars, need >={MIN_BARS}",
        )

    as_of_close = float(ohlcv["Close"].iloc[-1])
    if is_backtest:
        # Replace today-leaking snapshot fields with values from sliced OHLCV.
        snap = _recompute_snapshot_from_ohlcv(snap, ohlcv)
        snap["current"] = as_of_close

    current = snap.get("current") or as_of_close
    if not current:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_snapshot": False, "as_of": as_of_iso},
            fix_point="backend/yahoo.py:snapshot",
            reason="no current price from data source",
        )

    ctx.snapshot = snap
    ctx.ohlcv = ohlcv

    return StageResult(
        stage_id=stage_id, passed=True,
        features={"bars": bars, "current": current, "as_of": as_of_iso},
        evidence=[
            f"{bars} daily bars · current ₹{current:.2f}"
            + (f" · as-of {as_of_iso}" if as_of_iso else "")
        ],
        fix_point="backend/stages/ingest.py:MIN_BARS",
    )
