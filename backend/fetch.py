"""Pluggable data-source abstraction.

Single entry point the rest of the data layer uses. Switches between:
1. NSE bhavcopy — current default, India-native, populated by the
   Stockya-tuner ingestor (`Stockya-tuner/app/bhavcopy_ingest.py`)
2. Yahoo Finance (yfinance) — fallback, free, blocked on some networks
3. Demo fixtures — DEMO_MODE=1, hand-coded synthetic OHLCV

Add new sources by writing a new `_fetch_via_*` function and routing in `fetch()`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd

from .snapshot_calc import build_snapshot_from_ohlcv

# Yahoo (yfinance) imports are deferred inside the route below so that the
# default `bhavcopy` path doesn't carry a hard dep on yfinance.


def _demo_enabled() -> bool:
    """DEMO_MODE takes precedence over DATA_SOURCE.

    Rationale: on a firewalled corporate machine, DATA_SOURCE=yahoo would try
    to hit the internet and DATA_SOURCE=bhavcopy would read a cache the user
    hasn't populated. Setting DEMO_MODE=1 as a single env should be enough to
    get the app producing (synthetic) picks without editing anything else.
    """
    return os.environ.get("DEMO_MODE", "0") == "1"


def fetch_ohlcv(
    symbol: str,
    end: Optional[str] = None,
    lookback_days: int = 730,
) -> pd.DataFrame:
    """Return ~2 years of daily OHLCV for one ticker.

    Live mode (`end=None`): bars ending today.
    Backtest mode (`end="YYYY-MM-DD"`): bars ending at that historical date,
    going back `lookback_days` calendar days.

    Selection order:
        1. DEMO_MODE=1               -> synthetic OHLCV (backend/demo_data.py)
        2. DATA_SOURCE=yahoo         -> yfinance wrapper
        3. DATA_SOURCE=bhavcopy      -> local NSE bhavcopy CSV cache
        4. (default)  bhavcopy
    """
    if _demo_enabled():
        from .yahoo import history_ohlcv as _demo_history
        return _demo_history(symbol, end=end, lookback_days=lookback_days)
    src = os.environ.get("DATA_SOURCE", "bhavcopy").lower()
    if src == "yahoo":
        from .yahoo import history_ohlcv as _yahoo_history
        return _yahoo_history(symbol, end=end, lookback_days=lookback_days)
    if src == "bhavcopy":
        return _fetch_via_bhavcopy(symbol, end=end, lookback_days=lookback_days)
    raise ValueError(f"Unknown DATA_SOURCE={src!r}")


def fetch_snapshot(symbol: str) -> dict:
    """Return today's fundamentals + headline price for one ticker."""
    if _demo_enabled():
        from .yahoo import snapshot as _demo_snapshot
        return _demo_snapshot(symbol)
    src = os.environ.get("DATA_SOURCE", "bhavcopy").lower()
    if src == "yahoo":
        from .yahoo import snapshot as _yahoo_snapshot
        return _yahoo_snapshot(symbol)
    if src == "bhavcopy":
        return _fetch_via_bhavcopy_snapshot(symbol)
    raise ValueError(f"Unknown DATA_SOURCE={src!r}")


# --- Bhavcopy data source ---------------------------------------------------
# The tuner's `app.bhavcopy_ingest` downloads NSE daily files and pivots them
# into per-symbol OHLCV CSVs. Stockya just reads those.


def _bhavcopy_ohlcv_dir() -> Path:
    """Where the tuner-pivoted per-symbol CSVs live.

    Default points at the sibling tuner repo. Override with
    `STOCKYA_OHLCV_DIR=/abs/path` if the tuner cache is elsewhere.
    """
    raw = os.environ.get(
        "STOCKYA_OHLCV_DIR",
        "C:/Claude_projects/Stockya-tuner/data/ohlcv",
    )
    return Path(raw).resolve()


def _resolve_bhavcopy_csv(symbol: str) -> Path:
    """Locate the per-symbol CSV, trying both bare and `.NS`-suffixed names.

    Stockya's universe loader and external callers pass symbols in either form
    (e.g. 'RELIANCE' from a bhavcopy universe file, 'RELIANCE.NS' from a Yahoo
    convention). The tuner writes one or the other depending on how the
    ingestor was invoked. We try the literal name first, then add or strip
    '.NS', so both conventions resolve.
    """
    root = _bhavcopy_ohlcv_dir()
    candidates = [f"{symbol}.csv"]
    sym_upper = symbol.upper()
    if sym_upper.endswith(".NS"):
        candidates.append(f"{symbol[:-3]}.csv")
    else:
        candidates.append(f"{symbol}.NS.csv")
    for name in candidates:
        p = root / name
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Bhavcopy CSV missing for {symbol} in {root}. Tried: {candidates}. "
        "Run the tuner's bhavcopy_ingest first: `python -m app.bhavcopy_ingest "
        f"run --symbols {symbol} --from <YYYY-MM-DD> --to <YYYY-MM-DD>`."
    )


def _fetch_via_bhavcopy(
    symbol: str,
    end: Optional[str] = None,
    lookback_days: int = 730,
) -> pd.DataFrame:
    """Read OHLCV for one ticker from the tuner-pivoted bhavcopy cache."""
    p = _resolve_bhavcopy_csv(symbol)
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df = df.sort_index()
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(
        {"Open": float, "High": float, "Low": float, "Close": float, "Volume": "int64"}
    )
    if end is not None:
        end_ts = pd.Timestamp(end)
        start_ts = end_ts - pd.Timedelta(days=lookback_days)
        df = df[(df.index >= start_ts) & (df.index <= end_ts)]
    return df


def _fetch_via_bhavcopy_snapshot(symbol: str) -> dict:
    """Snapshot from the bhavcopy OHLCV cache.

    Bhavcopy has no metadata feed (no yfinance `.info` equivalent), so
    `sector`/`industry` come back as `None` and `company` defaults to the
    symbol. All numeric fields are computed by the shared
    `build_snapshot_from_ohlcv` helper — identical math to the Yahoo path.
    """
    df = _fetch_via_bhavcopy(symbol, end=None)
    if df.empty:
        raise RuntimeError(f"Bhavcopy CSV is empty for {symbol}")
    return build_snapshot_from_ohlcv(symbol, df, overrides={"exchange": "NSE"})
