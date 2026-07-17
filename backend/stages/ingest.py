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

from datetime import date as _date, datetime, time as _time
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ..fetch import fetch_ohlcv, fetch_snapshot
from ..pipeline import PipelineContext, StageResult
from ..snapshot_calc import build_snapshot_from_ohlcv

stage_id = "I"

# Minimum daily bars before the long-term and consolidation gates are
# meaningful. 200 = required for the 200d MA used in ranking bonuses.
MIN_BARS = 200

# Advisory-only floor: with >=260 finalized bars every indicator (200d MA,
# OBV-90d, ADV50, MAD-50d) has full lookback with margin. Below this the
# pipeline still runs but the trace records has_full_lookback=False so
# downstream calibration knows the sample is truncated.
FULL_LOOKBACK_BARS = 260

_IST = ZoneInfo("Asia/Kolkata")
# NSE closes 15:30 IST; 5-min buffer for feed catch-up before we trust the
# last bar of the current session as finalized.
_SESSION_CLOSE_BUFFER = _time(15, 35)


def _drop_partial_session_bar(
    ohlcv: pd.DataFrame,
    is_backtest: bool,
) -> tuple[pd.DataFrame, int]:
    """If the last bar belongs to a session that hasn't closed yet, drop it.

    Live-mode only — backtests always use finalized as-of slices. The check
    is IST-aware because NSE market hours are the authority, not wallclock.
    Returns (possibly-trimmed df, count of bars dropped).
    """
    if is_backtest or ohlcv.empty:
        return ohlcv, 0
    now_ist = datetime.now(_IST)
    last_ts = ohlcv.index[-1]
    try:
        last_date = last_ts.date()
    except AttributeError:
        return ohlcv, 0
    if last_date == now_ist.date() and now_ist.time() < _SESSION_CLOSE_BUFFER:
        return ohlcv.iloc[:-1], 1
    return ohlcv, 0


def _clean_malformed_rows(ohlcv: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop rows that can't represent a real session bar.

    Rules:
      • NaN in any of Open/High/Low/Close → not a completed print
      • Volume <= 0 or NaN            → suspended day / no trades

    Suspended sessions and holiday-adjacent phantom bars sometimes leak
    through the data-source layer; every downstream indicator assumes real
    numbers. Cheaper to reject them once here than tolerate them everywhere.
    Returns (cleaned df, dropped count).
    """
    if ohlcv.empty:
        return ohlcv, 0
    before = len(ohlcv)
    ohlc_cols = [c for c in ("Open", "High", "Low", "Close") if c in ohlcv.columns]
    cleaned = ohlcv.dropna(subset=ohlc_cols) if ohlc_cols else ohlcv
    if "Volume" in cleaned.columns:
        vol = cleaned["Volume"].fillna(0)
        cleaned = cleaned[vol > 0]
    return cleaned, before - len(cleaned)


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


def _recompute_snapshot_from_ohlcv(snap: dict, ohlcv: pd.DataFrame) -> dict:
    """Replace today-leaking numeric fields with values from the as-of-sliced
    OHLCV. Display fields (company, sector, industry, exchange) are preserved
    from the input snap via `overrides`. Used only in backtest mode so the
    downstream stages and UI see point-in-time values instead of live ones.

    Routes through the shared `build_snapshot_from_ohlcv` helper so live and
    backtest snapshots emit identical shape and use identical math.
    """
    symbol = snap.get("symbol") or snap.get("company") or ""
    overrides = {
        "company": snap.get("company"),
        "sector": snap.get("sector"),
        "industry": snap.get("industry"),
    }
    if snap.get("exchange"):
        overrides["exchange"] = snap["exchange"]
    return build_snapshot_from_ohlcv(symbol, ohlcv, overrides=overrides)


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

    # Fetch snapshot + OHLCV. Data-source misconfigurations (missing bhavcopy
    # cache, unreachable Yahoo) should surface as a clean [I] gate failure with
    # an actionable reason, not an uncaught crash — otherwise the composite
    # score can't tell the difference between "no data" and "no signal".
    try:
        snap = fetch_snapshot(ctx.symbol)
        ohlcv = (
            fetch_ohlcv(ctx.symbol, end=as_of_iso) if is_backtest
            else fetch_ohlcv(ctx.symbol)
        )
    except FileNotFoundError as e:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_ohlcv": False, "as_of": as_of_iso},
            fix_point="backend/.env  (DEMO_MODE=1  or  DATA_SOURCE=yahoo)",
            reason=(
                f"data source missing OHLCV: {e}. "
                "Set DEMO_MODE=1 in backend/.env for synthetic data, "
                "or DATA_SOURCE=yahoo for live fetch."
            ),
        )
    except Exception as e:
        return StageResult(
            stage_id=stage_id, passed=False,
            features={"has_ohlcv": False, "as_of": as_of_iso},
            fix_point="backend/fetch.py",
            reason=f"fetch failed: {type(e).__name__}: {e}",
        )
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

    # Finalized-bar hygiene — drop partial intraday + malformed rows BEFORE
    # the MIN_BARS check, so a fetch that returned 201 rows including one
    # NaN row and one intraday-partial row fails cleanly instead of feeding
    # noise into downstream indicators. Both counts are reported in features
    # so operators can see what got trimmed.
    ohlcv, dropped_malformed = _clean_malformed_rows(ohlcv)
    ohlcv, dropped_partial = _drop_partial_session_bar(ohlcv, is_backtest)

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
            features={
                "bars": bars,
                "min_required": MIN_BARS,
                "as_of": as_of_iso,
                "dropped_malformed": dropped_malformed,
                "dropped_partial_session": dropped_partial,
            },
            fix_point="backend/stages/ingest.py:MIN_BARS",
            reason=f"only {bars} bars, need >={MIN_BARS}",
        )
    has_full_lookback = bars >= FULL_LOOKBACK_BARS

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
        features={
            "bars": bars,
            "current": current,
            "as_of": as_of_iso,
            "has_full_lookback": has_full_lookback,
            "dropped_malformed": dropped_malformed,
            "dropped_partial_session": dropped_partial,
        },
        evidence=[
            f"{bars} daily bars · current ₹{current:.2f}"
            + (f" · as-of {as_of_iso}" if as_of_iso else "")
            + (f" · dropped {dropped_malformed}m/{dropped_partial}p"
               if (dropped_malformed or dropped_partial) else "")
        ],
        fix_point="backend/stages/ingest.py:MIN_BARS",
    )
