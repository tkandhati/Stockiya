"""Volume-first analysis engine.

Implements the canonical "world's best" volume strategies and combines them into
a single verdict. Each strategy returns a labeled signal (bullish / neutral /
bearish) so the picker AND the user can see exactly which patterns are firing.

Strategies implemented:
1.  **Up/Down volume ratio** (accumulation vs distribution day count, 30d)
2.  **OBV slope** — Joe Granville's On-Balance Volume cumulative flow (30d)
3.  **Chaikin A/D Line slope** — close-position-weighted volume (30d)
4.  **Chaikin Money Flow (CMF, 21d)** — bounded [-1, +1] money-flow oscillator
5.  **Money Flow Index (MFI, 14d)** — volume-weighted RSI; >80 overbought, <20 oversold
6.  **Pocket Pivot** (Gil Morales / Chris Kacher) — up-day volume is the largest
    of the prior 10 down-day volumes while price is in a tight range
7.  **Volume Dry-Up (VDU)** — Mark Minervini's pre-breakout signature: the most
    recent 5-day avg volume is sub-50% of the 50-day avg while price is tight
8.  **CAN SLIM breakout volume** (William O'Neil) — recent breakout day with
    volume >= 1.4x 50-day avg
9.  **VWAP posture** (rolling 60-day) — price above VWAP = institutional bid wins
10. **Wyckoff-lite phase classification** — combines tightness, OBV slope, and
    volume regime to label the chart as Accumulation / Markup /
    Distribution / Markdown / Indeterminate

All inputs come from a daily OHLCV DataFrame: Open, High, Low, Close, Volume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
import pandas as pd

from .indicators import VolumeSpikeEvent, volume_spike_event


Verdict = Literal["accumulating", "neutral", "distributing", "unknown"]
SignalState = Literal["bullish", "neutral", "bearish"]
WyckoffPhase = Literal["accumulation", "markup", "distribution", "markdown", "indeterminate"]
EntryTiming = Literal["early", "mid", "late", "missed", "unknown"]

# Stan Weinstein's four-stage long-term framework. The investing-horizon anchor.
WeinsteinStage = Literal[
    "stage_1_base",          # post-decline base, flat price near a key low
    "stage_1_to_2",          # base breaking — turning the corner
    "stage_2_advance",       # uptrend with rising volume, the buy zone
    "stage_3_top",           # flattening at highs, distribution begins
    "stage_4_decline",       # downtrend with rising volume on red days
    "undefined",
]


@dataclass
class StrategySignal:
    name: str
    state: SignalState
    value: Optional[float]
    label: str
    description: str


@dataclass
class AccumulationSignals:
    days_used: int

    # --- Medium-term (current strategies — confirmation) ---
    vol_recent_10d: Optional[float]
    vol_avg_30d: Optional[float]
    vol_avg_90d: Optional[float]
    vol_trend_pct: Optional[float]
    up_down_vol_ratio: Optional[float]            # 30 sessions
    obv_slope_pct: Optional[float]                # 30d
    ad_line_slope_pct: Optional[float]            # 30d
    cmf_21d: Optional[float]
    mfi_14d: Optional[float]
    price_tightness_pct: Optional[float]          # 20d
    price_change_30d_pct: Optional[float]
    vwap_60d: Optional[float]
    price_vs_vwap_pct: Optional[float]

    # --- LONG-TERM (the investing-horizon lens — primary) ---
    weinstein_stage: WeinsteinStage = "undefined"
    weinstein_note: str = ""                       # one-line plain English
    ma_30w: Optional[float] = None                 # 30-week (~150d) moving average
    ma_30w_slope_pct: Optional[float] = None       # slope of 30wMA over last 10 weeks
    ma_50d: Optional[float] = None
    ma_150d: Optional[float] = None
    ma_200d: Optional[float] = None
    minervini_template: bool = False               # 50>150>200, all rising, price>50
    obv_slope_90d_pct: Optional[float] = None
    obv_slope_180d_pct: Optional[float] = None
    # Zero-crossing-safe OBV trend (bounded). Preferred for user-facing display.
    obv_norm_slope_90d_pct: Optional[float] = None
    obv_norm_slope_180d_pct: Optional[float] = None
    cmf_60d: Optional[float] = None
    up_down_vol_ratio_90d: Optional[float] = None
    base_length_days: int = 0                      # days price stayed within ±10% of recent
    vol_qoq_growth_pct: Optional[float] = None     # last quarter vs prior quarter avg vol
    price_change_180d_pct: Optional[float] = None

    # --- Pattern-fire flags ---
    pocket_pivot_count_30d: int = 0
    volume_dry_up: bool = False
    canslim_breakout: bool = False
    volume_event: Optional[VolumeSpikeEvent] = None

    # --- Block + bulk deals (NSE EOD institutional trade records) ---
    block_deal_buy_count_30d: int = 0
    block_deal_sell_count_30d: int = 0
    block_deal_net_qty_ratio: float = 0.0  # in [-1, +1]; +1 = all buys

    # --- Composite ---
    accum_score: float = 0.0
    verdict: Verdict = "unknown"
    wyckoff_phase: WyckoffPhase = "indeterminate"
    entry_timing: EntryTiming = "unknown"
    entry_timing_note: str = ""
    one_liner: str = ""
    signals: list[StrategySignal] = field(default_factory=list)


@dataclass
class TargetWindow:
    """Suggested holding window for a setup, derived from the long-term picture.

    The output the user sees, e.g. "3 months ± 1 month" or "6 months ± 2 months".
    """
    center_months: float          # e.g. 3.0, 4.0, 6.0
    tolerance_months: float       # e.g. 1.0, 1.5, 2.0
    label: str                    # human-readable
    rationale: str                # why this window


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def compute(history: pd.DataFrame, symbol: Optional[str] = None) -> AccumulationSignals:
    """Run every strategy and produce a unified verdict."""
    if history is None or history.empty:
        return _unknown(0)

    df = history.dropna(subset=["Close", "Volume"]).tail(260).copy()
    n = len(df)
    if n < 30:
        return _unknown(n)

    closes = df["Close"]
    highs = df["High"] if "High" in df.columns else closes
    lows = df["Low"] if "Low" in df.columns else closes
    vols = df["Volume"]

    signals: list[StrategySignal] = []
    volume_event = volume_spike_event(df)

    # ---------- 1. Volume trend (recent vs 30/90 day average) ----------
    vol_10 = float(vols.tail(10).mean())
    vol_30 = float(vols.tail(30).mean()) if n >= 30 else float(vols.mean())
    vol_50 = float(vols.tail(50).mean()) if n >= 50 else vol_30
    vol_90 = float(vols.tail(90).mean()) if n >= 90 else vol_30
    vol_trend_pct = (vol_10 / vol_30 - 1) * 100 if vol_30 > 0 else None

    if vol_trend_pct is not None:
        if vol_trend_pct >= 15:
            signals.append(StrategySignal(
                "Volume trend", "bullish", vol_trend_pct,
                f"10d avg {vol_trend_pct:+.0f}% vs 30d",
                "Recent volume is rising — institutions are stepping in.",
            ))
        elif vol_trend_pct <= -15:
            signals.append(StrategySignal(
                "Volume trend", "bearish", vol_trend_pct,
                f"10d avg {vol_trend_pct:+.0f}% vs 30d",
                "Volume is drying up — interest fading.",
            ))
        else:
            signals.append(StrategySignal(
                "Volume trend", "neutral", vol_trend_pct,
                f"10d avg {vol_trend_pct:+.0f}% vs 30d",
                "No meaningful change in participation.",
            ))

    # ---------- 2. Up/Down volume ratio (30d) ----------
    recent30 = df.tail(30)
    deltas30 = recent30["Close"].diff()
    up_vol = float(recent30["Volume"][deltas30 > 0].sum())
    down_vol = float(recent30["Volume"][deltas30 < 0].sum())
    if down_vol > 0:
        up_down = up_vol / down_vol
    elif up_vol > 0:
        up_down = 5.0
    else:
        up_down = None

    if up_down is not None:
        if up_down >= 1.4:
            signals.append(StrategySignal(
                "Up/Down volume", "bullish", up_down,
                f"{up_down:.2f}x",
                "Up-day volume dominates — net buying pressure.",
            ))
        elif up_down <= 0.7:
            signals.append(StrategySignal(
                "Up/Down volume", "bearish", up_down,
                f"{up_down:.2f}x",
                "Down-day volume dominates — net selling.",
            ))
        else:
            signals.append(StrategySignal(
                "Up/Down volume", "neutral", up_down,
                f"{up_down:.2f}x",
                "Buying and selling roughly balanced.",
            ))

    # ---------- 3. OBV (Granville) slope ----------
    # Bug fix: previously this module cumsum'd its own OBV series while
    # indicators.py did the same in a second function. Two implementations →
    # two card/detail-page numbers that disagreed for the same ticker. Now
    # both sites route through indicators.obv() — one source of truth.
    from .indicators import obv as _obv_series, obv_slope_pct as _obv_slope_pct
    obv = _obv_series(closes, vols)
    obv_slope = _obv_slope_pct(obv, 30)

    if obv_slope is not None:
        if obv_slope >= 5:
            signals.append(StrategySignal(
                "OBV (Granville)", "bullish", obv_slope,
                f"{obv_slope:+.1f}% / 30d",
                "On-Balance Volume rising — cumulative buying pressure.",
            ))
        elif obv_slope <= -5:
            signals.append(StrategySignal(
                "OBV (Granville)", "bearish", obv_slope,
                f"{obv_slope:+.1f}% / 30d",
                "On-Balance Volume falling — cumulative selling pressure.",
            ))
        else:
            signals.append(StrategySignal(
                "OBV (Granville)", "neutral", obv_slope,
                f"{obv_slope:+.1f}% / 30d",
                "OBV flat — no cumulative directional flow.",
            ))

    # ---------- 4. Chaikin A/D Line slope ----------
    ad_slope = None
    try:
        clv = ((closes - lows) - (highs - closes)) / (highs - lows).replace(0, np.nan)
        clv = clv.fillna(0)
        ad_line = (clv * vols).cumsum()
        if len(ad_line) >= 31 and ad_line.iloc[-31] != 0:
            ad_slope = (ad_line.iloc[-1] / abs(ad_line.iloc[-31]) - np.sign(ad_line.iloc[-31])) * 100
    except Exception:
        ad_slope = None

    if ad_slope is not None and abs(ad_slope) > 0.1:
        if ad_slope >= 5:
            signals.append(StrategySignal(
                "Chaikin A/D", "bullish", ad_slope,
                f"{ad_slope:+.1f}% / 30d",
                "A/D Line rising — closes near highs on volume (Chaikin accumulation).",
            ))
        elif ad_slope <= -5:
            signals.append(StrategySignal(
                "Chaikin A/D", "bearish", ad_slope,
                f"{ad_slope:+.1f}% / 30d",
                "A/D Line falling — closes near lows on volume (distribution).",
            ))

    # ---------- 5. Chaikin Money Flow (21-day) ----------
    cmf = None
    try:
        win = 21 if n >= 21 else n
        clv_w = ((closes - lows) - (highs - closes)) / (highs - lows).replace(0, np.nan)
        clv_w = clv_w.fillna(0)
        money_flow_volume = clv_w * vols
        cmf = float(money_flow_volume.tail(win).sum() / vols.tail(win).sum())
    except Exception:
        cmf = None

    if cmf is not None:
        if cmf >= 0.10:
            signals.append(StrategySignal(
                "Chaikin Money Flow (21d)", "bullish", cmf,
                f"{cmf:+.2f}",
                "CMF strongly positive — sustained buying pressure.",
            ))
        elif cmf <= -0.10:
            signals.append(StrategySignal(
                "Chaikin Money Flow (21d)", "bearish", cmf,
                f"{cmf:+.2f}",
                "CMF strongly negative — sustained selling pressure.",
            ))
        else:
            signals.append(StrategySignal(
                "Chaikin Money Flow (21d)", "neutral", cmf,
                f"{cmf:+.2f}",
                "CMF near zero — no decisive flow.",
            ))

    # ---------- 6. Money Flow Index (14d) — volume-weighted RSI ----------
    mfi = None
    try:
        typical_price = (highs + lows + closes) / 3
        money_flow = typical_price * vols
        tp_change = typical_price.diff()
        positive_flow = money_flow.where(tp_change > 0, 0)
        negative_flow = money_flow.where(tp_change < 0, 0)
        pos_sum = positive_flow.tail(14).sum()
        neg_sum = negative_flow.tail(14).sum()
        if neg_sum > 0:
            mfi = 100 - (100 / (1 + (pos_sum / neg_sum)))
        elif pos_sum > 0:
            mfi = 100.0
        mfi = float(mfi) if mfi is not None else None
    except Exception:
        mfi = None

    if mfi is not None:
        if mfi >= 80:
            signals.append(StrategySignal(
                "MFI (14d)", "bearish", mfi,
                f"{mfi:.0f}",
                "Volume-weighted RSI overbought — late-stage move, exhaustion risk.",
            ))
        elif mfi <= 20:
            signals.append(StrategySignal(
                "MFI (14d)", "bullish", mfi,
                f"{mfi:.0f}",
                "Volume-weighted RSI oversold — capitulation, often a low.",
            ))
        elif 50 <= mfi < 80:
            signals.append(StrategySignal(
                "MFI (14d)", "bullish", mfi,
                f"{mfi:.0f}",
                "MFI in healthy uptrend zone (50-80).",
            ))
        elif 20 < mfi < 50:
            signals.append(StrategySignal(
                "MFI (14d)", "bearish", mfi,
                f"{mfi:.0f}",
                "MFI in weakening zone (20-50).",
            ))

    # ---------- 7. Pocket Pivot count (last 30d) ----------
    pp_count = _pocket_pivot_count(df.tail(30))
    if pp_count >= 1:
        signals.append(StrategySignal(
            "Pocket Pivot (Morales)", "bullish", float(pp_count),
            f"{pp_count} fired in last 30d",
            (
                "Up-day volume is the largest of the prior 10 down-day volumes while "
                "price is in a tight base — institutional buy point."
            ),
        ))

    # ---------- 8. Volume Dry-Up (Minervini) ----------
    vdu = False
    try:
        vol_5 = float(vols.tail(5).mean())
        if vol_50 > 0 and vol_5 / vol_50 < 0.5:
            # Combined with price tightness check below
            tight_hi = float(highs.tail(20).max())
            tight_lo = float(lows.tail(20).min())
            mean_close = float(closes.tail(20).mean())
            if mean_close > 0 and (tight_hi - tight_lo) / mean_close < 0.10:
                vdu = True
    except Exception:
        vdu = False

    if vdu:
        signals.append(StrategySignal(
            "Volume Dry-Up (Minervini)", "bullish", None,
            "5d avg < 50% of 50d, price tight",
            "Pre-breakout 'volume dry-up' signature — supply is exhausted near support.",
        ))

    # ---------- 9. CAN SLIM breakout volume (O'Neil) ----------
    canslim = False
    try:
        last_close = float(closes.iloc[-1])
        last_vol = float(vols.iloc[-1])
        # Breakout: price within 2% of 20d high AND volume >= 1.4x 50d avg
        if vol_50 > 0:
            high20 = float(highs.tail(20).max())
            if last_close >= 0.98 * high20 and last_vol >= 1.4 * vol_50:
                canslim = True
    except Exception:
        canslim = False

    if canslim:
        signals.append(StrategySignal(
            "CAN SLIM breakout (O'Neil)", "bullish", None,
            "At 20d high on >=1.4x avg volume",
            "Classic O'Neil breakout — price punching through resistance on heavy buying.",
        ))

    # ---------- 9b. Contextual volume spike event ----------
    if volume_event.kind != "neutral":
        signals.append(StrategySignal(
            "Volume Spike Event",
            "bullish" if volume_event.direction == "bullish" else "bearish",
            volume_event.score,
            volume_event.label,
            volume_event.detail,
        ))

    # ---------- 10. VWAP posture (60d rolling) ----------
    price_vs_vwap = None
    vwap_60 = None
    try:
        win = 60 if n >= 60 else n
        typical_price = (highs + lows + closes) / 3
        recent = pd.DataFrame({"tp": typical_price, "v": vols}).tail(win)
        if recent["v"].sum() > 0:
            vwap_60 = float((recent["tp"] * recent["v"]).sum() / recent["v"].sum())
            price_vs_vwap = (float(closes.iloc[-1]) / vwap_60 - 1) * 100
    except Exception:
        vwap_60 = None
        price_vs_vwap = None

    if price_vs_vwap is not None:
        if price_vs_vwap >= 1.5:
            signals.append(StrategySignal(
                "VWAP (60d)", "bullish", price_vs_vwap,
                f"price {price_vs_vwap:+.1f}% vs VWAP",
                "Price trading above 60-day VWAP — institutional bid is winning.",
            ))
        elif price_vs_vwap <= -1.5:
            signals.append(StrategySignal(
                "VWAP (60d)", "bearish", price_vs_vwap,
                f"price {price_vs_vwap:+.1f}% vs VWAP",
                "Price below 60-day VWAP — institutional offer is winning.",
            ))

    # ---------- Tightness & 30-day price change (used by Wyckoff) ----------
    last20 = df.tail(20)
    if len(last20) >= 5 and float(last20["Close"].mean()) > 0:
        price_tightness_pct = float(
            (last20["High"].max() - last20["Low"].min()) / last20["Close"].mean() * 100
        )
    else:
        price_tightness_pct = None

    price_change_30d_pct = (
        (float(closes.iloc[-1]) / float(closes.iloc[-31]) - 1) * 100
        if len(closes) >= 31 else None
    )

    # ---------- Wyckoff-lite phase ----------
    phase = _wyckoff_phase(
        obv_slope=obv_slope,
        cmf=cmf,
        price_change_30d=price_change_30d_pct,
        tightness=price_tightness_pct,
        vol_trend_pct=vol_trend_pct,
    )

    # ---------- Composite score in [-1, +1] ----------
    sub: list[float] = []
    if vol_trend_pct is not None:
        sub.append(max(-1.0, min(1.0, vol_trend_pct / 50.0)))
    if up_down is not None:
        if up_down >= 1.0:
            sub.append(min(1.0, (up_down - 1.0) / 1.0))
        else:
            sub.append(max(-1.0, (up_down - 1.0) / 0.5))
    if obv_slope is not None:
        sub.append(max(-1.0, min(1.0, obv_slope / 25.0)))
    if cmf is not None:
        sub.append(max(-1.0, min(1.0, cmf * 4.0)))  # CMF ±0.25 maps to ±1
    if mfi is not None:
        # Healthy zone is 50-80. Map: 50 → 0, 80 → +0.6, 90 → -0.4 (overbought late), 20 → +0.4 (bounce)
        if 50 <= mfi <= 80:
            sub.append((mfi - 50) / 30 * 0.6)
        elif mfi > 80:
            sub.append(0.6 - (mfi - 80) / 20 * 0.8)
        elif mfi < 20:
            sub.append(0.4)  # capitulation can be a low
        else:
            sub.append((mfi - 50) / 30 * 0.4)

    bonus = 0.0
    if pp_count >= 1:
        bonus += 0.20 * min(pp_count, 3) / 3
    if vdu:
        bonus += 0.15
    if canslim:
        bonus += 0.20
    if price_vs_vwap is not None:
        if price_vs_vwap >= 0:
            bonus += min(0.10, price_vs_vwap / 100)
        else:
            bonus += max(-0.10, price_vs_vwap / 100)
    if volume_event.kind != "neutral":
        if volume_event.direction == "bullish":
            bonus += 0.15 * volume_event.score
        elif volume_event.direction == "bearish":
            bonus -= 0.25 * volume_event.score

    parabolic_pen = 0.0
    if price_change_30d_pct is not None and price_change_30d_pct > 25:
        parabolic_pen = -0.30

    # ---------- LONG-TERM (the investing-horizon lens) ----------
    # Stan Weinstein 30-week MA + Minervini Trend Template + 90/180-day OBV.

    ma_50d = float(closes.tail(50).mean()) if n >= 50 else None
    ma_150d = float(closes.tail(150).mean()) if n >= 150 else None
    ma_200d = float(closes.tail(200).mean()) if n >= 200 else None

    # 30-week (= 150 trading day) MA + slope over the last ~10 weeks (50 sessions)
    ma_30w: Optional[float] = ma_150d
    ma_30w_slope_pct: Optional[float] = None
    if n >= 200:
        ma_30w_now = float(closes.tail(150).mean())
        ma_30w_50ago = float(closes.iloc[-200:-50].mean())
        if ma_30w_50ago > 0:
            ma_30w_slope_pct = (ma_30w_now / ma_30w_50ago - 1) * 100
        ma_30w = ma_30w_now

    # Stan Weinstein Stage classification — anchored on price vs 30wMA + slope
    last = float(closes.iloc[-1])
    weinstein_stage, weinstein_note = _weinstein_stage(
        last_close=last,
        ma_30w=ma_30w,
        ma_30w_slope_pct=ma_30w_slope_pct,
        price_change_180d=(last / float(closes.iloc[-180]) - 1) * 100 if n >= 180 else None,
        price_change_30d=price_change_30d_pct,
        vol_trend_pct=vol_trend_pct,
        obv_slope=obv_slope,
    )

    # Minervini Trend Template — long-term winners filter (simplified):
    # 50d > 150d > 200d, price > 50d, 200d MA rising
    minervini_template = False
    if ma_50d and ma_150d and ma_200d and n >= 220:
        ma200_20ago = float(closes.iloc[-220:-20].mean())
        rising_200 = ma_200d > ma200_20ago
        minervini_template = (
            ma_50d > ma_150d > ma_200d
            and last > ma_50d
            and rising_200
        )

    # OBV at 90 and 180 days
    # NOTE: the % change forms below are what the UI historically shows. They
    # can blow up when the base bar is near zero — that is the root cause of
    # the card vs detail page divergence (e.g. +357 % vs +198 %). We keep
    # them for backwards-compat with existing thresholds and the accumulation
    # schema, but ALSO emit the zero-crossing-safe norm-slope form. Callers
    # (and the UI) should prefer the *_norm_pct variants going forward.
    from .indicators import obv_norm_slope_pct as _obv_norm_slope_pct
    obv_slope_90d_pct: Optional[float] = None
    obv_slope_180d_pct: Optional[float] = None
    if len(obv) >= 91 and obv.iloc[-91] != 0:
        obv_slope_90d_pct = (obv.iloc[-1] / obv.iloc[-91] - 1) * 100
    if len(obv) >= 181 and obv.iloc[-181] != 0:
        obv_slope_180d_pct = (obv.iloc[-1] / obv.iloc[-181] - 1) * 100
    obv_norm_slope_90d_pct = _obv_norm_slope_pct(obv, 90)
    obv_norm_slope_180d_pct = _obv_norm_slope_pct(obv, 180)

    # CMF 60-day
    cmf_60d: Optional[float] = None
    try:
        win = 60 if n >= 60 else n
        clv60 = ((closes - lows) - (highs - closes)) / (highs - lows).replace(0, np.nan)
        clv60 = clv60.fillna(0)
        cmf_60d = float((clv60 * vols).tail(win).sum() / vols.tail(win).sum())
    except Exception:
        cmf_60d = None

    # Up/down vol ratio over the last 90 sessions (long-term flow)
    up_down_90d: Optional[float] = None
    if n >= 90:
        recent90 = df.tail(90)
        d90 = recent90["Close"].diff()
        u90 = float(recent90["Volume"][d90 > 0].sum())
        dn90 = float(recent90["Volume"][d90 < 0].sum())
        if dn90 > 0:
            up_down_90d = u90 / dn90
        elif u90 > 0:
            up_down_90d = 5.0

    # Base length — how many recent sessions price has stayed within ±10% of current
    base_length_days = 0
    if last > 0:
        for i in range(len(closes) - 1, -1, -1):
            c_i = float(closes.iloc[i])
            if abs(c_i / last - 1) <= 0.10:
                base_length_days += 1
            else:
                break

    # Quarter-over-quarter volume growth (last 63d avg vs prior 63d)
    vol_qoq_growth_pct: Optional[float] = None
    if n >= 130:
        last_q = float(vols.tail(63).mean())
        prev_q = float(vols.iloc[-126:-63].mean())
        if prev_q > 0:
            vol_qoq_growth_pct = (last_q / prev_q - 1) * 100

    price_change_180d_pct: Optional[float] = None
    if n >= 181:
        price_change_180d_pct = (float(closes.iloc[-1]) / float(closes.iloc[-181]) - 1) * 100

    # ---------- LONG-TERM SIGNAL CARDS ----------
    if weinstein_stage in ("stage_2_advance", "stage_1_to_2"):
        signals.append(StrategySignal(
            "Weinstein Stage", "bullish", None,
            "Stage 2 advance" if weinstein_stage == "stage_2_advance" else "Stage 1->2 transition",
            weinstein_note,
        ))
    elif weinstein_stage == "stage_1_base":
        signals.append(StrategySignal(
            "Weinstein Stage", "neutral", None, "Stage 1 base",
            weinstein_note,
        ))
    elif weinstein_stage in ("stage_3_top", "stage_4_decline"):
        signals.append(StrategySignal(
            "Weinstein Stage", "bearish", None,
            "Stage 3 top" if weinstein_stage == "stage_3_top" else "Stage 4 decline",
            weinstein_note,
        ))

    if obv_slope_90d_pct is not None and obv_slope_90d_pct >= 5:
        signals.append(StrategySignal(
            "OBV (90d) — long-term", "bullish", obv_slope_90d_pct,
            f"{obv_slope_90d_pct:+.0f}% / 90d",
            "Cumulative buying pressure has been rising for 3+ months — sustained institutional flow.",
        ))
    elif obv_slope_90d_pct is not None and obv_slope_90d_pct <= -5:
        signals.append(StrategySignal(
            "OBV (90d) — long-term", "bearish", obv_slope_90d_pct,
            f"{obv_slope_90d_pct:+.0f}% / 90d",
            "Cumulative selling pressure has dominated for 3+ months — institutions have been leaving.",
        ))

    if cmf_60d is not None and cmf_60d >= 0.05:
        signals.append(StrategySignal(
            "Chaikin Money Flow (60d)", "bullish", cmf_60d,
            f"{cmf_60d:+.2f}",
            "Money flow positive across the last quarter — durable buying pressure.",
        ))
    elif cmf_60d is not None and cmf_60d <= -0.05:
        signals.append(StrategySignal(
            "Chaikin Money Flow (60d)", "bearish", cmf_60d,
            f"{cmf_60d:+.2f}",
            "Money flow negative across the last quarter — durable selling pressure.",
        ))

    if minervini_template:
        signals.append(StrategySignal(
            "Minervini Trend Template", "bullish", None,
            "50 > 150 > 200, rising",
            "Long-term moving averages in textbook bull alignment — historical home of multi-month winners.",
        ))

    if base_length_days >= 90:
        signals.append(StrategySignal(
            "Base length", "bullish", float(base_length_days),
            f"{base_length_days} sessions",
            "Price has been stair-stepping in a tight zone for 4+ months — institutions accumulating quietly.",
        ))

    if vol_qoq_growth_pct is not None and vol_qoq_growth_pct >= 15:
        signals.append(StrategySignal(
            "QoQ volume growth", "bullish", vol_qoq_growth_pct,
            f"{vol_qoq_growth_pct:+.0f}% q/q",
            "Average daily volume is materially higher this quarter than last — interest is broadening.",
        ))

    # ---------- Block + bulk deals (NSE institutional trade records) ----------
    block_deal_buy = 0
    block_deal_sell = 0
    block_deal_net_ratio = 0.0
    if symbol:
        try:
            from .block_deals import aggregate_30d
            deals = aggregate_30d(symbol)
            block_deal_buy = deals.buy_count
            block_deal_sell = deals.sell_count
            block_deal_net_ratio = deals.net_qty_ratio

            if (block_deal_buy + block_deal_sell) >= 2:
                if block_deal_net_ratio >= 0.30:
                    signals.append(StrategySignal(
                        "Block / Bulk deals (30d)", "bullish", block_deal_net_ratio,
                        f"{block_deal_buy}B/{block_deal_sell}S, net +{int(block_deal_net_ratio*100)}%",
                        "Net institutional buying via NSE block & bulk deal window over 30 days. "
                        "Literal trade records — much harder to fake than aggregated volume.",
                    ))
                elif block_deal_net_ratio <= -0.30:
                    signals.append(StrategySignal(
                        "Block / Bulk deals (30d)", "bearish", block_deal_net_ratio,
                        f"{block_deal_buy}B/{block_deal_sell}S, net {int(block_deal_net_ratio*100)}%",
                        "Net institutional selling via NSE block & bulk deals over 30 days. "
                        "Institutions are documented exiting.",
                    ))
        except Exception as e:
            log_local = logging  # avoid extra imports
            try:
                log_local.getLogger("volume_signals").warning(
                    "block deal aggregation failed for %s: %s", symbol, e)
            except Exception:
                pass

    # ---------- Composite score ----------
    # Long-term lens for a 6-month investment horizon: weight long-term heavily.
    base = sum(sub) / len(sub) if sub else 0.0  # medium-term composite

    long_score_parts: list[float] = []
    if obv_slope_90d_pct is not None:
        long_score_parts.append(max(-1.0, min(1.0, obv_slope_90d_pct / 30.0)))
    if obv_slope_180d_pct is not None:
        long_score_parts.append(max(-1.0, min(1.0, obv_slope_180d_pct / 50.0)))
    if cmf_60d is not None:
        long_score_parts.append(max(-1.0, min(1.0, cmf_60d * 5.0)))
    if up_down_90d is not None:
        if up_down_90d >= 1.0:
            long_score_parts.append(min(1.0, (up_down_90d - 1.0)))
        else:
            long_score_parts.append(max(-1.0, (up_down_90d - 1.0) / 0.5))
    if ma_30w_slope_pct is not None:
        long_score_parts.append(max(-1.0, min(1.0, ma_30w_slope_pct / 12.0)))

    long_score = sum(long_score_parts) / len(long_score_parts) if long_score_parts else 0.0

    # Stage-driven adjustments
    if weinstein_stage == "stage_2_advance":
        long_score = max(long_score, 0.4)
    elif weinstein_stage == "stage_1_to_2":
        long_score = max(long_score, 0.3)
    elif weinstein_stage == "stage_3_top":
        long_score = min(long_score, 0.0)
    elif weinstein_stage == "stage_4_decline":
        long_score = -1.0  # absolute reject

    if minervini_template:
        long_score += 0.10
    if base_length_days >= 90:
        long_score += 0.10
    if vol_qoq_growth_pct is not None and vol_qoq_growth_pct >= 15:
        long_score += 0.05

    # Block-deal boost: documented institutional trades carry weight.
    if block_deal_net_ratio >= 0.30 and (block_deal_buy + block_deal_sell) >= 2:
        long_score += 0.10
    elif block_deal_net_ratio <= -0.30 and (block_deal_buy + block_deal_sell) >= 2:
        long_score -= 0.20  # documented selling is a stronger negative

    long_score = max(-1.0, min(1.0, long_score))

    # Final composite: 55% long-term, 35% medium-term, 10% pattern bonuses
    raw_score = 0.55 * long_score + 0.35 * base + 0.10 * bonus + parabolic_pen

    # ---------- Entry timing classification ----------
    # Horizon-aware: a 6-month investor wants the spot BEFORE the multi-month
    # advance unfolds. The Weinstein stage is the primary anchor.
    timing, timing_note, timing_bonus = _classify_entry_timing(
        raw_score=raw_score,
        wyckoff_phase=phase,
        weinstein_stage=weinstein_stage,
        price_change_30d=price_change_30d_pct,
        price_change_180d=price_change_180d_pct,
        price_tightness=price_tightness_pct,
        obv_slope=obv_slope,
        obv_slope_90d=obv_slope_90d_pct,
        cmf=cmf,
        cmf_60d=cmf_60d,
        vol_trend=vol_trend_pct,
        vol_qoq_pct=vol_qoq_growth_pct,
        pocket_pivots=pp_count,
        vdu=vdu,
        base_length=base_length_days,
        minervini=minervini_template,
    )

    accum_score = max(-1.0, min(1.0, raw_score + timing_bonus))

    if accum_score >= 0.35:
        verdict: Verdict = "accumulating"
    elif accum_score <= -0.30:
        verdict = "distributing"
    else:
        verdict = "neutral"

    one_liner = _summarize(verdict, phase, signals)

    return AccumulationSignals(
        days_used=n,
        vol_recent_10d=vol_10,
        vol_avg_30d=vol_30,
        vol_avg_90d=vol_90,
        vol_trend_pct=round(vol_trend_pct, 1) if vol_trend_pct is not None else None,
        up_down_vol_ratio=round(up_down, 2) if up_down is not None else None,
        obv_slope_pct=round(obv_slope, 1) if obv_slope is not None else None,
        ad_line_slope_pct=round(ad_slope, 1) if ad_slope is not None else None,
        cmf_21d=round(cmf, 3) if cmf is not None else None,
        mfi_14d=round(mfi, 1) if mfi is not None else None,
        price_tightness_pct=round(price_tightness_pct, 1) if price_tightness_pct is not None else None,
        price_change_30d_pct=round(price_change_30d_pct, 2) if price_change_30d_pct is not None else None,
        vwap_60d=round(vwap_60, 2) if vwap_60 is not None else None,
        price_vs_vwap_pct=round(price_vs_vwap, 2) if price_vs_vwap is not None else None,
        # Long-term
        weinstein_stage=weinstein_stage,
        weinstein_note=weinstein_note,
        ma_30w=round(ma_30w, 2) if ma_30w is not None else None,
        ma_30w_slope_pct=round(ma_30w_slope_pct, 2) if ma_30w_slope_pct is not None else None,
        ma_50d=round(ma_50d, 2) if ma_50d is not None else None,
        ma_150d=round(ma_150d, 2) if ma_150d is not None else None,
        ma_200d=round(ma_200d, 2) if ma_200d is not None else None,
        minervini_template=minervini_template,
        obv_slope_90d_pct=round(obv_slope_90d_pct, 1) if obv_slope_90d_pct is not None else None,
        obv_slope_180d_pct=round(obv_slope_180d_pct, 1) if obv_slope_180d_pct is not None else None,
        obv_norm_slope_90d_pct=round(obv_norm_slope_90d_pct, 1) if obv_norm_slope_90d_pct is not None else None,
        obv_norm_slope_180d_pct=round(obv_norm_slope_180d_pct, 1) if obv_norm_slope_180d_pct is not None else None,
        cmf_60d=round(cmf_60d, 3) if cmf_60d is not None else None,
        up_down_vol_ratio_90d=round(up_down_90d, 2) if up_down_90d is not None else None,
        base_length_days=base_length_days,
        vol_qoq_growth_pct=round(vol_qoq_growth_pct, 1) if vol_qoq_growth_pct is not None else None,
        price_change_180d_pct=round(price_change_180d_pct, 2) if price_change_180d_pct is not None else None,
        # Patterns
        pocket_pivot_count_30d=pp_count,
        volume_dry_up=vdu,
        canslim_breakout=canslim,
        volume_event=volume_event,
        # Block + bulk deals
        block_deal_buy_count_30d=block_deal_buy,
        block_deal_sell_count_30d=block_deal_sell,
        block_deal_net_qty_ratio=block_deal_net_ratio,
        # Composite
        accum_score=round(accum_score, 3),
        verdict=verdict,
        wyckoff_phase=phase,
        entry_timing=timing,
        entry_timing_note=timing_note,
        one_liner=one_liner,
        signals=signals,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _pocket_pivot_count(window: pd.DataFrame) -> int:
    """Count Pocket Pivot days in the window.

    A Pocket Pivot fires on a day where Close > prev Close (up day) AND that
    day's volume is greater than the volume of the largest down day in the
    prior 10 trading sessions. Tight-range bonus implicit via the sliding
    10-day comparison window.
    """
    if window is None or len(window) < 12:
        return 0
    closes = window["Close"].reset_index(drop=True)
    vols = window["Volume"].reset_index(drop=True)
    count = 0
    for i in range(11, len(window)):
        if closes.iloc[i] <= closes.iloc[i - 1]:
            continue
        prev_window = window.iloc[i - 10 : i]
        prev_deltas = prev_window["Close"].diff().fillna(0)
        prev_down_vols = prev_window["Volume"][prev_deltas < 0]
        if prev_down_vols.empty:
            max_down_vol = 0.0
        else:
            max_down_vol = float(prev_down_vols.max())
        if vols.iloc[i] > max_down_vol and max_down_vol > 0:
            count += 1
    return int(count)


def _classify_entry_timing(
    *,
    raw_score: float,
    wyckoff_phase: WyckoffPhase,
    weinstein_stage: WeinsteinStage,
    price_change_30d: Optional[float],
    price_change_180d: Optional[float],
    price_tightness: Optional[float],
    obv_slope: Optional[float],
    obv_slope_90d: Optional[float],
    cmf: Optional[float],
    cmf_60d: Optional[float],
    vol_trend: Optional[float],
    vol_qoq_pct: Optional[float],
    pocket_pivots: int,
    vdu: bool,
    base_length: int,
    minervini: bool,
) -> tuple[EntryTiming, str, float]:
    """Horizon-aware entry timing for a 6-month investor.

    Goal: find the spot BEFORE the multi-month advance unfolds.
    Stage-anchored, with long-term volume confirmation required.
    """
    pc30 = price_change_30d if price_change_30d is not None else 0.0
    pc180 = price_change_180d if price_change_180d is not None else 0.0

    # Hard rejects — Stage 4 / distribution
    if (
        weinstein_stage == "stage_4_decline"
        or wyckoff_phase in ("distribution", "markdown")
        or raw_score < -0.10
    ):
        return (
            "missed",
            "Stage 4 / distribution — institutions are leaving. Exit zone, not entry.",
            -0.20,
        )

    # Bullish long-term confirmation gate
    long_term_bullish = (
        (obv_slope_90d is not None and obv_slope_90d >= 5)
        or (cmf_60d is not None and cmf_60d >= 0.05)
        or (vol_qoq_pct is not None and vol_qoq_pct >= 15)
        or weinstein_stage in ("stage_1_to_2", "stage_2_advance")
        or minervini
    )
    short_term_trigger = (
        pocket_pivots >= 1 or vdu or (cmf is not None and cmf >= 0.10) or (obv_slope or 0) >= 5
    )

    if not (long_term_bullish or short_term_trigger):
        return (
            "unknown",
            "Volume tape is not yet showing a multi-month institutional fingerprint — wait.",
            0.0,
        )

    # ---- EARLY: Stage 1->2 transition is the prize ----
    # Long base + long-term volume building + price still hasn't run = the spot.
    if (
        weinstein_stage == "stage_1_to_2"
        or (weinstein_stage == "stage_1_base" and long_term_bullish and pc30 < 5)
        or (long_term_bullish and pc30 < 8 and pc180 < 12 and base_length >= 60)
        or (vdu and base_length >= 45)
    ):
        return (
            "early",
            "Pre-breakout: 30wMA flattening / turning up, long-term OBV positive, price still flat. The spot before the multi-month advance.",
            +0.25,
        )

    # ---- MID: early Stage 2 — breakout has started, still room ----
    if weinstein_stage == "stage_2_advance" and pc180 < 25 and pc30 < 18:
        return (
            "mid",
            "Early Stage 2 advance: institutions are visibly buying and price has started moving but still has months of runway.",
            +0.10,
        )

    # ---- MID: just emerged from base ----
    if 8 <= pc30 <= 18 and long_term_bullish:
        return (
            "mid",
            "Mid-stage: breakout started but not yet extended. Long-term volume confirms.",
            +0.05,
        )

    # ---- LATE: mature Stage 2 / heading into Stage 3 ----
    if (
        weinstein_stage == "stage_3_top"
        or pc180 > 35
        or pc30 > 18
    ):
        return (
            "late",
            "Late stage: price has already run substantially OR distribution is starting to form. Wait for a pullback or pass.",
            -0.10,
        )

    # Default
    return (
        "early",
        "Pre-breakout: long-term volume is leading, price has not yet meaningfully moved.",
        +0.15,
    )


def _weinstein_stage(
    *,
    last_close: float,
    ma_30w: Optional[float],
    ma_30w_slope_pct: Optional[float],
    price_change_180d: Optional[float],
    price_change_30d: Optional[float],
    vol_trend_pct: Optional[float],
    obv_slope: Optional[float],
) -> tuple[WeinsteinStage, str]:
    """Stan Weinstein's four-stage classification — the long-term anchor.

    Stage 1: post-decline base. Price flat near the 30wMA, MA flat.
    Stage 1 -> 2: base breaking. Price rising through 30wMA, MA starting to slope up.
    Stage 2: confirmed advance. Price > 30wMA, MA rising, volume expanding.
    Stage 3: top forming. Price flat at highs, MA rolling over.
    Stage 4: decline. Price < 30wMA, MA falling, volume on red days.
    """
    if ma_30w is None or ma_30w_slope_pct is None:
        return "undefined", "Insufficient history (need ~9-12 months) to classify long-term stage."

    above = last_close > ma_30w
    below = last_close < ma_30w * 0.98
    slope_up = ma_30w_slope_pct > 1.0
    slope_down = ma_30w_slope_pct < -1.0
    slope_flat = abs(ma_30w_slope_pct) <= 1.0
    pc180 = price_change_180d if price_change_180d is not None else 0.0
    pc30 = price_change_30d if price_change_30d is not None else 0.0
    vt = vol_trend_pct or 0.0
    obv = obv_slope or 0.0

    if below and slope_down:
        return ("stage_4_decline",
                f"Stage 4 decline: price below 30wMA ({((last_close/ma_30w-1)*100):+.1f}%), MA sloping down ({ma_30w_slope_pct:+.1f}%). Avoid.")
    if above and slope_down and pc30 < 0:
        return ("stage_3_top",
                f"Stage 3 top forming: price flat at highs but 30wMA rolling over ({ma_30w_slope_pct:+.1f}%). Distribution risk.")
    if above and slope_up:
        return ("stage_2_advance",
                f"Stage 2 advance: price above 30wMA ({((last_close/ma_30w-1)*100):+.1f}%), MA sloping up ({ma_30w_slope_pct:+.1f}%). Trend in force.")
    if slope_flat and above and (vt > 5 or obv > 5):
        return ("stage_1_to_2",
                f"Stage 1->2 transition: 30wMA flattening, price above it, volume building. Setup for advance.")
    if slope_flat:
        return ("stage_1_base",
                f"Stage 1 base: 30wMA flat ({ma_30w_slope_pct:+.1f}%). Awaiting catalyst — patience or look elsewhere.")
    if slope_up and not above:
        return ("stage_1_to_2",
                f"Stage 1->2 transition: 30wMA starting to turn up ({ma_30w_slope_pct:+.1f}%) but price hasn't yet cleared.")
    return "undefined", "Mixed long-term signals — no clear stage."


def suggest_target_window(a: AccumulationSignals) -> TargetWindow:
    """Pick a holding-window suggestion from the long-term picture.

    Heuristic: a fresh Stage 1->2 transition or a tight VDU base is a "breakout
    pending" setup — typically resolves in 3 months ± 1. A Stage 1 base or
    early Stage 2 with sustained accumulation is a slower compounder — 6
    months ± 2. Mature Stage 2 we don't enter, so we don't suggest a window.
    """
    stage = a.weinstein_stage
    pc30 = a.price_change_30d_pct or 0.0
    pc180 = a.price_change_180d_pct or 0.0
    base = a.base_length_days or 0
    vdu = a.volume_dry_up
    pp = a.pocket_pivot_count_30d or 0

    # Breakout pending: fast resolution
    if (
        (stage == "stage_1_to_2" and pc30 < 6)
        or (vdu and base >= 45)
        or (a.canslim_breakout and pc30 < 10)
        or (stage == "stage_2_advance" and pp >= 2 and pc180 < 18)
    ):
        return TargetWindow(
            center_months=3.0,
            tolerance_months=1.0,
            label="3 months ± 1 month",
            rationale=(
                "Breakout setup: long base + volume signal firing now. Most of these "
                "resolve into a multi-month advance within ~3 months once they trigger. "
                "Watch for the move; review weekly."
            ),
        )

    # Slow compounder: long base, no immediate trigger
    if (
        stage == "stage_1_base"
        or (stage == "stage_1_to_2" and pc30 >= 6)
        or (base >= 90 and not vdu)
        or (stage == "stage_2_advance" and pc180 >= 18 and pc180 < 35)
    ):
        return TargetWindow(
            center_months=6.0,
            tolerance_months=2.0,
            label="6 months ± 2 months",
            rationale=(
                "Slow compounder: base is mature, accumulation is steady, no urgent breakout "
                "trigger. Best held through a full 6-month advance with periodic check-ins."
            ),
        )

    # Default middle
    return TargetWindow(
        center_months=4.5,
        tolerance_months=1.5,
        label="4-6 months",
        rationale=(
            "Default investing window. Re-evaluate at 4 months; exit by 6 unless the "
            "thesis has clearly strengthened."
        ),
    )


def _wyckoff_phase(
    obv_slope: Optional[float],
    cmf: Optional[float],
    price_change_30d: Optional[float],
    tightness: Optional[float],
    vol_trend_pct: Optional[float],
) -> WyckoffPhase:
    """Lightweight Wyckoff phase classification.

    Accumulation: tight base + rising OBV + positive CMF + price not yet up much.
    Markup:      price up + rising OBV + sustained volume (post-accumulation breakout).
    Distribution: tight top + falling OBV + negative CMF + price up a lot then stalling.
    Markdown:    price down + falling OBV + negative CMF.
    """
    if obv_slope is None and cmf is None:
        return "indeterminate"

    obv = obv_slope or 0.0
    money = cmf or 0.0
    pc30 = price_change_30d or 0.0
    tight = tightness or 100.0
    vt = vol_trend_pct or 0.0

    rising_flow = obv >= 5 or money >= 0.10
    falling_flow = obv <= -5 or money <= -0.10
    tight_base = tight < 10
    big_up = pc30 >= 8
    big_down = pc30 <= -8

    if rising_flow and tight_base and not big_up:
        return "accumulation"
    if rising_flow and big_up and vt >= 0:
        return "markup"
    if falling_flow and tight_base and pc30 > 0:
        return "distribution"
    if falling_flow and big_down:
        return "markdown"
    return "indeterminate"


def _summarize(
    verdict: Verdict,
    phase: WyckoffPhase,
    signals: list[StrategySignal],
) -> str:
    bulls = [s for s in signals if s.state == "bullish"]
    bears = [s for s in signals if s.state == "bearish"]

    head_phase = {
        "accumulation": "Wyckoff Accumulation phase: ",
        "markup": "Wyckoff Markup phase: ",
        "distribution": "Wyckoff Distribution phase: ",
        "markdown": "Wyckoff Markdown phase: ",
        "indeterminate": "",
    }[phase]

    head_verdict = {
        "accumulating": "Net buying pressure dominates.",
        "distributing": "Net selling pressure dominates.",
        "neutral": "No decisive pressure on either side.",
        "unknown": "Insufficient data.",
    }[verdict]

    detail_bits: list[str] = []
    for s in (bulls if verdict == "accumulating" else bears if verdict == "distributing" else bulls + bears)[:3]:
        detail_bits.append(f"{s.name} ({s.label})")
    detail = " | ".join(detail_bits)

    if detail:
        return f"{head_phase}{head_verdict} Top signals: {detail}."
    return f"{head_phase}{head_verdict}"


def _unknown(n: int) -> AccumulationSignals:
    return AccumulationSignals(
        days_used=n,
        vol_recent_10d=None, vol_avg_30d=None, vol_avg_90d=None,
        vol_trend_pct=None, up_down_vol_ratio=None,
        obv_slope_pct=None, ad_line_slope_pct=None,
        cmf_21d=None, mfi_14d=None,
        price_tightness_pct=None, price_change_30d_pct=None,
        vwap_60d=None, price_vs_vwap_pct=None,
        weinstein_stage="undefined",
        weinstein_note="",
        ma_30w=None, ma_30w_slope_pct=None,
        ma_50d=None, ma_150d=None, ma_200d=None,
        minervini_template=False,
        obv_slope_90d_pct=None, obv_slope_180d_pct=None,
        cmf_60d=None, up_down_vol_ratio_90d=None,
        base_length_days=0, vol_qoq_growth_pct=None,
        price_change_180d_pct=None,
        pocket_pivot_count_30d=0,
        volume_dry_up=False,
        canslim_breakout=False,
        block_deal_buy_count_30d=0,
        block_deal_sell_count_30d=0,
        block_deal_net_qty_ratio=0.0,
        accum_score=0.0,
        verdict="unknown",
        wyckoff_phase="indeterminate",
        entry_timing="unknown",
        entry_timing_note="",
        one_liner="Insufficient price/volume history to run the volume strategies.",
        signals=[],
    )
