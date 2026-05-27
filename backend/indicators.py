"""Slim indicator library for the gates pipeline.

All functions are pure: pandas in, primitive/dataclass out.
No I/O, no logging. Lookahead-safe: only data passed in is read; the live
path passes EOD-closed bars only, the backtest path slices to t-1 itself.

Harvested from the old volume_signals.py — kept only what the four gates,
the ranker, and the position sizer consume. The Wyckoff/Weinstein/CMF/MFI/
VWAP narrative engine is deliberately not carried forward.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Moving averages
# --------------------------------------------------------------------------- #

def sma(close: pd.Series, n: int) -> Optional[float]:
    if len(close) < n:
        return None
    return float(close.iloc[-n:].mean())


def ma_stack_aligned(close: pd.Series) -> bool:
    """True if 50d > 150d > 200d (Minervini-style alignment). Used by ranker."""
    m50, m150, m200 = sma(close, 50), sma(close, 150), sma(close, 200)
    if m50 is None or m150 is None or m200 is None:
        return False
    return m50 > m150 > m200


def sma_slope_pct(close: pd.Series, period: int, lookback: int) -> Optional[float]:
    """% change in the `period`-day SMA over the last `lookback` bars.

    Compares mean of the most recent `period` closes against the mean of
    `period` closes ending `lookback` bars ago. Returns the difference as a
    percentage. Used by the long-term gate to confirm the 150d MA is rising.
    """
    if len(close) < period + lookback:
        return None
    ma_now = float(close.iloc[-period:].mean())
    ma_then = float(close.iloc[-(period + lookback):-lookback].mean())
    if ma_then <= 0:
        return None
    return (ma_now / ma_then - 1) * 100


def up_down_vol_ratio(close: pd.Series, volume: pd.Series, n: int) -> Optional[float]:
    """Sum of volume on up-days / sum of volume on down-days over the last n bars.

    Saturates at 5.0 when there are no down-day volumes. Returns None on
    insufficient history or no movement at all.
    """
    if len(close) < n + 1:
        return None
    deltas = close.diff()
    last_n_deltas = deltas.iloc[-n:]
    last_n_vol = volume.iloc[-n:]
    up_vol = float(last_n_vol[last_n_deltas > 0].sum())
    down_vol = float(last_n_vol[last_n_deltas < 0].sum())
    if down_vol > 0:
        return up_vol / down_vol
    if up_vol > 0:
        return 5.0
    return None


# --------------------------------------------------------------------------- #
# True range / ATR — simple-mean variant (faster, slightly more responsive
# than Wilder's RMA recurrence; swap to Wilder if you want classic ATR).
# --------------------------------------------------------------------------- #

def atr(df: pd.DataFrame, n: int = 14) -> Optional[float]:
    if len(df) < n + 1:
        return None
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return float(tr.iloc[-n:].mean())


def atr_pct(df: pd.DataFrame, n: int = 14) -> Optional[float]:
    """ATR as % of last close — the consolidation-gate tightness metric."""
    a = atr(df, n)
    if a is None:
        return None
    last = float(df["Close"].iloc[-1])
    if last <= 0:
        return None
    return a / last * 100


# --------------------------------------------------------------------------- #
# Average daily volume
# --------------------------------------------------------------------------- #

def adv(volume: pd.Series, n: int) -> Optional[float]:
    if len(volume) < n:
        return None
    return float(volume.iloc[-n:].mean())


# --------------------------------------------------------------------------- #
# Rolling resistance / support
# --------------------------------------------------------------------------- #

def rolling_high(high: pd.Series, n: int, exclude_today: bool = True) -> Optional[float]:
    """Highest high over the prior n bars. exclude_today=True is the breakout
    resistance the current bar's close must exceed."""
    if exclude_today:
        if len(high) < n + 1:
            return None
        return float(high.iloc[-(n + 1):-1].max())
    if len(high) < n:
        return None
    return float(high.iloc[-n:].max())


def rolling_low(low: pd.Series, n: int, exclude_today: bool = True) -> Optional[float]:
    if exclude_today:
        if len(low) < n + 1:
            return None
        return float(low.iloc[-(n + 1):-1].min())
    if len(low) < n:
        return None
    return float(low.iloc[-n:].min())


# --------------------------------------------------------------------------- #
# Candle anatomy
# --------------------------------------------------------------------------- #

def upper_third_ratio(row: pd.Series) -> Optional[float]:
    """(Close - Low) / (High - Low) for one bar. >= 0.67 = closed in upper third."""
    h, l, c = float(row["High"]), float(row["Low"]), float(row["Close"])
    rng = h - l
    if rng <= 0:
        return None
    return (c - l) / rng


def last_bar_upper_third_ratio(df: pd.DataFrame) -> Optional[float]:
    if df is None or df.empty:
        return None
    return upper_third_ratio(df.iloc[-1])


# --------------------------------------------------------------------------- #
# OBV + slope + bullish divergence
# --------------------------------------------------------------------------- #

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Granville On-Balance Volume — cumulative signed-volume series."""
    direction = close.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def obv_slope_pct(obv_series: pd.Series, n: int) -> Optional[float]:
    """% change in OBV over the last n bars."""
    if len(obv_series) < n + 1:
        return None
    base = obv_series.iloc[-(n + 1)]
    if base == 0:
        return None
    return float((obv_series.iloc[-1] / base - 1) * 100)


@dataclass
class DivergenceResult:
    is_bullish: bool
    form: str                       # "classic" | "flat-price" | "none"
    price_low_early: Optional[float]
    price_low_recent: Optional[float]
    obv_at_early_low: Optional[float]
    obv_at_recent_low: Optional[float]
    detail: str


def obv_bullish_divergence(df: pd.DataFrame, lookback: int = 20) -> DivergenceResult:
    """Bullish OBV-price divergence over the last `lookback` bars.

    Split the window in half. Find the lowest close in each half. Two forms:
      - Classic: price made a lower low while OBV made a higher low.
      - Flat-price: price within +/-2% across the two halves while OBV is
        meaningfully (>= 2%) higher at the second low.
    """
    if df is None or len(df) < lookback + 2:
        return DivergenceResult(False, "none", None, None, None, None, "Insufficient bars.")

    closes = df["Close"].iloc[-lookback:].reset_index(drop=True)
    obv_full = obv(df["Close"], df["Volume"])
    obv_window = obv_full.iloc[-lookback:].reset_index(drop=True)

    half = lookback // 2
    if half < 3:
        return DivergenceResult(False, "none", None, None, None, None, "Lookback too small.")

    left_lo = int(np.argmin(closes.iloc[:half].values))
    right_lo = half + int(np.argmin(closes.iloc[half:].values))

    p1 = float(closes.iloc[left_lo])
    p2 = float(closes.iloc[right_lo])
    o1 = float(obv_window.iloc[left_lo])
    o2 = float(obv_window.iloc[right_lo])

    if p2 < p1 and o2 > o1:
        return DivergenceResult(
            True, "classic", p1, p2, o1, o2,
            f"Price LL {p1:.2f}->{p2:.2f} ({(p2/p1-1)*100:+.1f}%) "
            f"while OBV HL ({(o2-o1)/max(abs(o1),1)*100:+.1f}%).",
        )

    price_flat = p1 > 0 and abs(p2 / p1 - 1) < 0.02
    obv_up = (o2 - o1) / max(abs(o1), 1) > 0.02
    if price_flat and obv_up:
        return DivergenceResult(
            True, "flat-price", p1, p2, o1, o2,
            f"Price flat ({p1:.2f}~{p2:.2f}) but OBV "
            f"{(o2-o1)/max(abs(o1),1)*100:+.1f}%.",
        )

    return DivergenceResult(
        False, "none", p1, p2, o1, o2,
        f"No divergence (price {(p2/p1-1)*100 if p1 else 0:+.1f}%, "
        f"OBV {(o2-o1)/max(abs(o1),1)*100:+.1f}%).",
    )


# --------------------------------------------------------------------------- #
# Consolidation duration helper
# --------------------------------------------------------------------------- #

def days_within_band(close: pd.Series, band_pct: float = 0.10) -> int:
    """Walking back from the last bar, count consecutive bars whose close
    stayed within +/-band_pct of the last close. Used for 5-8w range check."""
    if close.empty:
        return 0
    last = float(close.iloc[-1])
    if last <= 0:
        return 0
    days = 0
    for i in range(len(close) - 1, -1, -1):
        if abs(close.iloc[i] / last - 1) <= band_pct:
            days += 1
        else:
            break
    return days


def in_range_for(close: pd.Series, min_days: int, max_days: int, band_pct: float = 0.10) -> bool:
    """True if the in-band streak length falls in [min_days, max_days]."""
    d = days_within_band(close, band_pct)
    return min_days <= d <= max_days
