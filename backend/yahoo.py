"""Thin wrappers over yfinance with safe `None` handling.

Returns only what the volume-only pipeline needs: price + OHLCV history +
labels (sector/industry are kept for display, no numeric fundamentals).

Set DEMO_MODE=1 to bypass yfinance entirely and serve bundled fixtures —
useful when running behind a network that blocks Yahoo Finance.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from .demo_data import DEMO_SNAPSHOTS, demo_history_6m


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
    return yf.Ticker(symbol)


def snapshot(symbol: str) -> dict:
    """Price + display snapshot for one ticker. No numeric fundamentals."""
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
    try:
        info: dict = t.info or {}
    except Exception:
        info = {}

    try:
        hist: pd.DataFrame = t.history(period="1y", auto_adjust=False)
    except Exception:
        hist = pd.DataFrame()

    current = _to_float(info.get("currentPrice")) or _to_float(info.get("regularMarketPrice"))
    prev_close = _to_float(info.get("previousClose"))
    if current is None and not hist.empty:
        current = _to_float(hist["Close"].iloc[-1])
    if prev_close is None and len(hist) >= 2:
        prev_close = _to_float(hist["Close"].iloc[-2])

    day_change_pct = None
    if current is not None and prev_close not in (None, 0):
        day_change_pct = round((current - prev_close) / prev_close * 100, 2)

    ma50 = ma200 = None
    return_3m = return_1y = None
    fifty_two_high = fifty_two_low = None
    vol_today = vol_avg30 = None

    if not hist.empty:
        closes = hist["Close"].dropna()
        if len(closes) >= 50:
            ma50 = _to_float(closes.tail(50).mean())
        if len(closes) >= 200:
            ma200 = _to_float(closes.tail(200).mean())
        if len(closes) >= 65:
            return_3m = _to_float((closes.iloc[-1] / closes.iloc[-65] - 1) * 100)
            return_3m = round(return_3m, 2) if return_3m is not None else None
        if len(closes) >= 2:
            return_1y = _to_float((closes.iloc[-1] / closes.iloc[0] - 1) * 100)
            return_1y = round(return_1y, 2) if return_1y is not None else None
        fifty_two_high = _to_float(closes.max())
        fifty_two_low = _to_float(closes.min())

        vols = hist["Volume"].dropna()
        if len(vols) >= 1:
            vol_today = _to_float(vols.iloc[-1])
        if len(vols) >= 30:
            vol_avg30 = _to_float(vols.tail(30).mean())

    return {
        "symbol": symbol,
        "company": info.get("longName") or info.get("shortName") or symbol,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "current": current,
        "day_change_pct": day_change_pct,
        "fifty_two_w_high": _to_float(info.get("fiftyTwoWeekHigh")) or fifty_two_high,
        "fifty_two_w_low": _to_float(info.get("fiftyTwoWeekLow")) or fifty_two_low,
        "ma50": ma50,
        "ma200": ma200,
        "return_3m_pct": return_3m,
        "return_1y_pct": return_1y,
        "vol_today": vol_today,
        "vol_avg30": vol_avg30,
    }


def history_ohlcv(symbol: str) -> pd.DataFrame:
    """Return ~1 year of daily OHLCV for the volume engine.

    1 year is the minimum window we need for a long-term lens:
    - 30-week (150-day) moving average for Stan Weinstein Stage Analysis
    - 200-day MA + slope for Minervini's Trend Template
    - Quarter-over-quarter volume comparisons
    - Multi-month base detection

    Columns: Open, High, Low, Close, Volume. Index: date. Empty on failure.
    """
    if _demo_enabled():
        from .demo_data import demo_ohlcv
        return demo_ohlcv(symbol)
    t = _ticker(symbol)
    try:
        h = t.history(period="1y", auto_adjust=False)
    except Exception:
        return pd.DataFrame()
    if h is None or h.empty:
        return pd.DataFrame()
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in h.columns]
    return h[cols].dropna(subset=["Close"]).copy()


def history_ohlcv_6m(symbol: str) -> pd.DataFrame:
    return history_ohlcv(symbol)


def history_6m(symbol: str) -> list[dict]:
    """Return list of {date, close} for the last ~6 months (UI sparkline)."""
    if _demo_enabled():
        return demo_history_6m(symbol)
    t = _ticker(symbol)
    try:
        hist = t.history(period="6mo", auto_adjust=False)
    except Exception:
        return []
    if hist.empty:
        return []
    out: list[dict] = []
    for ts, row in hist.iterrows():
        close = _to_float(row.get("Close"))
        if close is None:
            continue
        out.append({"date": ts.strftime("%Y-%m-%d"), "close": round(close, 2)})
    return out
