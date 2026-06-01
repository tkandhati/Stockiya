"""Pluggable data-source abstraction.

Single entry point the rest of the data layer uses. Switches between:
1. Yahoo Finance (yfinance) — current default, free, blocked on some networks
2. NSE bhavcopy (TODO) — daily ZIP from NSE itself, free, India-native
3. Demo fixtures — DEMO_MODE=1, hand-coded synthetic OHLCV

Add new sources by writing a new `_fetch_via_*` function and routing in `fetch()`.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from .yahoo import history_ohlcv as _yahoo_history
from .yahoo import snapshot as _yahoo_snapshot


def fetch_ohlcv(
    symbol: str,
    end: Optional[str] = None,
    lookback_days: int = 730,
) -> pd.DataFrame:
    """Return ~2 years of daily OHLCV for one ticker.

    Live mode (`end=None`): bars ending today.
    Backtest mode (`end="YYYY-MM-DD"`): bars ending at that historical date,
    going back `lookback_days` calendar days.

    Source is selected by env var `DATA_SOURCE`:
        - `yahoo` (default) — yfinance via the demo-mode-aware wrapper
        - `bhavcopy`        — NSE bhavcopy archive (not yet implemented)
    """
    src = os.environ.get("DATA_SOURCE", "yahoo").lower()
    if src == "yahoo":
        return _yahoo_history(symbol, end=end, lookback_days=lookback_days)
    if src == "bhavcopy":
        return _fetch_via_bhavcopy(symbol)
    raise ValueError(f"Unknown DATA_SOURCE={src!r}")


def fetch_snapshot(symbol: str) -> dict:
    """Return today's fundamentals + headline price for one ticker."""
    src = os.environ.get("DATA_SOURCE", "yahoo").lower()
    if src == "yahoo":
        return _yahoo_snapshot(symbol)
    if src == "bhavcopy":
        return _fetch_via_bhavcopy_snapshot(symbol)
    raise ValueError(f"Unknown DATA_SOURCE={src!r}")


# --- Bhavcopy stub (TODO) -----------------------------------------------------
# To wire in the NSE bhavcopy:
#   1. Nightly download https://archives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
#      (or the FO bhavcopy if you trade derivatives) into data/bhavcopy/.
#   2. Parse with pandas, store one CSV per day.
#   3. Implement these two functions to read from that local archive.
#   4. Set DATA_SOURCE=bhavcopy in .env and Yahoo never gets touched again.

def _fetch_via_bhavcopy(symbol: str) -> pd.DataFrame:
    raise NotImplementedError(
        "Bhavcopy data source is not yet implemented. "
        "See backend/fetch.py for the wiring plan."
    )


def _fetch_via_bhavcopy_snapshot(symbol: str) -> dict:
    raise NotImplementedError(
        "Bhavcopy data source is not yet implemented. "
        "See backend/fetch.py for the wiring plan."
    )
