"""Thin wrappers over yfinance with safe `None` handling.

Returns only what the volume-only pipeline needs: price + OHLCV history +
labels (sector/industry are kept for display, no numeric fundamentals).

Set DEMO_MODE=1 to bypass yfinance entirely and serve bundled fixtures —
useful when running behind a network that blocks Yahoo Finance.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import date as _date, timedelta as _td
from functools import lru_cache
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from . import yf_session
from .demo_data import DEMO_SNAPSHOTS, demo_history_6m
from .snapshot_calc import build_snapshot_from_ohlcv

log = logging.getLogger("yahoo")


# --------------------------------------------------------------------------- #
# All Yahoo access is throttled + retried + cached in `backend/yf_session.py`
# (one place, so the orchestrator can't burst Yahoo into a 429). This wrapper
# builds the request signature, applies a per-RUN in-memory memo, and delegates.
# --------------------------------------------------------------------------- #

# Per-run in-memory memo of history frames, keyed by "symbol|sig". Unlike the
# on-disk cache in yf_session (which never stores empty/failed frames), this memo
# ALSO remembers empties — so a rate-limited symbol is fetched at most ONCE per
# run instead of re-firing the heavy history request from snapshot(), then
# history_ohlcv(), then history_6m(). That triple-fetch of failed symbols was the
# "hundreds of ratelimit errors" source. `run_universe` calls clear_run_memo() at
# the start of every run, so a fresh run (or a manual "Refresh picks") still
# retries symbols that failed earlier. Reversible via STOCKYA_YF_RUN_MEMO=0.
_RUN_MEMO: dict[str, pd.DataFrame] = {}
_RUN_MEMO_LOCK = threading.Lock()


def _run_memo_enabled() -> bool:
    v = os.environ.get("STOCKYA_YF_RUN_MEMO")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def clear_run_memo() -> None:
    """Drop the per-run history memo so the next run re-attempts every symbol."""
    with _RUN_MEMO_LOCK:
        _RUN_MEMO.clear()


def forget(symbols) -> None:
    """Drop memo entries for specific symbols so the NEXT fetch re-attempts them.

    The per-run memo deliberately remembers empties (a rate-limited symbol is
    fetched at most once per run — see the module note). That is right for the
    main fan-out, but it also means a symbol dropped to an empty frame can never
    recover within the same run. The orchestrator's dropped-stock re-sweep
    (``_resweep_dropped_fetches``) calls this to forget exactly the dropped
    symbols after a cooldown, so a slower single-threaded retry actually hits
    Yahoo again instead of returning the memoized empty. No-op for symbols not
    in the memo. Accepts a single symbol or any iterable of symbols.
    """
    syms = {symbols} if isinstance(symbols, str) else set(symbols)
    if not syms:
        return
    with _RUN_MEMO_LOCK:
        for key in [k for k in _RUN_MEMO if k.split("|", 1)[0] in syms]:
            _RUN_MEMO.pop(key, None)


def fetch_lookback_days() -> int:
    """Calendar-day span of the live history window (the "required interval").

    Default ~450 days ≈ 310 trading bars — enough for the pipeline's 200d MA,
    OBV-90d, ADV50 and 150d MA to sit at full lookback (see
    ``backend/stages/ingest.py`` FULL_LOOKBACK_BARS=260 / MIN_BARS=200), and far
    less than the old blanket 2y. Floored at 400 days so those gates never
    starve. Raise STOCKYA_FETCH_LOOKBACK_DAYS toward 730 to restore ~2y.
    """
    try:
        n = int(float(os.environ.get("STOCKYA_FETCH_LOOKBACK_DAYS", 450)))
    except (TypeError, ValueError):
        n = 450
    return max(400, n)


def _history_with_retry(
    symbol: str,
    period: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """yfinance.history via the shared throttled/retried/cached session + run memo.

    Returns an empty DataFrame only after all retries are exhausted — so a
    truly-dead ticker (still empty) is distinguishable from transient Yahoo
    flakiness (recovered by retry). Rate limiting, backoff and the on-disk day
    cache are all handled in `yf_session`.

    Live calls (no `period`, no `start`/`end`) fetch the required-interval window
    (``fetch_lookback_days``) ending today — one shared, cacheable window reused
    by snapshot() / history_ohlcv() / history_6m() so they hit ONE cache+memo key
    (≈ one network call per symbol). An explicit `start`/`end` window (backtest)
    or a legacy `period` string are still honoured for back-compat.
    """
    t = _ticker(symbol)
    kwargs: dict = {"auto_adjust": True}
    if start or end:
        if start:
            kwargs["start"] = start
        if end:
            kwargs["end"] = end
        sig = f"s{start or ''}_e{end or ''}"
    elif period:
        kwargs["period"] = period
        # Day-stamp the signature so a live period fetch refreshes each day.
        sig = f"p{period}_{time.strftime('%Y-%m-%d')}"
    else:
        # Default live path: the required-interval window ending today. yfinance's
        # `end` is exclusive, so add a day. Day-stamped via the dates in `sig`.
        end_d = _date.today()
        start_d = end_d - _td(days=fetch_lookback_days())
        kwargs["start"] = start_d.isoformat()
        kwargs["end"] = (end_d + _td(days=1)).isoformat()
        sig = f"s{kwargs['start']}_e{kwargs['end']}"

    memo_on = _run_memo_enabled()
    key = f"{symbol}|{sig}"
    if memo_on:
        with _RUN_MEMO_LOCK:
            hit = _RUN_MEMO.get(key)
        if hit is not None:
            return hit.copy()

    df = yf_session.history(t, symbol, sig, **kwargs)

    if memo_on:
        stored = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        with _RUN_MEMO_LOCK:
            _RUN_MEMO[key] = stored
    return df


def _demo_enabled() -> bool:
    return os.environ.get("DEMO_MODE", "0") == "1"


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


@lru_cache(maxsize=128)
def _ticker(symbol: str) -> yf.Ticker:
    # Ticker creation (with the optional shared impersonating session) lives in
    # yf_session; the lru_cache here keeps one Ticker per symbol per process.
    return yf_session.get_ticker(symbol)


def snapshot(symbol: str) -> dict:
    """Price + display snapshot for one ticker. No numeric fundamentals.

    By default the labels (company/sector/industry) and headline price come
    from yfinance ``.info`` — the heaviest, most rate-limited Yahoo endpoint.
    Set STOCKYA_YF_SKIP_INFO=1 to skip it entirely on full-universe runs: the
    numeric fields then come from the far-lighter ``fast_info`` and labels
    degrade to None (same as the bhavcopy path; sector is supplied elsewhere
    from config/sector_map.json).
    """
    if _demo_enabled():
        demo = DEMO_SNAPSHOTS.get(symbol)
        if demo:
            return dict(demo)
        return {
            "symbol": symbol, "company": symbol, "sector": None, "industry": None,
            "current": None, "day_change_pct": None,
            "fifty_two_w_high": None, "fifty_two_w_low": None,
            "ma50": None, "ma200": None,
            "return_3m_pct": None, "return_1y_pct": None,
            "vol_today": None, "vol_avg30": None,
        }
    t = _ticker(symbol)
    if yf_session.skip_info():
        # DEFAULT: no per-ticker label/quote network call at all. `fast_info`
        # used to add one Yahoo request per ticker, but every NUMERIC field it
        # provided (last_price, previous_close, year_high/low) is already derived
        # from the OHLCV frame by `build_snapshot_from_ohlcv`, and the labels
        # (company/sector/industry) were already None on this path (sector comes
        # from config/sector_map.json elsewhere). Dropping it takes the live
        # Yahoo cost to exactly ONE history call per ticker — the whole point of
        # "#calls == #stocks". Set STOCKYA_YF_SKIP_INFO=0 to restore .info labels.
        overrides = {"company": symbol, "sector": None, "industry": None}
    else:
        info: dict = yf_session.info(t, symbol)
        overrides = {
            "company": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "current": _to_float(info.get("currentPrice")) or _to_float(info.get("regularMarketPrice")),
            "previous_close": _to_float(info.get("previousClose")),
            "fifty_two_w_high": _to_float(info.get("fiftyTwoWeekHigh")),
            "fifty_two_w_low": _to_float(info.get("fiftyTwoWeekLow")),
        }

    # Fetch the SAME required-interval window the ingest stage fetches
    # (`history_ohlcv`). `build_snapshot_from_ohlcv` only ever reads trailing
    # slices (last 252 for 52w-high/low + 1y return, tail(50/200/30)), so the
    # shared window yields identical snapshot numbers — and sharing it means this
    # call and history_ohlcv() hit ONE cache+memo key, so only the first is a
    # real Yahoo request and the rest are free.
    hist = _history_with_retry(symbol)
    return build_snapshot_from_ohlcv(symbol, hist, overrides=overrides)


def history_ohlcv(
    symbol: str,
    end: Optional[str] = None,
    lookback_days: int = 730,
) -> pd.DataFrame:
    """Return daily OHLCV for the volume engine.

    Live mode (`end=None`): the required-interval window ending today
    (``fetch_lookback_days``, ~450 calendar days ≈ 310 trading bars). That is
    enough for the long-term lens at full lookback:
      - 30-week (150-day) MA for Stan Weinstein Stage Analysis
      - 200-day MA + slope for Minervini's Trend Template
      - Multi-month base detection
    and far less than the old blanket 2y (a Yahoo-load cut). Raise
    STOCKYA_FETCH_LOOKBACK_DAYS toward 730 to widen the window back to ~2y.

    Backtest mode (`end="YYYY-MM-DD"`): bars from `end - lookback_days` to
    `end` inclusive (default lookback = 730 calendar days). Fetch ends at
    `end + 1 day` because yfinance's `end` is exclusive.

    Columns: Open, High, Low, Close, Volume. Index: date. Empty on failure.
    """
    if _demo_enabled():
        from .demo_data import demo_ohlcv
        return demo_ohlcv(symbol)

    if end:
        try:
            end_d = _date.fromisoformat(end)
        except ValueError:
            return pd.DataFrame()
        start_d = end_d - _td(days=lookback_days)
        h = _history_with_retry(
            symbol,
            start=start_d.isoformat(),
            end=(end_d + _td(days=1)).isoformat(),
        )
    else:
        h = _history_with_retry(symbol)

    if h.empty:
        return pd.DataFrame()
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in h.columns]
    return h[cols].dropna(subset=["Close"]).copy()


def history_ohlcv_6m(symbol: str) -> pd.DataFrame:
    return history_ohlcv(symbol)


def history_6m(symbol: str) -> list[dict]:
    """Return list of {date, close} for the last ~6 months (UI sparkline)."""
    if _demo_enabled():
        return demo_history_6m(symbol)
    # Reuse the shared required-interval window (same cache+memo key as
    # snapshot/ingest) and slice the last ~126 trading days (~6 months) locally,
    # rather than firing a separate Yahoo request on its own cache key.
    hist = _history_with_retry(symbol)
    if hist.empty:
        return []
    hist = hist.tail(126)
    out: list[dict] = []
    for ts, row in hist.iterrows():
        close = _to_float(row.get("Close"))
        if close is None:
            continue
        out.append({"date": ts.strftime("%Y-%m-%d"), "close": round(close, 2)})
    return out
