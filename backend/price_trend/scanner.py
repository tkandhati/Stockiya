"""Pure price-structure scoring for stocks approaching a breakout.

Only High/Low/Close structure is considered. Volume is deliberately ignored
so this strategy cannot bleed into Stockya's accumulation pipeline.
"""

from __future__ import annotations

from math import isfinite

import pandas as pd

from .models import PricePoint, PriceTrendCandidate


RESISTANCE_DAYS = 55
MIN_HISTORY = 170
MAX_DISTANCE_PCT = 8.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _sma(close: pd.Series, days: int, offset: int = 0) -> float:
    end = len(close) - offset
    return float(close.iloc[end - days:end].mean())


def _atr_pct(df: pd.DataFrame, days: int = 14) -> float:
    recent = df.iloc[-(days + 1):]
    previous_close = recent["Close"].shift(1)
    true_range = pd.concat(
        [
            (recent["High"] - recent["Low"]).abs(),
            (recent["High"] - previous_close).abs(),
            (recent["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    close = float(recent["Close"].iloc[-1])
    return float(true_range.iloc[-days:].mean()) / close * 100


def _date_label(value: object) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def scan_symbol(
    symbol: str,
    ohlcv: pd.DataFrame,
    *,
    company: str | None = None,
) -> PriceTrendCandidate | None:
    """Return a price-only pre-breakout candidate, or ``None`` if irrelevant."""
    if ohlcv is None or ohlcv.empty:
        return None
    required = ["High", "Low", "Close"]
    if any(column not in ohlcv.columns for column in required):
        return None

    # Intentional input firewall: Volume cannot affect this strategy.
    df = ohlcv[required].copy()
    for column in required:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=required)
    df = df[(df["Close"] > 0) & (df["High"] > 0) & (df["Low"] > 0)]
    if len(df) < MIN_HISTORY:
        return None

    close_series = df["Close"]
    close = float(close_series.iloc[-1])
    ma20 = _sma(close_series, 20)
    ma50 = _sma(close_series, 50)
    ma150 = _sma(close_series, 150)
    ma50_then = _sma(close_series, 50, offset=10)
    ma50_slope = (ma50 / ma50_then - 1) * 100

    resistance = float(df["High"].iloc[-(RESISTANCE_DAYS + 1):-1].max())
    if resistance <= 0:
        return None
    distance = (resistance - close) / resistance * 100

    # Approaching pivots only: exclude completed breakouts and distant prices.
    if distance < -0.35 or distance > MAX_DISTANCE_PCT or close <= ma150:
        return None

    recent_10 = df.iloc[-10:]
    range_10 = (
        (float(recent_10["High"].max()) - float(recent_10["Low"].min()))
        / close * 100
    )
    atr_14 = _atr_pct(df)
    prior_lows = float(df["Low"].iloc[-10:-5].mean())
    recent_lows = float(df["Low"].iloc[-5:].mean())
    higher_low = (recent_lows / prior_lows - 1) * 100
    return_20 = (close / float(close_series.iloc[-21]) - 1) * 100
    recent_floor = float(recent_10["Low"].min())
    support = max(recent_floor, ma20) if ma20 <= close else recent_floor

    numbers = [
        close, ma20, ma50, ma150, ma50_slope, resistance, distance,
        range_10, atr_14, higher_low, return_20, support,
    ]
    if not all(isfinite(value) for value in numbers):
        return None

    trend_points = sum(
        [
            8.0 if close > ma20 else 0.0,
            8.0 if close > ma50 else 0.0,
            8.0 if ma50 > ma150 else 0.0,
            6.0 if ma50_slope > 0 else 0.0,
        ]
    )
    proximity_points = 30.0 * _clamp(
        (MAX_DISTANCE_PCT - max(distance, 0)) / MAX_DISTANCE_PCT
    )
    compression_points = 20.0 * _clamp((12.0 - range_10) / 10.0)
    higher_low_points = 12.0 * _clamp((higher_low + 0.5) / 3.0)
    extension_points = 8.0 * _clamp((15.0 - max(return_20, 0)) / 12.0)
    score = round(
        proximity_points + compression_points + trend_points
        + higher_low_points + extension_points,
        1,
    )

    ready = all(
        [
            -0.25 <= distance <= 2.5,
            close > ma20 > ma50 > ma150,
            ma50_slope > 0,
            range_10 <= 7.0,
            atr_14 <= 4.5,
            higher_low > 0,
            return_20 <= 12.0,
        ]
    )
    forming = all(
        [
            distance <= 5.0,
            close > ma50 > ma150,
            ma50_slope > 0,
            range_10 <= 10.0,
            atr_14 <= 5.5,
            return_20 <= 15.0,
        ]
    )
    status = "ready" if ready else "forming" if forming else "watch"

    reasons: list[str] = []
    if distance <= 2.5:
        reasons.append(f"Only {max(distance, 0):.1f}% below the 55-day price pivot")
    else:
        reasons.append(f"Price is {distance:.1f}% below the 55-day pivot")
    if ma50 > ma150 and ma50_slope > 0:
        reasons.append(f"50-day trend is rising ({ma50_slope:+.1f}% over 10 days)")
    if range_10 <= 7.0:
        reasons.append(f"10-day price range tightened to {range_10:.1f}%")
    if higher_low > 0:
        reasons.append(f"Recent lows stepped {higher_low:.1f}% higher")

    watchouts: list[str] = []
    if close <= ma20:
        watchouts.append("Price is still below its 20-day average")
    if ma50 <= ma150:
        watchouts.append("50-day trend has not cleared the 150-day trend")
    if ma50_slope <= 0:
        watchouts.append("50-day trend is not rising yet")
    if range_10 > 7.0:
        watchouts.append(f"10-day range is still wide at {range_10:.1f}%")
    if higher_low <= 0:
        watchouts.append("Recent swing lows are not rising yet")
    if return_20 > 12.0:
        watchouts.append(f"Price has already moved {return_20:.1f}% in 20 days")

    history = [
        PricePoint(date=_date_label(index), close=round(float(value), 2))
        for index, value in close_series.iloc[-60:].items()
    ]

    return PriceTrendCandidate(
        symbol=symbol,
        company=company or symbol.removesuffix(".NS"),
        status=status,
        score=score,
        as_of=history[-1].date,
        close=round(close, 2),
        breakout_price=round(resistance, 2),
        distance_to_breakout_pct=round(distance, 2),
        support_price=round(support, 2),
        ma20=round(ma20, 2),
        ma50=round(ma50, 2),
        ma150=round(ma150, 2),
        ma50_slope_10d_pct=round(ma50_slope, 2),
        range_10d_pct=round(range_10, 2),
        atr_14d_pct=round(atr_14, 2),
        higher_low_pct=round(higher_low, 2),
        return_20d_pct=round(return_20, 2),
        reasons=reasons[:4],
        watchouts=watchouts[:3],
        price_history=history,
    )
