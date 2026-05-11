"""Static demo fixtures used when DEMO_MODE=1.

Lets the UI be developed/demoed on networks where Yahoo Finance is blocked.
Each demo ticker has a deterministic OHLCV regime that exercises the volume
engine — some clearly accumulating, some distributing, some neutral — so the
picker output is meaningful even without live data.

No fundamentals fields (P/E, ROE, D/E, market cap) — Stockya is volume-only.
"""

import math
import random
from datetime import date, timedelta

import pandas as pd

# Per-ticker static snapshot — price/volume/MA labels only. Real values come
# from yfinance in non-demo mode.
DEMO_SNAPSHOTS: dict[str, dict] = {
    "RELIANCE.NS": {
        "symbol": "RELIANCE.NS", "company": "Reliance Industries Limited",
        "sector": "Energy", "industry": "Oil & Gas Refining & Marketing",
        "current": 1402.50, "day_change_pct": 0.62,
        "fifty_two_w_high": 1610.0, "fifty_two_w_low": 1115.0,
        "ma50": 1370.0, "ma200": 1310.0,
        "return_3m_pct": 6.4, "return_1y_pct": -3.1,
        "vol_today": 12_500_000, "vol_avg30": 9_800_000,
    },
    "TCS.NS": {
        "symbol": "TCS.NS", "company": "Tata Consultancy Services Limited",
        "sector": "Technology", "industry": "Information Technology Services",
        "current": 3520.00, "day_change_pct": -0.32,
        "fifty_two_w_high": 4592.0, "fifty_two_w_low": 3060.0,
        "ma50": 3580.0, "ma200": 3850.0,
        "return_3m_pct": -3.8, "return_1y_pct": -8.5,
        "vol_today": 2_400_000, "vol_avg30": 2_100_000,
    },
    "INFY.NS": {
        "symbol": "INFY.NS", "company": "Infosys Limited",
        "sector": "Technology", "industry": "Information Technology Services",
        "current": 1465.00, "day_change_pct": 0.18,
        "fifty_two_w_high": 2006.0, "fifty_two_w_low": 1310.0,
        "ma50": 1490.0, "ma200": 1620.0,
        "return_3m_pct": -5.0, "return_1y_pct": -12.8,
        "vol_today": 8_200_000, "vol_avg30": 7_400_000,
    },
    "HDFCBANK.NS": {
        "symbol": "HDFCBANK.NS", "company": "HDFC Bank Limited",
        "sector": "Financial Services", "industry": "Banks",
        "current": 1990.00, "day_change_pct": 0.92,
        "fifty_two_w_high": 2050.0, "fifty_two_w_low": 1574.0,
        "ma50": 1950.0, "ma200": 1830.0,
        "return_3m_pct": 9.2, "return_1y_pct": 25.4,
        "vol_today": 9_800_000, "vol_avg30": 9_100_000,
    },
    "ICICIBANK.NS": {
        "symbol": "ICICIBANK.NS", "company": "ICICI Bank Limited",
        "sector": "Financial Services", "industry": "Banks",
        "current": 1485.00, "day_change_pct": 0.55,
        "fifty_two_w_high": 1500.0, "fifty_two_w_low": 1148.0,
        "ma50": 1450.0, "ma200": 1340.0,
        "return_3m_pct": 8.6, "return_1y_pct": 31.2,
        "vol_today": 11_200_000, "vol_avg30": 10_400_000,
    },
    "AXISBANK.NS": {
        "symbol": "AXISBANK.NS", "company": "Axis Bank Limited",
        "sector": "Financial Services", "industry": "Banks",
        "current": 1180.00, "day_change_pct": -0.42,
        "fifty_two_w_high": 1340.0, "fifty_two_w_low": 996.0,
        "ma50": 1190.0, "ma200": 1130.0,
        "return_3m_pct": 4.1, "return_1y_pct": 9.8,
        "vol_today": 7_800_000, "vol_avg30": 8_500_000,
    },
    "KOTAKBANK.NS": {
        "symbol": "KOTAKBANK.NS", "company": "Kotak Mahindra Bank Limited",
        "sector": "Financial Services", "industry": "Banks",
        "current": 1840.00, "day_change_pct": 0.18,
        "fifty_two_w_high": 2070.0, "fifty_two_w_low": 1625.0,
        "ma50": 1820.0, "ma200": 1780.0,
        "return_3m_pct": 2.4, "return_1y_pct": 4.6,
        "vol_today": 4_900_000, "vol_avg30": 5_200_000,
    },
    "SBIN.NS": {
        "symbol": "SBIN.NS", "company": "State Bank of India",
        "sector": "Financial Services", "industry": "Banks",
        "current": 805.00, "day_change_pct": 0.78,
        "fifty_two_w_high": 912.0, "fifty_two_w_low": 680.0,
        "ma50": 790.0, "ma200": 770.0,
        "return_3m_pct": 5.2, "return_1y_pct": 10.4,
        "vol_today": 12_400_000, "vol_avg30": 13_100_000,
    },
    "INDUSINDBK.NS": {
        "symbol": "INDUSINDBK.NS", "company": "IndusInd Bank Limited",
        "sector": "Financial Services", "industry": "Banks",
        "current": 985.00, "day_change_pct": -1.15,
        "fifty_two_w_high": 1550.0, "fifty_two_w_low": 605.0,
        "ma50": 870.0, "ma200": 1090.0,
        "return_3m_pct": 18.2, "return_1y_pct": -28.6,
        "vol_today": 6_400_000, "vol_avg30": 7_800_000,
    },
    "HCLTECH.NS": {
        "symbol": "HCLTECH.NS", "company": "HCL Technologies Limited",
        "sector": "Technology", "industry": "Information Technology Services",
        "current": 1568.00, "day_change_pct": 0.22,
        "fifty_two_w_high": 1880.0, "fifty_two_w_low": 1365.0,
        "ma50": 1545.0, "ma200": 1620.0,
        "return_3m_pct": 1.5, "return_1y_pct": -4.2,
        "vol_today": 3_100_000, "vol_avg30": 3_400_000,
    },
    "WIPRO.NS": {
        "symbol": "WIPRO.NS", "company": "Wipro Limited",
        "sector": "Technology", "industry": "Information Technology Services",
        "current": 248.50, "day_change_pct": 0.05,
        "fifty_two_w_high": 320.0, "fifty_two_w_low": 220.0,
        "ma50": 250.0, "ma200": 270.0,
        "return_3m_pct": -3.4, "return_1y_pct": -10.6,
        "vol_today": 11_400_000, "vol_avg30": 12_000_000,
    },
    "TECHM.NS": {
        "symbol": "TECHM.NS", "company": "Tech Mahindra Limited",
        "sector": "Technology", "industry": "Information Technology Services",
        "current": 1495.00, "day_change_pct": 0.34,
        "fifty_two_w_high": 1810.0, "fifty_two_w_low": 1380.0,
        "ma50": 1460.0, "ma200": 1580.0,
        "return_3m_pct": -1.8, "return_1y_pct": -5.4,
        "vol_today": 2_100_000, "vol_avg30": 2_500_000,
    },
    "BHARTIARTL.NS": {
        "symbol": "BHARTIARTL.NS", "company": "Bharti Airtel Limited",
        "sector": "Communication Services", "industry": "Telecom Services",
        "current": 1822.00, "day_change_pct": 0.68,
        "fifty_two_w_high": 1920.0, "fifty_two_w_low": 1378.0,
        "ma50": 1790.0, "ma200": 1680.0,
        "return_3m_pct": 6.8, "return_1y_pct": 24.5,
        "vol_today": 5_900_000, "vol_avg30": 6_200_000,
    },
    "ITC.NS": {
        "symbol": "ITC.NS", "company": "ITC Limited",
        "sector": "Consumer Defensive", "industry": "Tobacco",
        "current": 425.00, "day_change_pct": 0.24,
        "fifty_two_w_high": 499.0, "fifty_two_w_low": 380.0,
        "ma50": 420.0, "ma200": 435.0,
        "return_3m_pct": 1.2, "return_1y_pct": -1.8,
        "vol_today": 14_500_000, "vol_avg30": 16_400_000,
    },
    "HINDUNILVR.NS": {
        "symbol": "HINDUNILVR.NS", "company": "Hindustan Unilever Limited",
        "sector": "Consumer Defensive", "industry": "Household & Personal Products",
        "current": 2415.00, "day_change_pct": -0.42,
        "fifty_two_w_high": 3035.0, "fifty_two_w_low": 2172.0,
        "ma50": 2440.0, "ma200": 2560.0,
        "return_3m_pct": -2.6, "return_1y_pct": -8.4,
        "vol_today": 1_400_000, "vol_avg30": 1_700_000,
    },
    "MARUTI.NS": {
        "symbol": "MARUTI.NS", "company": "Maruti Suzuki India Limited",
        "sector": "Consumer Cyclical", "industry": "Auto Manufacturers",
        "current": 12450.00, "day_change_pct": 1.05,
        "fifty_two_w_high": 13680.0, "fifty_two_w_low": 9700.0,
        "ma50": 12100.0, "ma200": 11400.0,
        "return_3m_pct": 7.8, "return_1y_pct": 12.4,
        "vol_today": 850_000, "vol_avg30": 920_000,
    },
    "TATAMOTORS.NS": {
        "symbol": "TATAMOTORS.NS", "company": "Tata Motors Limited",
        "sector": "Consumer Cyclical", "industry": "Auto Manufacturers",
        "current": 712.00, "day_change_pct": -0.85,
        "fifty_two_w_high": 1180.0, "fifty_two_w_low": 535.0,
        "ma50": 740.0, "ma200": 870.0,
        "return_3m_pct": -8.4, "return_1y_pct": -25.6,
        "vol_today": 24_500_000, "vol_avg30": 22_000_000,
    },
    "SUNPHARMA.NS": {
        "symbol": "SUNPHARMA.NS", "company": "Sun Pharmaceutical Industries Limited",
        "sector": "Healthcare", "industry": "Drug Manufacturers",
        "current": 1670.00, "day_change_pct": 0.45,
        "fifty_two_w_high": 1960.0, "fifty_two_w_low": 1430.0,
        "ma50": 1640.0, "ma200": 1720.0,
        "return_3m_pct": 1.8, "return_1y_pct": 6.2,
        "vol_today": 2_200_000, "vol_avg30": 2_600_000,
    },
    "DRREDDY.NS": {
        "symbol": "DRREDDY.NS", "company": "Dr. Reddy's Laboratories Limited",
        "sector": "Healthcare", "industry": "Drug Manufacturers",
        "current": 1240.00, "day_change_pct": -0.35,
        "fifty_two_w_high": 1422.0, "fifty_two_w_low": 1080.0,
        "ma50": 1230.0, "ma200": 1280.0,
        "return_3m_pct": 0.8, "return_1y_pct": -3.2,
        "vol_today": 1_100_000, "vol_avg30": 1_400_000,
    },
    "TATASTEEL.NS": {
        "symbol": "TATASTEEL.NS", "company": "Tata Steel Limited",
        "sector": "Basic Materials", "industry": "Steel",
        "current": 145.50, "day_change_pct": 1.45,
        "fifty_two_w_high": 168.0, "fifty_two_w_low": 122.0,
        "ma50": 140.0, "ma200": 145.0,
        "return_3m_pct": 4.2, "return_1y_pct": -2.6,
        "vol_today": 38_000_000, "vol_avg30": 42_000_000,
    },
    "LT.NS": {
        "symbol": "LT.NS", "company": "Larsen & Toubro Limited",
        "sector": "Industrials", "industry": "Engineering & Construction",
        "current": 3650.00, "day_change_pct": 0.55,
        "fifty_two_w_high": 3963.0, "fifty_two_w_low": 3050.0,
        "ma50": 3580.0, "ma200": 3520.0,
        "return_3m_pct": 4.6, "return_1y_pct": 8.2,
        "vol_today": 1_800_000, "vol_avg30": 2_100_000,
    },
    "BAJFINANCE.NS": {
        "symbol": "BAJFINANCE.NS", "company": "Bajaj Finance Limited",
        "sector": "Financial Services", "industry": "Credit Services",
        "current": 9120.00, "day_change_pct": -0.65,
        "fifty_two_w_high": 9650.0, "fifty_two_w_low": 6376.0,
        "ma50": 8950.0, "ma200": 7800.0,
        "return_3m_pct": 8.8, "return_1y_pct": 32.4,
        "vol_today": 2_400_000, "vol_avg30": 2_800_000,
    },
    "ASIANPAINT.NS": {
        "symbol": "ASIANPAINT.NS", "company": "Asian Paints Limited",
        "sector": "Basic Materials", "industry": "Specialty Chemicals",
        "current": 2350.00, "day_change_pct": -0.85,
        "fifty_two_w_high": 3422.0, "fifty_two_w_low": 2125.0,
        "ma50": 2400.0, "ma200": 2680.0,
        "return_3m_pct": -4.6, "return_1y_pct": -18.2,
        "vol_today": 2_400_000, "vol_avg30": 2_100_000,
    },
    "TITAN.NS": {
        "symbol": "TITAN.NS", "company": "Titan Company Limited",
        "sector": "Consumer Cyclical", "industry": "Luxury Goods",
        "current": 3380.00, "day_change_pct": 0.45,
        "fifty_two_w_high": 3886.0, "fifty_two_w_low": 2925.0,
        "ma50": 3320.0, "ma200": 3380.0,
        "return_3m_pct": 1.8, "return_1y_pct": -3.5,
        "vol_today": 1_400_000, "vol_avg30": 1_800_000,
    },
}


# --- Per-ticker demo OHLCV generation -----------------------------------------
# Each stock gets a "regime" that determines the volume/price relationship over
# the last ~130 trading days. The detector picks up these patterns naturally.
#
#   accumulating  – price flat-to-rising, recent volume > 30d avg, up-day vol > down-day
#   breakout      – tight base then late surge with heavy volume on green days
#   distributing  – price weakening, recent volume rising on red days
#   neutral       – low-conviction, mixed signals
#   parabolic     – up >25% in 30d (penalised — late retail)

_REGIMES: dict[str, str] = {
    # Accumulating leaders (clean institutional buying)
    "ICICIBANK.NS": "accumulating",
    "SBIN.NS": "accumulating",
    "BHARTIARTL.NS": "accumulating",
    "MARUTI.NS": "accumulating",
    "DRREDDY.NS": "accumulating",
    # Stealth-base breakout candidates
    "BAJFINANCE.NS": "breakout",
    "HDFCBANK.NS": "breakout",
    # Distributing — to be avoided (and exited)
    "TCS.NS": "distributing",
    "INFY.NS": "distributing",
    "WIPRO.NS": "distributing",
    "TECHM.NS": "distributing",
    "ASIANPAINT.NS": "distributing",
    "HINDUNILVR.NS": "distributing",
    "TATAMOTORS.NS": "distributing",
    # Neutral — no edge
    "RELIANCE.NS": "neutral",
    "HCLTECH.NS": "neutral",
    "AXISBANK.NS": "neutral",
    "KOTAKBANK.NS": "neutral",
    "INDUSINDBK.NS": "neutral",
    "ITC.NS": "neutral",
    "TATASTEEL.NS": "neutral",
    "LT.NS": "neutral",
    "SUNPHARMA.NS": "neutral",
    # Parabolic (up too much, too fast)
    "TITAN.NS": "parabolic",
}


def _bday_index(end: date, n: int) -> list[date]:
    """Return a list of the last `n` business-day dates ending at `end`."""
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def demo_ohlcv(symbol: str) -> pd.DataFrame:
    """Synthetic 1-year OHLCV with a regime-shaped long-term pattern."""
    snap = DEMO_SNAPSHOTS.get(symbol)
    if not snap or not snap.get("current"):
        return pd.DataFrame()

    regime = _REGIMES.get(symbol, "neutral")
    current = float(snap["current"])
    base_vol = float(snap.get("vol_avg30") or 5_000_000)

    days = 252  # ~1 trading year
    rng = random.Random(hash(symbol) & 0xFFFF_FFFF)
    dates = _bday_index(date.today(), days)

    closes: list[float] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []

    if regime == "accumulating":
        start = current * 0.92
        for i in range(days):
            progress = i / (days - 1)
            trend = start + (current - start) * progress
            wiggle = current * 0.012 * math.sin(i / 6.5) + rng.gauss(0, current * 0.006)
            c = trend + wiggle
            closes.append(c)
        for i, c in enumerate(closes):
            recent_boost = 1.25 if i >= days - 30 else 1.0
            up_bias = 1.18 if i > 0 and c >= closes[i - 1] else 0.85
            v = base_vol * recent_boost * up_bias * rng.uniform(0.85, 1.15)
            volumes.append(v)

    elif regime == "breakout":
        start = current * 0.94
        for i in range(days):
            if i < days - 20:
                c = start + start * 0.012 * math.sin(i / 5) + rng.gauss(0, start * 0.005)
            else:
                breakout_progress = (i - (days - 20)) / 20
                c = start + (current - start) * breakout_progress + rng.gauss(0, current * 0.008)
            closes.append(c)
        for i, c in enumerate(closes):
            if i < days - 20:
                v = base_vol * rng.uniform(0.7, 1.0)
            else:
                up_bias = 1.6 if i > 0 and c >= closes[i - 1] else 0.9
                v = base_vol * 1.7 * up_bias * rng.uniform(0.9, 1.2)
            volumes.append(v)

    elif regime == "distributing":
        start = current * 1.18
        for i in range(days):
            progress = i / (days - 1)
            trend = start + (current - start) * progress
            wiggle = current * 0.015 * math.sin(i / 7) + rng.gauss(0, current * 0.008)
            closes.append(trend + wiggle)
        for i, c in enumerate(closes):
            recent_boost = 1.20 if i >= days - 30 else 1.0
            down_bias = 1.35 if i > 0 and c < closes[i - 1] else 0.85
            v = base_vol * recent_boost * down_bias * rng.uniform(0.85, 1.15)
            volumes.append(v)

    elif regime == "parabolic":
        start = current * 0.78
        flat = start * 0.97
        for i in range(days):
            if i < days - 30:
                c = flat + flat * 0.01 * math.sin(i / 6) + rng.gauss(0, flat * 0.005)
            else:
                progress = (i - (days - 30)) / 30
                c = flat + (current - flat) * progress + rng.gauss(0, current * 0.01)
            closes.append(c)
        for i, c in enumerate(closes):
            if i < days - 30:
                v = base_vol * rng.uniform(0.7, 1.0)
            else:
                v = base_vol * rng.uniform(1.2, 1.8)
            volumes.append(v)

    else:  # neutral
        start = current * 0.97
        for i in range(days):
            c = start + (current - start) * (i / (days - 1))
            c += current * 0.02 * math.sin(i / 9) + rng.gauss(0, current * 0.01)
            closes.append(c)
        for _ in closes:
            volumes.append(base_vol * rng.uniform(0.85, 1.15))

    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c
        o = prev + rng.gauss(0, c * 0.003)
        spread = c * rng.uniform(0.005, 0.018)
        h = max(o, c) + spread * rng.uniform(0.4, 1.0)
        low = min(o, c) - spread * rng.uniform(0.4, 1.0)
        opens.append(o)
        highs.append(h)
        lows.append(low)

    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name="Date"),
    )


def demo_history_6m(symbol: str) -> list[dict]:
    df = demo_ohlcv(symbol)
    if df.empty:
        return []
    df = df.tail(130)
    return [
        {"date": ts.strftime("%Y-%m-%d"), "close": round(float(c), 2)}
        for ts, c in zip(df.index, df["Close"])
    ]
