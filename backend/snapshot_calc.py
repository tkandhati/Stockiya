"""Shared OHLCV → snapshot synthesis.

Single source of truth for the dict shape that `stages/*.py` consume off
`ctx.snapshot`. All three callers route through here:

  - `backend/yahoo.py:snapshot`                       (Yahoo live, with yfinance .info overrides)
  - `backend/fetch.py:_fetch_via_bhavcopy_snapshot`   (bhavcopy live, OHLCV-only)
  - `backend/stages/ingest.py:_recompute_snapshot_from_ohlcv` (backtest as-of recompute)

Each source supplies what it knows authoritatively via `overrides`; everything
else is computed from the OHLCV frame. The canonical shape matches what
`middleware/main.py` already reads off the snapshot.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd


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


def build_snapshot_from_ohlcv(
    symbol: str,
    ohlcv: pd.DataFrame,
    overrides: Optional[dict] = None,
) -> dict:
    """Compute the canonical Stockya snapshot dict from an OHLCV frame.

    `overrides` carries fields the source knows better than OHLCV:
      - `current`, `previous_close` — preferred over last-bar / second-last-bar
      - `fifty_two_w_high`, `fifty_two_w_low` — preferred when truthy
      - `company`, `sector`, `industry` — display-only, no fallback from OHLCV
      - `exchange` — optional provenance (e.g. "NSE" for bhavcopy)

    `vol_avg30` uses a 30-bar window to match the existing Yahoo contract and
    the backtest recompute. `return_1y_pct` uses the last 252 bars so a longer
    OHLCV frame (bhavcopy has ~2y) still produces a true 1-year return.
    """
    overrides = overrides or {}

    closes = (
        ohlcv["Close"].dropna()
        if not ohlcv.empty and "Close" in ohlcv.columns
        else pd.Series(dtype=float)
    )
    vols = (
        ohlcv["Volume"].dropna()
        if not ohlcv.empty and "Volume" in ohlcv.columns
        else pd.Series(dtype=float)
    )

    last_252 = closes.tail(252) if len(closes) > 252 else closes

    current = _to_float(overrides.get("current"))
    if current is None and not closes.empty:
        current = _to_float(closes.iloc[-1])
    prev_close = _to_float(overrides.get("previous_close"))
    if prev_close is None and len(closes) >= 2:
        prev_close = _to_float(closes.iloc[-2])

    day_change_pct = None
    if current is not None and prev_close not in (None, 0):
        day_change_pct = round((current - prev_close) / prev_close * 100, 2)

    fifty_two_high = _to_float(overrides.get("fifty_two_w_high")) or (
        _to_float(last_252.max()) if not last_252.empty else None
    )
    fifty_two_low = _to_float(overrides.get("fifty_two_w_low")) or (
        _to_float(last_252.min()) if not last_252.empty else None
    )

    ma50 = _to_float(closes.tail(50).mean()) if len(closes) >= 50 else None
    ma200 = _to_float(closes.tail(200).mean()) if len(closes) >= 200 else None

    return_3m_pct: Optional[float] = None
    if len(closes) >= 65:
        r3 = _to_float((closes.iloc[-1] / closes.iloc[-65] - 1) * 100)
        return_3m_pct = round(r3, 2) if r3 is not None else None

    return_1y_pct: Optional[float] = None
    if len(last_252) >= 2:
        r1 = _to_float((last_252.iloc[-1] / last_252.iloc[0] - 1) * 100)
        return_1y_pct = round(r1, 2) if r1 is not None else None

    vol_today = _to_float(vols.iloc[-1]) if not vols.empty else None
    vol_avg30 = _to_float(vols.tail(30).mean()) if len(vols) >= 30 else None

    out = {
        "symbol": symbol,
        "company": overrides.get("company") or symbol,
        "sector": overrides.get("sector"),
        "industry": overrides.get("industry"),
        "current": current,
        "day_change_pct": day_change_pct,
        "fifty_two_w_high": fifty_two_high,
        "fifty_two_w_low": fifty_two_low,
        "ma50": ma50,
        "ma200": ma200,
        "return_3m_pct": return_3m_pct,
        "return_1y_pct": return_1y_pct,
        "vol_today": vol_today,
        "vol_avg30": vol_avg30,
    }
    if overrides.get("exchange"):
        out["exchange"] = overrides["exchange"]
    return out
