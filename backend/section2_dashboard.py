"""Section 2 — tiled monitoring dashboard over the recent-picks cohort.

Owner ask (2026-10-02): Section 2 (the "analysis on the last 45–60 days of
picks") should be a grid of small leaderboard TILES rather than one long table:

  * Top 5 by volume over 15 / 30 / 45 / 60 sessions
  * Top 5 by delivery %
  * A few other important-indicator tiles (accumulation, most-recommended)
  * PAST-LEARNINGS surfaced as tiles ONLY when they qualify as an important
    signal, with minimal badges (not noise).

Population = the cohort of PREVIOUS picks still open within the lookback window
(``pullback_setups._cohort`` — the single source of truth, read from
portfolio.csv). All metrics are computed from the same required-interval OHLCV
the scan already fetched (bhavcopy/offline on the run machine), so this adds no
extra data fetch.

Monitoring / presentation ONLY — never touches selection, scoring, ranking,
sizing or exits. Reversible: STOCKYA_PULLBACK_SETUPS=0 disables the whole
section (shared with the setups table). Volume ranking is RELATIVE (recent avg
vs the name's own ~250-session baseline) so a megacap's size doesn't dominate —
it answers "which recent picks are getting the most volume attention", the
app's volume-vs-baseline lens. Set STOCKYA_SECTION2_VOL_ABSOLUTE=1 to rank by
absolute average volume instead.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from .fetch import fetch_ohlcv
from .indicators import obv, obv_norm_slope_pct, sma, up_down_vol_ratio
from . import pullback_setups as ps

log = logging.getLogger("section2_dashboard")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PICKS_GLOB = str(_PROJECT_ROOT / "data" / "picks_*.json")
_PICKS_RE = re.compile(r"picks_(\d{4}-\d{2}-\d{2})\.json$")

# Volume leaderboard windows, in TRADING sessions (bars), not calendar days.
VOL_WINDOWS = (15, 30, 45, 60)
BASELINE_BARS = 250            # stable denominator for the relative-volume ratio
TOP_N = 5                      # top-5 per tile
_HIGH_DELIVERY = 60.0          # % — "buying taken home", the sanctioned delivery tilt
_REPEAT_MIN = 3                # appearances to count as a repeat recommendation
OBV_WINDOW = 90
UD_WINDOW = 90


def _envb(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _to_f(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _round(x: Optional[float], n: int = 2) -> Optional[float]:
    return round(x, n) if isinstance(x, (int, float)) else None


# --------------------------------------------------------------------------- #
# Recommendation frequency — how many times a symbol was recommended.
# --------------------------------------------------------------------------- #
def _appearance_counts(today_iso: str, window_days: int) -> dict[str, int]:
    """Count, per symbol, how many ``picks_<date>.json`` within the window listed
    it as a pick. One count per dated file (one trading day) — so it reads as
    "appeared on N of the last ~M pick days"."""
    try:
        today = date.fromisoformat(today_iso)
    except (TypeError, ValueError):
        today = datetime.now().date()
    cutoff = today - timedelta(days=window_days)
    counts: dict[str, int] = {}
    for path in glob.glob(_PICKS_GLOB):
        m = _PICKS_RE.search(path.replace("\\", "/"))
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff or d > today:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:  # noqa: BLE001 — a bad archive file must not break the build
            continue
        seen_in_file: set[str] = set()
        for p in payload.get("picks") or []:
            sym = (p.get("symbol") or "").strip()
            if sym and sym not in seen_in_file:
                seen_in_file.add(sym)
                counts[sym] = counts.get(sym, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Delivery % — prefer the per-symbol bhavcopy side-car, fall back to the
# daily-file advisory reader.
# --------------------------------------------------------------------------- #
def _delivery_pct(symbol: str) -> Optional[float]:
    # 1) per-symbol side-car written by backend/bhavcopy_fetch.py
    try:
        _, out_name = (symbol[:-3], symbol) if symbol.upper().endswith(".NS") else (symbol, symbol)
        p = _PROJECT_ROOT / "data" / "delivery" / f"{out_name}.csv"
        if not p.exists():
            p = _PROJECT_ROOT / "data" / "delivery" / f"{symbol.upper().replace('.NS','')}.csv"
        if p.exists():
            df = pd.read_csv(p)
            if "DeliveryPct" in df.columns and len(df):
                return _to_f(df["DeliveryPct"].dropna().iloc[-1])
    except Exception:  # noqa: BLE001
        pass
    # 2) the app's daily-file advisory reader (whole-market delivery_<date>.csv)
    return ps._delivery_pct(symbol)


# --------------------------------------------------------------------------- #
# Per-symbol metric block.
# --------------------------------------------------------------------------- #
def _metrics(symbol: str, df: pd.DataFrame, appearances: int,
            setup: Optional[dict]) -> Optional[dict]:
    if df is None or df.empty or "Volume" not in df.columns:
        return None
    vol = df["Volume"].astype(float)
    close = df["Close"].astype(float)
    if len(vol) < max(VOL_WINDOWS):
        baseline = _to_f(vol.mean())
    else:
        baseline = _to_f(vol.tail(BASELINE_BARS).mean())

    relvol: dict[str, Optional[float]] = {}
    avgvol: dict[str, Optional[float]] = {}
    for w in VOL_WINDOWS:
        avg = _to_f(vol.tail(w).mean()) if len(vol) >= 1 else None
        avgvol[str(w)] = avg
        relvol[str(w)] = (avg / baseline) if (avg is not None and baseline) else None

    obv_series = obv(close, vol)
    obv90 = obv_norm_slope_pct(obv_series, OBV_WINDOW)
    ud90 = up_down_vol_ratio(close, vol, UD_WINDOW)
    sma50 = sma(close, 50)
    above_50 = bool(sma50 is not None and float(close.iloc[-1]) >= sma50)

    return {
        "symbol": symbol,
        "company": (setup or {}).get("company") or symbol,
        "relvol": relvol,
        "avgvol": avgvol,
        "delivery_pct": _round(_delivery_pct(symbol)),
        "obv90_slope": _round(obv90),
        "ud_ratio_90": _round(ud90, 3),
        "above_50sma": above_50,
        "appearances": appearances,
        "status": (setup or {}).get("status"),
        "status_label": (setup or {}).get("status_label"),
    }


def _disp_sym(sym: str) -> str:
    return sym.replace(".NS", "")


def _leaderboard(metrics: list[dict], key_fn, fmt_fn, *, title: str, subtitle: str,
                tile_id: str) -> Optional[dict]:
    ranked = [(m, key_fn(m)) for m in metrics]
    ranked = [(m, v) for m, v in ranked if v is not None]
    if not ranked:
        return None
    ranked.sort(key=lambda t: t[1], reverse=True)
    rows = []
    for m, _v in ranked[:TOP_N]:
        value, extra = fmt_fn(m)
        rows.append({"symbol": m["symbol"], "display": _disp_sym(m["symbol"]),
                     "company": m["company"], "value": value, "extra": extra,
                     "status": m.get("status")})
    return {"id": tile_id, "title": title, "subtitle": subtitle, "rows": rows}


# --------------------------------------------------------------------------- #
# Public builder.
# --------------------------------------------------------------------------- #
def build_section2(today_iso: str) -> Optional[dict]:
    """Assemble the tiled Section-2 dashboard. Returns None when disabled or the
    cohort is empty (the caller then shows a friendly empty state). Never raises.
    """
    if not ps.enabled():
        return None

    window_days = max(ps._cohort_lookback_days(), _envi("STOCKYA_SECTION2_WINDOW_DAYS", 60))
    # Temporarily widen the cohort lookback to the dashboard window so "last
    # 45–60 days of picks" is honoured without mutating the setups-table default.
    prev = os.environ.get("STOCKYA_HISTSETUP_LOOKBACK_DAYS")
    os.environ["STOCKYA_HISTSETUP_LOOKBACK_DAYS"] = str(window_days)
    try:
        symbols = ps._cohort(today_iso)
    finally:
        if prev is None:
            os.environ.pop("STOCKYA_HISTSETUP_LOOKBACK_DAYS", None)
        else:
            os.environ["STOCKYA_HISTSETUP_LOOKBACK_DAYS"] = prev

    if not symbols:
        return {"window_days": window_days, "cohort_size": 0,
                "tiles": [], "learnings": [], "setups": []}

    appearances = _appearance_counts(today_iso, window_days)

    metrics: list[dict] = []
    setups: list[dict] = []
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym)
        except Exception as e:  # noqa: BLE001 — missing cache for this name
            log.debug("section2: fetch failed for %s: %s", sym, e)
            continue
        try:
            setup = ps.evaluate_symbol(sym, df)
        except Exception:  # noqa: BLE001
            log.exception("section2: evaluate failed for %s", sym)
            setup = None
        m = _metrics(sym, df, appearances.get(sym, 0), setup)
        if m is not None:
            metrics.append(m)
        if setup is not None:
            setups.append(setup)

    absolute = _envb("STOCKYA_SECTION2_VOL_ABSOLUTE", False)

    def _vol_fmt(w):
        def inner(m):
            if absolute:
                a = m["avgvol"].get(str(w))
                return (f"{a/1e5:.1f}L" if a else "—"), None
            r = m["relvol"].get(str(w))
            a = m["avgvol"].get(str(w))
            return (f"{r:.2f}×" if r else "—",
                    (f"{a/1e5:.1f}L avg" if a else None))
        return inner

    tiles: list[dict] = []
    for w in VOL_WINDOWS:
        tiles.append(_leaderboard(
            metrics,
            key_fn=lambda m, w=w: (m["avgvol"].get(str(w)) if absolute else m["relvol"].get(str(w))),
            fmt_fn=_vol_fmt(w),
            title=f"Top volume · {w}d",
            subtitle=("avg daily volume" if absolute else "recent volume vs its own norm"),
            tile_id=f"vol{w}",
        ))
    tiles.append(_leaderboard(
        metrics, key_fn=lambda m: m["delivery_pct"],
        fmt_fn=lambda m: (f"{m['delivery_pct']:.0f}%", None),
        title="Top delivery %", subtitle="buying taken to demat, not intraday churn",
        tile_id="delivery",
    ))
    tiles.append(_leaderboard(
        metrics, key_fn=lambda m: m["obv90_slope"],
        fmt_fn=lambda m: (f"{m['obv90_slope']:+.0f}%",
                          (f"U/D {m['ud_ratio_90']:.2f}" if m["ud_ratio_90"] else None)),
        title="Top accumulation", subtitle="OBV-90d net-rising (on-balance volume)",
        tile_id="obv",
    ))
    tiles.append(_leaderboard(
        metrics, key_fn=lambda m: (m["appearances"] or None),
        fmt_fn=lambda m: (f"{m['appearances']}×", "times recommended"),
        title="Most recommended", subtitle=f"appearances in the last {window_days}d of picks",
        tile_id="frequency",
    ))
    tiles = [t for t in tiles if t]

    # ---- Past-learnings tiles — only when they have qualifying members ----
    by_sym = {m["symbol"]: m for m in metrics}

    def _names(pred):
        return [{"symbol": s, "display": _disp_sym(s)} for s, m in by_sym.items() if pred(m)]

    learnings: list[dict] = []
    trig = [{"symbol": r["symbol"], "display": _disp_sym(r["symbol"])}
            for r in setups if r.get("status") in ("buy_trigger", "vdu_confirmed_watch_trigger")]
    if trig:
        learnings.append({
            "id": "trigger_fired", "badge": "⚡ Trigger",
            "label": "Trigger confirmed / close",
            "why": "Trigger confirmation is the one thing that separated winners from "
                   "losers in the offline study — only these have an actual spark.",
            "names": trig,
        })
    inval = [{"symbol": r["symbol"], "display": _disp_sym(r["symbol"])}
             for r in setups if r.get("status") == "invalidated"]
    if inval:
        learnings.append({
            "id": "distribution_risk", "badge": "⚠ Distribution",
            "label": "Distribution / trap risk",
            "why": "A heavy-volume down-day or a close below the Day-0 low — the "
                   "distribution-trap pattern to avoid near highs.",
            "names": inval,
        })
    hi_deliv = _names(lambda m: isinstance(m["delivery_pct"], (int, float))
                      and m["delivery_pct"] >= _HIGH_DELIVERY)
    if hi_deliv:
        learnings.append({
            "id": "high_delivery", "badge": "💰 Delivery",
            "label": f"High delivery (≥{_HIGH_DELIVERY:.0f}%)",
            "why": "High delivery % = real buying taken home (smart-money tilt), "
                   "not intraday churn.",
            "names": hi_deliv,
        })
    repeat = _names(lambda m: (m["appearances"] or 0) >= _REPEAT_MIN)
    if repeat:
        learnings.append({
            "id": "repeat", "badge": "🔁 Repeat",
            "label": f"Repeatedly surfaced (≥{_REPEAT_MIN}×)",
            "why": "Names that keep re-appearing show a persistent institutional "
                   "footprint, not a one-day blip.",
            "names": repeat,
        })

    # Actionable setups first, for the compact strip under the tiles.
    setups.sort(key=lambda r: (ps._STATUS_ORDER.get(r.get("status"), 99), r.get("symbol") or ""))

    return {
        "window_days": window_days,
        "cohort_size": len(symbols),
        "evaluated": len(metrics),
        "tiles": tiles,
        "learnings": learnings,
        "setups": setups,
    }
