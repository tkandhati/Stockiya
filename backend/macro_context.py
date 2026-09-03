"""Scoring-neutral MACRO / conviction context (owner ask, 2026-08-29).

Adds a *context* layer to build conviction on a coil — US-market correlation +
tailwind, sector leadership, and a coarse export-exposure tag. It answers "does
the wider backdrop agree?", NOT "should we buy?".

HARD RULE (PRINCIPLES §8: "do NOT override the volume signal with fundamentals"):
this is CONTEXT-ONLY. Nothing here touches selection, the composite/gates, the
coil-quality score, traction, or the ranking. It only annotates a row. This
mirrors the institutional-flow layer, which is likewise scoring-neutral.

BEST-EFFORT + NETWORK-OPTIONAL: US/benchmark series come from yfinance (cached to
disk once per ~day). Any failure — offline, firewall, yfinance missing, thin
history — degrades the relevant sub-block to None and NEVER raises. Env-gate
STOCKYA_MACRO_CONTEXT=0 disables the whole layer (keeps the app fully offline).

Correlation note: NSE and US sessions don't line up perfectly (different
holidays, US closes after IST). We correlate daily-close returns on the
overlapping dates — an approximate CONTEXT measure, not a precise beta.

Fix points (top of file):
    US_TICKERS / BENCH_TICKER / USDINR_TICKER : the yfinance symbols
    CORR_WINDOW            : trading days for the return correlation
    LEADERSHIP_WINDOW      : trading days for relative-strength vs the benchmark
    LEADERSHIP_BAND_PCT    : rel-return band that separates leader/laggard
    CACHE_TTL_HOURS        : how long the fetched market series stays fresh
    EXPORT_BY_SECTOR       : sector -> export-exposure tag
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_CONFIG_DIR = _PROJECT_ROOT / "config"
_CACHE = _DATA_DIR / "macro_cache.json"
_SECTOR_MAP_FILE = _CONFIG_DIR / "sector_map.json"

# --------------------------------------------------------------------------- #
# Fix points
# --------------------------------------------------------------------------- #
US_TICKERS: dict[str, str] = {"sp500": "^GSPC", "nasdaq": "^IXIC"}
BENCH_TICKER: str = "^NSEI"      # Nifty 50 — the leadership benchmark
USDINR_TICKER: str = "INR=X"     # USD/INR — rupee tailwind for exporters
CORR_WINDOW: int = 60
LEADERSHIP_WINDOW: int = 60
LEADERSHIP_BAND_PCT: float = 5.0
CACHE_TTL_HOURS: float = 18.0
_LOOKBACK_DAYS: int = 220

# Coarse export exposure per sector (no fundamentals — a sector heuristic).
EXPORT_BY_SECTOR: dict[str, str] = {
    "IT": "high",
    "Pharma": "high",
    "Textiles": "high",
    "Chemicals": "high",
    "Auto": "medium",
    "AutoAncillary": "medium",
    "Metals": "medium",
    "CapitalGoods": "medium",
    "Healthcare": "medium",
    "Conglomerate": "medium",
    "Banking": "low",
    "Financials": "low",
    "FMCG": "low",
    "Consumer": "low",
    "Infra": "low",
    "Logistics": "low",
    "Energy": "low",
    "Realty": "low",
    "Cement": "low",
    "Telecom": "low",
    "Sugar": "low",
}


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _enabled() -> bool:
    """On by default; STOCKYA_MACRO_CONTEXT=0 disables the whole layer."""
    return os.environ.get("STOCKYA_MACRO_CONTEXT", "1") != "0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Sector map (static, file-only)
# --------------------------------------------------------------------------- #
def load_sector_map() -> dict:
    """symbol -> sector, from config/sector_map.json. {} if the file is absent."""
    try:
        return json.loads(_SECTOR_MAP_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def sector_for(symbol: str, sector_map: dict) -> Optional[str]:
    if not symbol:
        return None
    return sector_map.get(symbol) or sector_map.get(symbol.upper()) or sector_map.get(
        symbol.upper().replace(".NS", "")
    )


def export_exposure(sector: Optional[str]) -> dict:
    """Coarse export-exposure tag from the sector (sector heuristic, not P&L)."""
    if not sector:
        return {"exposure": "unknown", "basis": None}
    exp = EXPORT_BY_SECTOR.get(sector, "unknown")
    return {
        "exposure": exp,
        "basis": (f"{sector} is a {exp}-export sector" if exp != "unknown" else None),
    }


# --------------------------------------------------------------------------- #
# Market-series fetch + cache (network-optional, never raises)
# --------------------------------------------------------------------------- #
def _series_to_dict(s: pd.Series) -> dict:
    out: dict[str, float] = {}
    for d, v in s.items():
        if pd.notna(v):
            out[pd.Timestamp(d).strftime("%Y-%m-%d")] = round(float(v), 4)
    return out


def _dict_to_series(d: Optional[dict]) -> Optional[pd.Series]:
    if not d:
        return None
    try:
        idx = pd.to_datetime(list(d.keys()))
        return pd.Series(list(d.values()), index=idx, dtype="float64").sort_index()
    except Exception:
        return None


def _fetch_close_history(ticker: str, lookback_days: int = _LOOKBACK_DAYS) -> Optional[pd.Series]:
    """Daily close series for one ticker via yfinance. None on ANY failure.

    Goes through `backend.yf_session` so these market-series fetches share the
    same global throttle / 429 backoff / day cache as every other Yahoo call —
    the deferred import keeps the offline default free of a hard yfinance dep.
    """
    try:
        from . import yf_session  # deferred: offline default carries no hard dep
    except Exception:
        return None
    try:
        t = yf_session.get_ticker(ticker)
        sig = f"macro_p{lookback_days}d_{_now().strftime('%Y-%m-%d')}"
        h = yf_session.history(t, ticker, sig, period=f"{lookback_days}d", auto_adjust=True)
        if h is None or h.empty or "Close" not in h:
            return None
        s = h["Close"].copy()
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
        return s.dropna()
    except Exception:
        return None


def fetch_market_series(force: bool = False) -> dict:
    """{name: {date_iso: close}} for sp500/nasdaq/bench/usdinr, cached ~daily.

    Returns the freshest data it can: a fresh fetch, else the last good cache,
    else {}. Never raises; {} when the layer is disabled.
    """
    if not _enabled():
        return {}

    cache: dict = {}
    if _CACHE.exists():
        try:
            cache = json.loads(_CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}

    if not force:
        ts = cache.get("fetched_at")
        if ts:
            try:
                if _now() - datetime.fromisoformat(ts) < timedelta(hours=CACHE_TTL_HOURS):
                    return cache.get("series") or {}
            except ValueError:
                pass

    series: dict[str, dict] = {}
    tickers = {**US_TICKERS, "bench": BENCH_TICKER, "usdinr": USDINR_TICKER}
    for name, tk in tickers.items():
        s = _fetch_close_history(tk)
        if s is not None and len(s):
            series[name] = _series_to_dict(s)

    if series:
        try:
            _CACHE.write_text(
                json.dumps({"fetched_at": _now().isoformat(), "series": series}),
                encoding="utf-8",
            )
        except OSError:
            pass
        return series
    # Fetch failed entirely (offline) — fall back to any stale cache.
    return cache.get("series") or {}


# --------------------------------------------------------------------------- #
# Pure analytics (deterministic, testable — no I/O)
# --------------------------------------------------------------------------- #
def market_regime(closes: Optional[pd.Series]) -> Optional[str]:
    """US regime from a close series: tailwind / headwind / neutral / None.

    tailwind  = above a rising 50d MA
    headwind  = below the 50d MA
    neutral   = above a flat/falling 50d MA
    """
    if closes is None or len(closes) < 60:
        return None
    ma50 = closes.rolling(50).mean()
    last = float(closes.iloc[-1])
    m_now = float(ma50.iloc[-1])
    m_prev = float(ma50.iloc[-11])
    if not (math.isfinite(m_now) and math.isfinite(m_prev)):
        return None
    if last < m_now:
        return "headwind"
    return "tailwind" if m_now > m_prev else "neutral"


def returns_correlation(
    a: Optional[pd.Series], b: Optional[pd.Series], window: int = CORR_WINDOW
) -> Optional[float]:
    """Correlation of daily-close returns on overlapping dates. None if thin."""
    if a is None or b is None:
        return None
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if len(df) < 12:
        return None
    r = df.pct_change().dropna()
    if len(r) < 10:
        return None
    r = r.iloc[-window:]
    c = r["a"].corr(r["b"])
    return round(float(c), 2) if pd.notna(c) else None


def _return_pct(s: Optional[pd.Series], window: int) -> Optional[float]:
    if s is None or len(s) < window + 1:
        return None
    base = float(s.iloc[-window - 1])
    if base <= 0:
        return None
    return (float(s.iloc[-1]) / base - 1) * 100.0


def leadership(
    stock: Optional[pd.Series], bench: Optional[pd.Series], window: int = LEADERSHIP_WINDOW
) -> Optional[dict]:
    """Relative strength vs the benchmark over `window` days: leader/inline/laggard."""
    rs = _return_pct(stock, window)
    rb = _return_pct(bench, window)
    if rs is None or rb is None:
        return None
    diff = rs - rb
    if diff >= LEADERSHIP_BAND_PCT:
        label = "leader"
    elif diff <= -LEADERSHIP_BAND_PCT:
        label = "laggard"
    else:
        label = "inline"
    return {
        "stock_return_pct": round(rs, 1),
        "bench_return_pct": round(rb, 1),
        "rel_pct": round(diff, 1),
        "label": label,
    }


def _us_note(sp_corr: Optional[float], regime: Optional[str]) -> str:
    if not _is_num(sp_corr) and regime is None:
        return "US linkage unknown (no data)."
    strength = ""
    if _is_num(sp_corr):
        a = abs(sp_corr)
        strength = "tracks US closely" if a >= 0.5 else ("some US linkage" if a >= 0.25 else "little US linkage")
        if sp_corr < -0.25:
            strength = "moves opposite the US"
    reg = {
        "tailwind": "US in an uptrend — tailwind",
        "headwind": "US below its 50d MA — headwind",
        "neutral": "US trend flat",
    }.get(regime or "", "")
    parts = [p for p in (strength, reg) if p]
    return "; ".join(parts) + "." if parts else "US linkage unknown."


# --------------------------------------------------------------------------- #
# Per-symbol context builder
# --------------------------------------------------------------------------- #
def _stock_close_series(symbol: str) -> Optional[pd.Series]:
    """Stock daily closes via the app's data source (bhavcopy/yahoo). None-safe."""
    try:
        from .fetch import fetch_ohlcv
        df = fetch_ohlcv(symbol, lookback_days=_LOOKBACK_DAYS)
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df:
        return None
    try:
        s = df["Close"].copy()
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
        return s.dropna()
    except Exception:
        return None


def build_context(symbol: str, market: dict, sector_map: dict) -> Optional[dict]:
    """Scoring-neutral conviction context for one symbol. None when nothing is
    known or the layer is disabled. Never raises."""
    if not _enabled():
        return None
    try:
        stock = _stock_close_series(symbol)
        sp = _dict_to_series((market or {}).get("sp500"))
        nq = _dict_to_series((market or {}).get("nasdaq"))
        bench = _dict_to_series((market or {}).get("bench"))

        sp_corr = returns_correlation(stock, sp)
        nq_corr = returns_correlation(stock, nq)
        regime = market_regime(sp)
        us = {
            "sp500_corr": sp_corr,
            "nasdaq_corr": nq_corr,
            "regime": regime,
            "note": _us_note(sp_corr, regime),
        }

        lead = leadership(stock, bench)
        sector = sector_for(symbol, sector_map)
        exp = export_exposure(sector)

        # Nothing meaningful known -> no context block at all.
        if sp_corr is None and regime is None and lead is None and sector is None:
            return None

        return {
            "us": us,
            "leadership": lead,
            "sector": sector,
            "export": exp,
            "disclaimer": "Context only — does not change the coil ranking.",
        }
    except Exception:
        return None
