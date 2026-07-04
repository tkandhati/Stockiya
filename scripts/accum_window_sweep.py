"""Accumulation-gate window sweep.

Reads cached OHLCV from Stockya-tuner, walks the last ~180 bars, and reports
how the three AC checks (tight range, dry volume, ADI positive divergence)
behave at W in {20, 30, 60}. Standalone — no pipeline glue, no network.

Prints a table per ticker + a summary recommendation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OHLCV_DIR = Path(r"C:/Claude_projects/Stockya-tuner/data/ohlcv")
TICKERS = ["STLTECH.NS", "EICHERMOT.NS"]
WINDOWS = [20, 30, 60]
LOOKBACK_BARS = 180

# Proposed defaults from the design
TIGHT_RANGE_PCT_MAX = 0.08
ATR_COMPRESSION_MAX = 0.85
VOLUME_DRY_MULT = 0.85
PRICE_SLOPE_MAX = 0.002
ADI_SLOPE_MIN = 0.005


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def adi(df: pd.DataFrame) -> pd.Series:
    """Accumulation/Distribution Line (cumulative money flow volume)."""
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    denom = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / denom
    mfm = mfm.fillna(0.0)
    return (mfm * vol).cumsum()


def norm_slope(y: pd.Series) -> float:
    """Linear-regression slope of y on integer x, normalized by mean(|y|).
    Returns slope-per-bar as a fraction of typical level.
    """
    y = y.dropna().to_numpy(dtype=float)
    if len(y) < 3:
        return float("nan")
    x = np.arange(len(y), dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    denom = np.mean(np.abs(y))
    return slope / denom if denom != 0 else float("nan")


def eval_window(df: pd.DataFrame, end_idx: int, W: int) -> dict:
    """Compute the three checks at bar end_idx for window size W."""
    if end_idx - 2 * W + 1 < 0 or end_idx - W < 14:
        return {}
    window = df.iloc[end_idx - W + 1 : end_idx + 1]
    prior = df.iloc[end_idx - 2 * W + 1 : end_idx - W + 1]

    # Check 1a: tight range
    hi = window["High"].max()
    lo = window["Low"].min()
    mean_close = window["Close"].mean()
    range_pct = (hi - lo) / mean_close if mean_close > 0 else np.nan

    # Check 1b: ATR compression (ATR14 today vs ATR14 W bars ago)
    atr_series = atr(df.iloc[: end_idx + 1], 14)
    atr_now = atr_series.iloc[-1]
    atr_then = atr_series.iloc[-W] if len(atr_series) >= W else np.nan
    atr_ratio = atr_now / atr_then if atr_then and atr_then > 0 else np.nan

    # Check 2: volume dryness
    vol_recent = window["Volume"].mean()
    vol_prior = prior["Volume"].mean()
    vol_ratio = vol_recent / vol_prior if vol_prior > 0 else np.nan

    # Check 3: ADI positive divergence
    adi_series = adi(df.iloc[: end_idx + 1]).iloc[-W:]
    close_series = window["Close"]
    price_slope = norm_slope(close_series)
    adi_slope = norm_slope(adi_series)

    passes = {
        "tight_range": range_pct <= TIGHT_RANGE_PCT_MAX,
        "atr_compression": atr_ratio <= ATR_COMPRESSION_MAX,
        "vol_dry": vol_ratio <= VOLUME_DRY_MULT,
        "price_flat": abs(price_slope) <= PRICE_SLOPE_MAX,
        "adi_rising": adi_slope >= ADI_SLOPE_MIN,
    }
    passes["divergence"] = passes["price_flat"] and passes["adi_rising"]
    passes["all"] = passes["tight_range"] and passes["vol_dry"] and passes["divergence"]

    return {
        "range_pct": range_pct,
        "atr_ratio": atr_ratio,
        "vol_ratio": vol_ratio,
        "price_slope": price_slope,
        "adi_slope": adi_slope,
        **{f"pass_{k}": v for k, v in passes.items()},
    }


def sweep_ticker(path: Path) -> dict:
    df = pd.read_csv(path, parse_dates=["Date"]).sort_values("Date").reset_index(drop=True)
    n = len(df)
    start_idx = max(2 * max(WINDOWS), n - LOOKBACK_BARS)
    endpoints = list(range(start_idx, n))

    out = {}
    for W in WINDOWS:
        rows = []
        for i in endpoints:
            r = eval_window(df, i, W)
            if r:
                rows.append(r)
        rf = pd.DataFrame(rows)
        if rf.empty:
            out[W] = None
            continue
        out[W] = {
            "n_endpoints": len(rf),
            "median_range_pct": rf["range_pct"].median(),
            "median_atr_ratio": rf["atr_ratio"].median(),
            "median_vol_ratio": rf["vol_ratio"].median(),
            "median_price_slope": rf["price_slope"].median(),
            "median_adi_slope": rf["adi_slope"].median(),
            "pass_rate_tight": rf["pass_tight_range"].mean(),
            "pass_rate_vol": rf["pass_vol_dry"].mean(),
            "pass_rate_div": rf["pass_divergence"].mean(),
            "pass_rate_all": rf["pass_all"].mean(),
        }
    return out


def fmt_pct(x):
    return f"{x*100:6.2f}%" if pd.notna(x) else "   n/a"


def fmt_num(x, d=4):
    return f"{x:+.{d}f}" if pd.notna(x) else "   n/a"


def main():
    print(f"Window sweep on {LOOKBACK_BARS}-bar lookback  |  W in {WINDOWS}")
    print(f"Defaults: range<={TIGHT_RANGE_PCT_MAX}  atr<={ATR_COMPRESSION_MAX}  "
          f"vol<={VOLUME_DRY_MULT}  |price_slope|<={PRICE_SLOPE_MAX}  "
          f"adi_slope>={ADI_SLOPE_MIN}")
    print("=" * 88)

    all_results = {}
    for ticker in TICKERS:
        p = OHLCV_DIR / f"{ticker}.csv"
        if not p.exists():
            print(f"! missing {p}")
            continue
        res = sweep_ticker(p)
        all_results[ticker] = res

        print(f"\n{ticker}")
        print(f"{'W':>3} {'n':>4}  {'range_med':>9}  {'atr_med':>7}  {'vol_med':>7}  "
              f"{'p_slope':>9}  {'adi_slope':>9}  {'tight%':>7}  {'vol%':>6}  "
              f"{'div%':>6}  {'ALL%':>6}")
        for W in WINDOWS:
            r = res.get(W)
            if r is None:
                print(f"{W:>3}  insufficient bars")
                continue
            print(
                f"{W:>3} {r['n_endpoints']:>4}  "
                f"{fmt_pct(r['median_range_pct']):>9}  "
                f"{r['median_atr_ratio']:>7.3f}  "
                f"{r['median_vol_ratio']:>7.3f}  "
                f"{fmt_num(r['median_price_slope']):>9}  "
                f"{fmt_num(r['median_adi_slope']):>9}  "
                f"{r['pass_rate_tight']*100:>6.1f}%  "
                f"{r['pass_rate_vol']*100:>5.1f}%  "
                f"{r['pass_rate_div']*100:>5.1f}%  "
                f"{r['pass_rate_all']*100:>5.1f}%"
            )

    # Aggregate recommendation
    print("\n" + "=" * 88)
    print("Aggregate pass-rate for ALL-three checks across tickers:")
    for W in WINDOWS:
        rates = [all_results[t][W]["pass_rate_all"] for t in all_results if all_results[t].get(W)]
        if not rates:
            continue
        print(f"  W={W:>2}  avg pass_all = {np.mean(rates)*100:5.2f}%  "
              f"(per-ticker: {[f'{r*100:.1f}%' for r in rates]})")


if __name__ == "__main__":
    sys.exit(main())
