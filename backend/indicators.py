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
from typing import Literal, Optional

import numpy as np
import pandas as pd

VolumeEventDirection = Literal["bullish", "bearish", "neutral"]
VolumeEventKind = Literal[
    "bullish_ignition",
    "early_accumulation",
    "support_absorption",
    "bearish_distribution",
    "climax_warning",
    "neutral",
]


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


def adaptive_windows(
    df: pd.DataFrame,
    base: int = 20,
    atr_n: int = 20,
    normal_atr_pct: float = 2.0,
    scale_min: float = 0.5,
    scale_max: float = 2.0,
    w_min: int = 5,
    w_max: int = 60,
) -> tuple[int, ...]:
    """Per-ticker adaptive window triplet, anchored by realized volatility.

    Mathematics:
        atr20_pct = ATR(20) / close                    # ticker's daily range %
        scale     = clamp(normal / atr20_pct, .5, 2.)  # smaller for high-vol
        W_center  = base * scale
        windows   = (W_center / 2, W_center, W_center * 2)  clamped to [5, 60]

    Rationale:
        Fixed windows assume every stock's accumulation base is exactly W bars
        long, regardless of how fast the stock moves. High-vol stocks compress
        and break out in shorter timeframes; low-vol stocks build longer bases.
        Scaling by realized ATR% makes the scan reach *per ticker* — no shared
        (10, 20, 40) rule. Deterministic; pure function of df.

    Fallback: if ATR is unavailable, returns (base // 2, base, base * 2)
    clamped — same behavior as the previous fixed default.
    """
    a_pct = atr_pct(df, atr_n)
    if a_pct is None or a_pct <= 0:
        w_c = base
    else:
        scale = normal_atr_pct / max(a_pct, 0.1)
        scale = max(scale_min, min(scale_max, scale))
        w_c = int(round(base * scale))
    w_c = max(w_min, min(w_max, w_c))
    triplet = (
        max(w_min, w_c // 2),
        w_c,
        min(w_max, w_c * 2),
    )
    # De-dupe in the edge case where clamping collapses two adjacent windows.
    seen: list[int] = []
    for w in triplet:
        if w not in seen:
            seen.append(w)
    return tuple(seen)


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
# Accumulation primitives — tight-range, volume dryness, ADI, slope
#
# Used by [ACS] tier-1 cheap screen and [AC] tier-2 full accumulation stage.
# All functions are pure and deterministic — no I/O, no randomness. Same
# input always yields the same output. Tuner imports these directly.
# --------------------------------------------------------------------------- #

def range_pct_window(df: pd.DataFrame, n: int) -> Optional[float]:
    """(max High - min Low) over the last n bars, as a fraction of mean Close.

    The tight-range measure for accumulation: a coiling stock stays inside a
    narrow band. Returns None on insufficient bars or non-positive mean price.
    """
    if df is None or len(df) < n:
        return None
    window = df.iloc[-n:]
    hi = float(window["High"].max())
    lo = float(window["Low"].min())
    mean_close = float(window["Close"].mean())
    if mean_close <= 0:
        return None
    return (hi - lo) / mean_close


def vol_dryness_ratio(volume: pd.Series, n: int) -> Optional[float]:
    """mean(volume last n bars) / mean(volume prior n bars).

    < 1.0 means volume is drying up in the recent window (the "quiet buying"
    footprint — institutions absorbing without lifting the price). Returns
    None on insufficient bars or zero prior-window mean.
    """
    if volume is None or len(volume) < 2 * n:
        return None
    recent = float(volume.iloc[-n:].mean())
    prior = float(volume.iloc[-2 * n : -n].mean())
    if prior <= 0:
        return None
    return recent / prior


def volume_robust_zscore(volume: pd.Series, n: int = 50) -> Optional[float]:
    """Per-ticker robust z-score of today's volume vs its trailing distribution.

    z = 0.6745 · (v_today - median(v, n)) / MAD(v, n)

    Median + MAD are robust to volume's fat right tail (a handful of spike
    days won't inflate the "normal" baseline). The 0.6745 factor makes the
    statistic comparable to a standard normal z (consistent under Gaussian).

    Additive to the existing `vol_ratio_today_50d`: this measure treats a
    sleepy large-cap and a hyperactive small-cap differently, so anomalies
    stand out on their own tape rather than against a shared multiplier.

    Returns None on <n+1 bars or when MAD is zero (flat volume history).
    """
    if volume is None or len(volume) < n + 1:
        return None
    prior = volume.iloc[-(n + 1):-1].astype(float)
    today = float(volume.iloc[-1])
    med = float(prior.median())
    mad = float((prior - med).abs().median())
    if mad <= 0:
        return None
    return 0.6745 * (today - med) / mad


def dry_up_streak_days(
    volume: pd.Series,
    n: int = 50,
    percentile: float = 25.0,
) -> int:
    """Consecutive trailing sessions with volume below the p-th percentile
    of the last n bars (default 25 %).

    Streak-based, not a single snapshot: a 6-day quiet run inside a coil is
    a stronger pre-breakout tell than one dry bar. Companion to (not a
    replacement for) `vol_dryness_ratio`, which stays available for existing
    multi-lookback checks.

    Returns 0 on insufficient history.
    """
    if volume is None or len(volume) < n + 1 or not (0.0 < percentile < 100.0):
        return 0
    prior = volume.iloc[-(n + 1):-1].astype(float)
    threshold = float(np.percentile(prior.to_numpy(), percentile))
    streak = 0
    for i in range(len(volume) - 1, -1, -1):
        if float(volume.iloc[i]) < threshold:
            streak += 1
        else:
            break
    return streak


def anomaly_cluster_count(
    volume: pd.Series,
    n: int = 50,
    lookback: int = 15,
    z_threshold: float = 2.0,
) -> int:
    """Count of sessions in the trailing `lookback` window whose robust
    z-score exceeded `z_threshold` (positive-tail spikes).

    A single pocket-pivot day is noisy; 2–3 spike days inside a tight base is
    the classic institutional-footprint cluster. Deterministic per-ticker.
    """
    if volume is None or len(volume) < n + lookback:
        return 0
    count = 0
    for i in range(len(volume) - lookback, len(volume)):
        window = volume.iloc[i - n : i].astype(float)
        med = float(window.median())
        mad = float((window - med).abs().median())
        if mad <= 0:
            continue
        z = 0.6745 * (float(volume.iloc[i]) - med) / mad
        if z >= z_threshold:
            count += 1
    return count


# --------------------------------------------------------------------------- #
# Signed volume pressure (2026-07-17)
#
# Foundation for the precision-first accumulation refit. Two properties matter:
#
#   1. Signed at the bar level — CLV × RV captures who won the day rather than
#      how much traded. Institutions "play tricks" with engineered spikes;
#      unsigned volume (ADV ratios, OBV cumulatives) can't tell an absorbed
#      spike from a distributed one. Signed pressure can.
#   2. Spike-dampened — RV is clipped at rv_clip (default 3.0) and downstream
#      aggregation uses EWM, not point-in-time reads. A single earnings-day
#      print cannot dominate a 20-bar aggregate, so the "temp spike" trick is
#      neutralized by construction, not by a heuristic filter.
#
# These primitives ship pure and unwired — the composite weight is 0 by
# default (see config/stage_weights.json). Only the trace records them until
# the champion-challenger tuner sees a positive shadow-mode correlation.
# --------------------------------------------------------------------------- #

def close_location_value(
    open_: float,
    high: float,
    low: float,
    close: float,
) -> Optional[float]:
    """Close Location Value (CLV) — signed intra-bar close position.

    CLV_t = (2·C - H - L) / (H - L)

    Range [-1, +1]. +1 = closed at high (buyers won), -1 = closed at low
    (sellers won), 0 = mid-range. Signed foundation for pressure metrics
    that multiply CLV by relative volume. `open_` is accepted but not used
    yet — kept in the signature so a future gap-adjusted variant can slot
    in without breaking callers.

    Returns None on zero-range bars (H == L).
    """
    del open_  # reserved for gap-adjusted variant; unused today
    rng = high - low
    if rng <= 0:
        return None
    clv = (2.0 * close - high - low) / rng
    if clv > 1.0:
        return 1.0
    if clv < -1.0:
        return -1.0
    return clv


def signed_volume_pressure(
    df: pd.DataFrame,
    adv_window: int = 60,
    rv_clip: float = 3.0,
) -> Optional[pd.Series]:
    """Per-bar signed pressure series: CLV_t × clip(V_t / median(V, N), 0, rv_clip).

    Positive = buyer-dominated bar with real volume behind it; negative =
    seller-dominated with volume; zero-range days contribute 0.

    Design choices worth calling out:

      • RV uses the trailing MEDIAN (not mean) for the same fat-tail reason
        `volume_robust_zscore` does — a handful of spike days won't inflate
        the "normal" baseline that today's bar is measured against.
      • RV is clipped at rv_clip=3.0. A 10× institutional dump on earnings
        day still registers as pressure but cannot single-handedly swing a
        20-bar EWM aggregate. This is the anti-"engineered spike" property.
      • Returns a full pd.Series so downstream code can pick its own horizon
        (5, 20, 60 bars) via ewm_signed_pressure. No fixed lookback baked in.

    Returns None on <adv_window+1 bars or if required columns are missing.
    """
    if df is None or df.empty:
        return None
    required = {"High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        return None
    if len(df) < adv_window + 1:
        return None

    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    rng = (high - low).replace(0.0, np.nan)
    clv = ((2.0 * close - high - low) / rng).clip(-1.0, 1.0).fillna(0.0)

    vol_median = volume.rolling(adv_window, min_periods=adv_window).median()
    rv = (volume / vol_median).clip(lower=0.0, upper=rv_clip).fillna(0.0)

    return clv * rv


def ewm_signed_pressure(pressure: pd.Series, halflife: int) -> Optional[float]:
    """Latest EWM value of a signed-pressure series with the given halflife.

    Exponential decay lets the aggregator emphasize recent bars without
    inheriting the single-day sensitivity of a spot read. Halflife choices
    map to horizons the plan calls out:

        halflife=3   ≈  5-bar sensitivity  (tape this week)
        halflife=10  ≈ 20-bar             (mid-swing flow)
        halflife=30  ≈ 60-bar             (base-period flow)

    Returns None on empty or all-NaN input.
    """
    if pressure is None or pressure.empty or halflife <= 0:
        return None
    valid = pressure.dropna()
    if valid.empty:
        return None
    ewm_series = valid.ewm(halflife=halflife, adjust=False).mean()
    return float(ewm_series.iloc[-1])


def adi(df: pd.DataFrame) -> Optional[pd.Series]:
    """Accumulation/Distribution Line — cumulative money-flow volume series.

    ADI_t = ADI_{t-1} + [((Close-Low) - (High-Close)) / (High-Low)] * Volume

    A rising ADI while price is flat/falling is the "positive divergence"
    signature of quiet accumulation. Returns None if OHLCV columns are missing.
    Zero-range bars contribute 0 (undefined money-flow multiplier).
    """
    if df is None or df.empty:
        return None
    required = {"High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        return None
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / rng
    mfm = mfm.fillna(0.0)
    return (mfm * vol).cumsum()


def norm_slope(y: pd.Series, n: Optional[int] = None) -> Optional[float]:
    """Linear-regression slope of y over the last n points (or all of y if
    n is None), normalized by mean(|y|). Returns slope-per-bar as a fraction
    of the typical level — comparable across tickers and across series of
    different absolute magnitudes (e.g. Close vs ADI). Returns None on <3
    valid points or when mean(|y|) is zero.
    """
    if y is None:
        return None
    series = y.dropna()
    if n is not None:
        if len(series) < n:
            return None
        series = series.iloc[-n:]
    if len(series) < 3:
        return None
    arr = series.to_numpy(dtype=float)
    x = np.arange(len(arr), dtype=float)
    slope = float(np.polyfit(x, arr, 1)[0])
    denom = float(np.mean(np.abs(arr)))
    if denom == 0:
        return None
    return slope / denom


# --------------------------------------------------------------------------- #
# OBV + slope + bullish divergence
# --------------------------------------------------------------------------- #

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Granville On-Balance Volume — cumulative signed-volume series."""
    direction = close.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def obv_slope_pct(obv_series: pd.Series, n: int) -> Optional[float]:
    """% change in OBV over the last n bars.

    WARNING: OBV is a signed cumulative that can pass through zero. This ratio
    blows up (or flips sign) when the base bar is near zero — the reason two
    UI surfaces can report wildly different long-horizon "% change" numbers
    for the same series. Prefer `obv_norm_slope_pct` for user-facing reporting;
    keep this one only where a documented % change threshold is already in
    force (e.g. lt_flow.py, rank.py bonus signals).
    """
    if len(obv_series) < n + 1:
        return None
    base = obv_series.iloc[-(n + 1)]
    if base == 0:
        return None
    return float((obv_series.iloc[-1] / base - 1) * 100)


def obv_norm_slope_pct(obv_series: pd.Series, n: int) -> Optional[float]:
    """Zero-crossing-safe OBV trend metric over the last n bars.

    Fits a linear regression to OBV over the trailing window, normalizes the
    slope by mean(|OBV|), and scales to a "% per window" figure. Because the
    denominator is a magnitude (never near zero even when OBV itself crosses
    zero), the metric stays bounded and comparable across tickers.

    Interpretation is the same *sign* as `obv_slope_pct` — positive = rising
    cumulative buying pressure, negative = distribution — but the magnitude
    won't explode to +356 % on one snapshot and +198 % on another for the
    same window.

    Returns None on <3 valid points or when the OBV series has zero magnitude.
    """
    if obv_series is None or len(obv_series) < max(n, 3):
        return None
    window = obv_series.iloc[-n:] if n is not None else obv_series
    slope_per_bar = norm_slope(window, None)
    if slope_per_bar is None:
        return None
    # slope_per_bar is slope-per-bar as fraction of mean(|OBV|). Multiply by
    # the number of bars to get the full-window change in the same units,
    # then to percent.
    return float(slope_per_bar * len(window) * 100.0)


# --------------------------------------------------------------------------- #
# OBV flow velocity — the derivative of OBV.
#
# A negative 30d OBV slope tells you flow is weak but not *when* it got weak.
# Comparing a short-window slope (10d) to a long-window slope (30d) separates
# "healing" flow (long weak, short turning up — pre-breakout inflection) from
# "hemorrhaging" flow (long weak, short still down — bull-trap territory).
#
# Uses obv_norm_slope_pct so short and long windows are on the same scale
# and comparable across tickers. Pure; deterministic.
# --------------------------------------------------------------------------- #

FlowInflection = Literal["healing", "hemorrhaging", "neutral", "unavailable"]


def obv_flow_inflection(
    close: pd.Series,
    volume: pd.Series,
    *,
    short: int = 10,
    long: int = 30,
    long_threshold_pct: float = 0.0,
    short_threshold_pct: float = 0.0,
) -> tuple[FlowInflection, Optional[float], Optional[float]]:
    """Classify OBV inflection from short-window vs long-window slope.

    Returns (label, short_slope_pct, long_slope_pct).

      healing       long_slope <  long_threshold  AND short_slope >  short_threshold
                      (multi-week weakness, but the last ~2 weeks are turning up)
      hemorrhaging  long_slope <  long_threshold  AND short_slope <  short_threshold
                      (both windows negative — flow still bleeding)
      neutral       neither condition
      unavailable   not enough bars to compute either slope

    Thresholds default to 0.0 (sign check). Non-zero thresholds let callers
    require magnitude, e.g. only count "healing" if short_slope >= +2%.
    """
    obv_series = obv(close, volume)
    s_short = obv_norm_slope_pct(obv_series, short)
    s_long = obv_norm_slope_pct(obv_series, long)
    if s_short is None or s_long is None:
        return "unavailable", s_short, s_long
    if s_long < long_threshold_pct and s_short > short_threshold_pct:
        return "healing", s_short, s_long
    if s_long < long_threshold_pct and s_short < short_threshold_pct:
        return "hemorrhaging", s_short, s_long
    return "neutral", s_short, s_long


@dataclass
class DivergenceResult:
    is_bullish: bool
    form: str                       # "classic" | "flat-price" | "none"
    price_low_early: Optional[float]
    price_low_recent: Optional[float]
    obv_at_early_low: Optional[float]
    obv_at_recent_low: Optional[float]
    detail: str


@dataclass
class VolumeSpikeEvent:
    """Contextual interpretation of a single-session volume spike.

    A large volume bar is not automatically bullish or bearish. The close
    location, prior quietness, support/resistance posture, extension from the
    50-day average, and OBV slope decide whether it is early accumulation,
    breakout ignition, absorption, distribution, or a late-stage climax.
    """
    kind: VolumeEventKind
    direction: VolumeEventDirection
    score: float
    label: str
    detail: str
    is_spike: bool
    vol_ratio_50: Optional[float]
    quiet_ratio_5_50: Optional[float]
    close_location: Optional[float]
    price_change_pct: Optional[float]
    break_pct: Optional[float]
    breakdown_pct: Optional[float]
    close_vs_ma50_pct: Optional[float]
    ret_30d_pct: Optional[float]
    obv_20d_slope_pct: Optional[float]
    base_days: int

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "score": self.score,
            "label": self.label,
            "detail": self.detail,
            "is_spike": self.is_spike,
            "vol_ratio_50": self.vol_ratio_50,
            "quiet_ratio_5_50": self.quiet_ratio_5_50,
            "close_location": self.close_location,
            "price_change_pct": self.price_change_pct,
            "break_pct": self.break_pct,
            "breakdown_pct": self.breakdown_pct,
            "close_vs_ma50_pct": self.close_vs_ma50_pct,
            "ret_30d_pct": self.ret_30d_pct,
            "obv_20d_slope_pct": self.obv_20d_slope_pct,
            "base_days": self.base_days,
        }


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


# --------------------------------------------------------------------------- #
# Early volume-spike event classifier
# --------------------------------------------------------------------------- #

def volume_spike_event(
    df: pd.DataFrame,
    *,
    early_spike_mult: float = 1.8,
    ignition_spike_mult: float = 2.5,
    quiet_ratio_max: float = 0.75,
    resistance_lookback: int = 20,
    support_lookback: int = 20,
    extension_vs_ma50_warn_pct: float = 18.0,
) -> VolumeSpikeEvent:
    """Classify the latest EOD bar as a contextual volume event.

    The live buy pipeline waits for a full breakout. This helper is earlier:
    it can flag a watchlist-style accumulation/ignition bar before every hard
    gate has cleared, while also catching high-volume distribution bars for
    exits and avoid-list warnings.
    """
    neutral = VolumeSpikeEvent(
        kind="neutral",
        direction="neutral",
        score=0.0,
        label="No volume event",
        detail="No abnormal volume event on the latest closed bar.",
        is_spike=False,
        vol_ratio_50=None,
        quiet_ratio_5_50=None,
        close_location=None,
        price_change_pct=None,
        break_pct=None,
        breakdown_pct=None,
        close_vs_ma50_pct=None,
        ret_30d_pct=None,
        obv_20d_slope_pct=None,
        base_days=0,
    )
    if df is None or df.empty or len(df) < 60:
        neutral.detail = "Insufficient bars for volume-event classification."
        return neutral

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        neutral.detail = "OHLCV columns unavailable for volume-event classification."
        return neutral

    close = df["Close"].dropna()
    high = df["High"].dropna()
    low = df["Low"].dropna()
    volume = df["Volume"].dropna()
    if len(close) < 60 or len(volume) < 60:
        neutral.detail = "Insufficient clean OHLCV rows for volume-event classification."
        return neutral

    last = df.iloc[-1]
    last_close = float(last["Close"])
    prev_close = float(close.iloc[-2])
    last_vol = float(last["Volume"])
    adv50 = adv(volume, 50)
    if not adv50 or adv50 <= 0 or last_close <= 0 or prev_close <= 0:
        neutral.detail = "Volume or price baseline unavailable for event classification."
        return neutral

    vol_ratio = last_vol / adv50
    prior_volume = volume.iloc[:-1]
    prior_adv5 = adv(prior_volume, 5)
    prior_adv50 = adv(prior_volume, 50)
    quiet_ratio = (
        prior_adv5 / prior_adv50
        if prior_adv5 is not None and prior_adv50 is not None and prior_adv50 > 0
        else None
    )
    close_location = last_bar_upper_third_ratio(df)
    price_change_pct = (last_close / prev_close - 1) * 100

    resistance = rolling_high(high, resistance_lookback, exclude_today=True)
    support = rolling_low(low, support_lookback, exclude_today=True)
    break_pct = (
        (last_close / resistance - 1) * 100
        if resistance is not None and resistance > 0
        else None
    )
    breakdown_pct = (
        (last_close / support - 1) * 100
        if support is not None and support > 0
        else None
    )
    ma50 = sma(close, 50)
    close_vs_ma50 = (
        (last_close / ma50 - 1) * 100
        if ma50 is not None and ma50 > 0
        else None
    )
    ret30 = (
        (last_close / float(close.iloc[-31]) - 1) * 100
        if len(close) >= 31 and float(close.iloc[-31]) > 0
        else None
    )
    obv20 = obv_slope_pct(obv(close, volume), 20)
    base = days_within_band(close, 0.10)

    def _round(x: Optional[float], digits: int = 3) -> Optional[float]:
        return round(float(x), digits) if x is not None else None

    is_spike = vol_ratio >= early_spike_mult
    if not is_spike:
        return VolumeSpikeEvent(
            kind="neutral",
            direction="neutral",
            score=0.0,
            label="No volume event",
            detail=f"Latest volume is {vol_ratio:.2f}x ADV50, below early spike threshold.",
            is_spike=False,
            vol_ratio_50=_round(vol_ratio),
            quiet_ratio_5_50=_round(quiet_ratio),
            close_location=_round(close_location),
            price_change_pct=_round(price_change_pct, 2),
            break_pct=_round(break_pct, 2),
            breakdown_pct=_round(breakdown_pct, 2),
            close_vs_ma50_pct=_round(close_vs_ma50, 2),
            ret_30d_pct=_round(ret30, 2),
            obv_20d_slope_pct=_round(obv20, 2),
            base_days=base,
        )

    quiet_before = quiet_ratio is not None and quiet_ratio <= quiet_ratio_max
    strong_close = close_location is not None and close_location >= 0.67
    constructive_close = close_location is not None and close_location >= 0.55
    weak_close = close_location is not None and close_location <= 0.33
    near_resistance = break_pct is not None and break_pct >= -2.0
    breakout = break_pct is not None and break_pct > 0
    support_break = breakdown_pct is not None and breakdown_pct < 0
    extended = (
        (close_vs_ma50 is not None and close_vs_ma50 >= extension_vs_ma50_warn_pct)
        or (ret30 is not None and ret30 >= 25.0)
    )
    obv_supportive = obv20 is None or obv20 >= -2.0
    obv_negative = obv20 is not None and obv20 <= -5.0
    volume_strength = min(1.0, max(0.0, (vol_ratio - early_spike_mult) / 2.5))

    if (
        vol_ratio >= ignition_spike_mult
        and strong_close
        and breakout
        and not extended
        and obv_supportive
    ):
        score = min(1.0, 0.65 + 0.35 * volume_strength)
        label = "Bullish volume ignition"
        detail = (
            f"{vol_ratio:.2f}x ADV50 with upper-third close and "
            f"{break_pct:+.1f}% break above {resistance_lookback}d resistance."
        )
        kind: VolumeEventKind = "bullish_ignition"
        direction: VolumeEventDirection = "bullish"
    elif (
        is_spike
        and constructive_close
        and quiet_before
        and near_resistance
        and not extended
        and obv_supportive
    ):
        score = min(0.85, 0.45 + 0.35 * volume_strength + (0.05 if breakout else 0.0))
        label = "Early accumulation spike"
        detail = (
            f"{vol_ratio:.2f}x ADV50 after quiet prior volume "
            f"({quiet_ratio:.2f}x), near {resistance_lookback}d resistance."
        )
        kind = "early_accumulation"
        direction = "bullish"
    elif is_spike and price_change_pct < 0 and strong_close and not support_break:
        score = min(0.75, 0.40 + 0.35 * volume_strength)
        label = "Support absorption"
        detail = (
            f"{vol_ratio:.2f}x ADV50 on a down day, but buyers closed the candle "
            "near the high. Watch for follow-through."
        )
        kind = "support_absorption"
        direction = "bullish"
    elif (
        is_spike
        and (weak_close or price_change_pct <= -2.0)
        and (support_break or extended or obv_negative)
    ):
        score = min(1.0, 0.60 + 0.40 * volume_strength)
        label = "Bearish distribution spike"
        reason = "support break" if support_break else "extended move" if extended else "OBV rollover"
        detail = (
            f"{vol_ratio:.2f}x ADV50 with weak close/down move; context: {reason}. "
            "Treat as exit/avoid warning."
        )
        kind = "bearish_distribution"
        direction = "bearish"
    elif vol_ratio >= ignition_spike_mult and extended and not strong_close:
        score = min(0.85, 0.50 + 0.35 * volume_strength)
        label = "Climax volume warning"
        detail = (
            f"{vol_ratio:.2f}x ADV50 after an extended move "
            f"({close_vs_ma50 or 0:+.1f}% vs 50d MA). Do not chase without reset."
        )
        kind = "climax_warning"
        direction = "bearish"
    else:
        score = min(0.35, 0.15 + 0.20 * volume_strength)
        label = "Unconfirmed volume spike"
        detail = (
            f"{vol_ratio:.2f}x ADV50, but price context is mixed. "
            "Needs follow-through before acting."
        )
        kind = "neutral"
        direction = "neutral"

    return VolumeSpikeEvent(
        kind=kind,
        direction=direction,
        score=round(score, 4),
        label=label,
        detail=detail,
        is_spike=True,
        vol_ratio_50=_round(vol_ratio),
        quiet_ratio_5_50=_round(quiet_ratio),
        close_location=_round(close_location),
        price_change_pct=_round(price_change_pct, 2),
        break_pct=_round(break_pct, 2),
        breakdown_pct=_round(breakdown_pct, 2),
        close_vs_ma50_pct=_round(close_vs_ma50, 2),
        ret_30d_pct=_round(ret30, 2),
        obv_20d_slope_pct=_round(obv20, 2),
        base_days=base,
    )
