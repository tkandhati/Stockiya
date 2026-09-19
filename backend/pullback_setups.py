"""Section 2 — Pullback Re-Entry Setups on previously-picked stocks.

Owner ask (2026-09-19): the on-screen report is trimmed to two sections —
(1) Top picks by volume, and (2) THIS module: the classic
impulse → volume-dry-up (VDU) pullback → breakout re-entry setup, but evaluated
only on stocks we ALREADY picked, and only while market interest in them has
PERSISTED.

Two ideas the owner insisted on, encoded here:

1. **Interest is judged on accumulation-continuity, NOT raw volume size.** A
   *low*-volume down-day inside a pullback is a supply test (VDU) — nobody is
   selling — which is interest *persisting*, not fading. So the "interest gate"
   below never requires big volume; it reads OBV-continuity + up/down-volume +
   trend, and treats a quiet dip as bullish. Down-day volume is only ever read
   two ways: LOW (< 0.7×ADV50) = VDU/confirming, HIGH (> 1.2×ADV50) =
   distribution/invalidating. Nothing in between demands "decent" volume.

2. **Only the required interval of data.** We reuse `fetch.fetch_ohlcv`, which
   returns the shared required-interval window (~310 bars) already fetched for
   the scan — no extra 2y downloads, no Yahoo burst.

The 11-rule strategy (verbatim from the owner's spec):
  1. Need 100–200 daily candles.
  2. Trade with trend: current close > 50d SMA.
  3. Impulse day (Day 0): close ≥ +2% AND volume ≥ 1.5×ADV50.
  4. Watch the next 2–5 sessions for a pullback toward the 20d EMA or Day-0 low.
  5. Require ≥1 down-day with volume < 0.7×ADV50 (a VDU supply test).
  6. Pullback low must hold above / within 1% of support, never below Day-0 low.
  7. Buy when price breaks the prior candle's high on volume > prior day's.
  8. Stop-loss 0.5% below the pullback's lowest low.
  9. Target 1 at 2:1 reward-to-risk; take partial profit there.
 10. Invalidate if any pullback down-day volume > 1.2×ADV50, or a close < Day-0 low.

PRESENTATION / MONITORING ONLY: this never touches selection, scoring, ranking,
sizing or exits. Reversible via STOCKYA_PULLBACK_SETUPS=0.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from .fetch import fetch_ohlcv
from .indicators import (
    adv,
    obv,
    obv_norm_slope_pct,
    sma,
    up_down_vol_ratio,
)

log = logging.getLogger("pullback_setups")


# --------------------------------------------------------------------------- #
# Env knobs (safe defaults; unset == default). Every threshold is tunable so the
# owner can dial the setup without a code change.
# --------------------------------------------------------------------------- #
def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _envb(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def enabled() -> bool:
    return _envb("STOCKYA_PULLBACK_SETUPS", True)


# Cohort lookback — how far back a previous pick may have been suggested and
# still be watched here. Default 45 days (matches pick_followup); raise via env
# if a longer memory is wanted.
def _cohort_lookback_days() -> int:
    return max(1, _envi("STOCKYA_HISTSETUP_LOOKBACK_DAYS", 45))


# Setup parameters (rules 1–10).
MIN_BARS = 120                 # rule 1 (100–200 candles)
SMA_TREND = 50                 # rule 2
IMPULSE_MIN_PCT = 2.0          # rule 3
IMPULSE_VOL_MULT = 1.5         # rule 3
ADV_WINDOW = 50                # ADV50
IMPULSE_SCAN = 20              # look for the most-recent Day 0 within N sessions
PULLBACK_MIN = 2               # rule 4
PULLBACK_MAX = 5               # rule 4
EMA_PERIOD = 20                # rule 4/6 support
VDU_VOL_MULT = 0.7             # rule 5
SUPPORT_BUFFER = 0.01          # rule 6 (1%)
INVALID_VOL_MULT = 1.2         # rule 10
STOP_BUFFER = 0.005            # rule 8 (0.5%)
RR = 2.0                       # rule 9

# Interest-persisted gate (accumulation-continuity, not raw volume).
OBV_WINDOW = 90
UD_WINDOW = 90


def _interest_obv_min() -> float:
    return _envf("STOCKYA_INTEREST_OBV_SLOPE_MIN", 0.0)


def _interest_ud_min() -> float:
    return _envf("STOCKYA_INTEREST_UD_RATIO_MIN", 1.0)


_STATUS_LABEL = {
    "buy_trigger": "Buy trigger fired",
    "vdu_confirmed_watch_trigger": "VDU confirmed — watch for trigger",
    "in_pullback_vdu_pending": "In pullback — VDU pending",
    "awaiting_pullback": "Impulse in — awaiting pullback",
    "no_impulse": "No recent impulse day",
    "below_50sma": "Below 50-day trend",
    "invalidated": "Setup invalidated",
    "insufficient_history": "Not enough history",
    "interest_faded": "Interest faded",
}

# Display order: actionable first, dead last.
_STATUS_ORDER = {
    "buy_trigger": 0,
    "vdu_confirmed_watch_trigger": 1,
    "in_pullback_vdu_pending": 2,
    "awaiting_pullback": 3,
    "no_impulse": 4,
    "below_50sma": 5,
    "invalidated": 6,
    "insufficient_history": 7,
    "interest_faded": 8,
}


# --------------------------------------------------------------------------- #
# Cohort — previous picks still open, suggested within the lookback window.
# --------------------------------------------------------------------------- #
_OPEN_STATUSES = frozenset({"open", "partial_t1"})


def _cohort(today_iso: str) -> list[str]:
    """Distinct symbols of open portfolio rows suggested within the lookback.

    Reads portfolio.csv via the portfolio module (single source of truth). Skips
    user-declined rows. Newest suggestion first; duplicates removed.
    """
    try:
        from .portfolio import _read_portfolio
        rows = _read_portfolio()
    except Exception:
        return []

    try:
        today = date.fromisoformat(today_iso)
    except (TypeError, ValueError):
        today = datetime.now().date()
    cutoff = today - timedelta(days=_cohort_lookback_days())

    seen: set[str] = set()
    out: list[str] = []
    # Sort newest-first so the first occurrence of a symbol is its latest pick.
    for r in sorted(rows, key=lambda r: (r.get("entry_date") or ""), reverse=True):
        if (r.get("status") or "").strip() not in _OPEN_STATUSES:
            continue
        if (r.get("ownership") or "").strip() == "declined":
            continue
        ed = (r.get("entry_date") or "").strip()
        try:
            if date.fromisoformat(ed) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        sym = (r.get("symbol") or "").strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


# --------------------------------------------------------------------------- #
# Per-symbol setup evaluation.
# --------------------------------------------------------------------------- #
def _to_f(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f


def _round(x: Optional[float], n: int = 2) -> Optional[float]:
    return round(x, n) if isinstance(x, (int, float)) else None


def _delivery_pct(symbol: str) -> Optional[float]:
    """Latest NSE delivery % if a delivery file is on disk; None otherwise.

    Advisory only — never gates the setup (the owner noted low volume can still
    be interest). Best-effort, never raises.
    """
    try:
        from .delivery import delivery_advisory
        adv_ = delivery_advisory(symbol) or {}
        return _to_f(adv_.get("delivery_pct") or adv_.get("latest_delivery_pct"))
    except Exception:
        return None


def evaluate_symbol(symbol: str, df: pd.DataFrame, company: Optional[str] = None) -> dict:
    """Run the interest gate + 11-rule state machine on one symbol's OHLCV.

    `df` is ascending daily OHLCV (Open/High/Low/Close/Volume), last row = latest
    finalized session. Returns one row dict (see module docstring / plan for the
    shape). Never raises — a bad frame degrades to `insufficient_history`.
    """
    row: dict = {
        "symbol": symbol,
        "company": company or symbol,
        "status": "insufficient_history",
        "status_label": _STATUS_LABEL["insufficient_history"],
        "interest": {
            "persisted": False, "obv90_slope": None, "ud_ratio_90": None,
            "above_50sma": None, "delivery_pct": None, "reasons": [],
        },
        "day0_date": None, "day0_pct": None, "adv50": None,
        "support_base": None, "pullback_low": None, "vdu": False,
        "entry": None, "stop": None, "target1": None, "rr": None,
        "notes": "",
    }

    if df is None or df.empty or len(df) < MIN_BARS:
        row["notes"] = f"Need ≥{MIN_BARS} daily candles; have {0 if df is None else len(df)}."
        return row

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    n = len(df)

    # Rolling ADV50 = mean of the 50 sessions BEFORE each bar (so an impulse day's
    # own volume isn't in its baseline). NaN for the first 50 bars.
    adv_series = vol.rolling(ADV_WINDOW).mean().shift(1)
    ema20 = close.ewm(span=EMA_PERIOD, adjust=False).mean()

    sma50 = sma(close, SMA_TREND)
    last_close = float(close.iloc[-1])
    above_50 = bool(sma50 is not None and last_close >= sma50)

    # ---- Interest-persisted gate (accumulation-continuity, not raw volume) ----
    obv_series = obv(close, vol)
    obv90 = obv_norm_slope_pct(obv_series, OBV_WINDOW)
    ud90 = up_down_vol_ratio(close, vol, UD_WINDOW)
    deliv = _delivery_pct(symbol)

    obv_ok = obv90 is not None and obv90 >= _interest_obv_min()
    ud_ok = ud90 is not None and ud90 >= _interest_ud_min()
    reasons: list[str] = []
    reasons.append(
        f"OBV-90d {'rising' if obv_ok else 'not rising'}"
        + (f" ({obv90:+.0f}%)" if obv90 is not None else " (n/a)")
    )
    reasons.append(
        f"up/down-vol 90d {'≥' if ud_ok else '<'} {_interest_ud_min():.2f}"
        + (f" ({ud90:.2f})" if ud90 is not None else " (n/a)")
    )
    reasons.append(
        f"close {'above' if above_50 else 'below'} 50d SMA"
        + (f" ({last_close:.2f} vs {sma50:.2f})" if sma50 is not None else "")
    )
    if deliv is not None:
        reasons.append(f"delivery {deliv:.0f}%")

    persisted = bool(obv_ok and ud_ok and above_50)
    row["interest"] = {
        "persisted": persisted,
        "obv90_slope": _round(obv90),
        "ud_ratio_90": _round(ud90, 3),
        "above_50sma": above_50,
        "delivery_pct": _round(deliv),
        "reasons": reasons,
    }
    row["adv50"] = _round(_to_f(adv_series.iloc[-1]))
    row["support_base"] = _round(_to_f(ema20.iloc[-1]))

    if not persisted:
        row["status"] = "interest_faded"
        row["status_label"] = _STATUS_LABEL["interest_faded"]
        row["notes"] = "Interest not persisting — " + "; ".join(reasons) + "."
        return row

    # ---- Rule 2: trade with the trend (already true if interest persisted) ----
    if not above_50:
        row["status"] = "below_50sma"
        row["status_label"] = _STATUS_LABEL["below_50sma"]
        row["notes"] = "Price below the 50-day trend — no re-entry."
        return row

    # ---- Rule 3: most-recent impulse Day 0 within the scan window ----
    day0 = None
    scan_from = max(1, n - IMPULSE_SCAN)
    for i in range(n - 1, scan_from - 1, -1):
        adv_i = _to_f(adv_series.iloc[i])
        if adv_i is None or adv_i <= 0:
            continue
        day_pct = (float(close.iloc[i]) / float(close.iloc[i - 1]) - 1) * 100
        if day_pct >= IMPULSE_MIN_PCT and float(vol.iloc[i]) >= IMPULSE_VOL_MULT * adv_i:
            day0 = i
            break

    if day0 is None:
        row["status"] = "no_impulse"
        row["status_label"] = _STATUS_LABEL["no_impulse"]
        row["notes"] = (
            f"No day in the last {IMPULSE_SCAN} sessions closed ≥{IMPULSE_MIN_PCT:.0f}% "
            f"on ≥{IMPULSE_VOL_MULT:.1f}×ADV50. Interest is holding; wait for the spark."
        )
        return row

    day0_close = float(close.iloc[day0])
    day0_low = float(low.iloc[day0])
    day0_pct = (day0_close / float(close.iloc[day0 - 1]) - 1) * 100
    row["day0_date"] = df.index[day0].strftime("%Y-%m-%d")
    row["day0_pct"] = _round(day0_pct)

    # ---- Rules 4–6/10: inspect the pullback (sessions after Day 0) ----
    pull_idx = list(range(day0 + 1, n))
    n_after = len(pull_idx)

    invalidated_reason = ""
    vdu = False
    for j in pull_idx:
        adv_j = _to_f(adv_series.iloc[j])
        is_down = float(close.iloc[j]) < float(close.iloc[j - 1])
        if float(close.iloc[j]) < day0_low:
            invalidated_reason = f"close {float(close.iloc[j]):.2f} broke below Day-0 low {day0_low:.2f}"
            break
        if is_down and adv_j and float(vol.iloc[j]) > INVALID_VOL_MULT * adv_j:
            invalidated_reason = (
                f"down-day volume {float(vol.iloc[j]):,.0f} > {INVALID_VOL_MULT:.1f}×ADV50 "
                "(distribution)"
            )
            break
        if is_down and adv_j and float(vol.iloc[j]) < VDU_VOL_MULT * adv_j:
            vdu = True  # rule 5: quiet down-day = supply dry-up (bullish)

    if invalidated_reason:
        row["status"] = "invalidated"
        row["status_label"] = _STATUS_LABEL["invalidated"]
        row["vdu"] = vdu
        row["notes"] = f"Invalidated: {invalidated_reason}."
        return row

    pullback_low = float(low.iloc[pull_idx].min()) if pull_idx else day0_low
    row["pullback_low"] = _round(pullback_low)
    row["vdu"] = vdu

    ema_last = float(ema20.iloc[-1])
    support_floor = min(ema_last, day0_low)
    held = pullback_low >= support_floor * (1 - SUPPORT_BUFFER) and pullback_low >= day0_low

    if n_after < PULLBACK_MIN:
        row["status"] = "awaiting_pullback"
        row["status_label"] = _STATUS_LABEL["awaiting_pullback"]
        row["notes"] = (
            f"Impulse on {row['day0_date']} (+{day0_pct:.1f}%). "
            f"Only {n_after} session(s) since — need {PULLBACK_MIN}–{PULLBACK_MAX} "
            "of pullback before a re-entry can set up."
        )
        return row

    # ---- Rule 7: breakout trigger on the latest bar (expanding volume) ----
    trigger = (
        float(high.iloc[-1]) > float(high.iloc[-2])
        and float(vol.iloc[-1]) > float(vol.iloc[-2])
    )

    if trigger and vdu and held:
        # ---- Rules 8–9: stop, target, R:R ----
        entry = last_close
        stop = pullback_low * (1 - STOP_BUFFER)
        risk = entry - stop
        target1 = entry + RR * risk if risk > 0 else None
        row["status"] = "buy_trigger"
        row["status_label"] = _STATUS_LABEL["buy_trigger"]
        row["entry"] = _round(entry)
        row["stop"] = _round(stop)
        row["target1"] = _round(target1)
        row["rr"] = RR
        row["notes"] = (
            f"Broke prior high on expanding volume after a "
            f"{'VDU ' if vdu else ''}pullback. Entry ₹{entry:.2f}, stop ₹{stop:.2f} "
            f"(0.5% below pullback low), T1 ₹{target1:.2f} (2:1); take partial at T1."
            + ("" if n_after <= PULLBACK_MAX else
               f"  Note: pullback ran {n_after} sessions (>{PULLBACK_MAX}).")
        )
        return row

    # Not yet triggered — classify where in the pullback it sits.
    if vdu and held:
        row["status"] = "vdu_confirmed_watch_trigger"
        row["status_label"] = _STATUS_LABEL["vdu_confirmed_watch_trigger"]
        row["notes"] = (
            f"VDU supply test seen during the pullback; holding support "
            f"(low ₹{pullback_low:.2f}). Buy on a close above the prior bar's high "
            "with volume > the prior day."
        )
    else:
        row["status"] = "in_pullback_vdu_pending"
        row["status_label"] = _STATUS_LABEL["in_pullback_vdu_pending"]
        missing = []
        if not vdu:
            missing.append("no volume-dry-up down-day yet")
        if not held:
            missing.append("support not yet confirmed")
        row["notes"] = (
            f"In pullback ({n_after} session(s) since impulse) — "
            + "; ".join(missing) + ". Watching."
        )
    return row


# --------------------------------------------------------------------------- #
# Public builder.
# --------------------------------------------------------------------------- #
def build_pullback_setups(today_iso: str) -> list[dict]:
    """Evaluate the pullback re-entry setup on every previous pick with persisted
    interest. Returns rows ordered actionable-first. Empty when disabled, no
    cohort, or no data. Never raises (best-effort per symbol).
    """
    if not enabled():
        return []

    symbols = _cohort(today_iso)
    if not symbols:
        return []

    show_faded = _envb("STOCKYA_PULLBACK_SHOW_FADED", False)

    rows: list[dict] = []
    faded = 0
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym)
        except Exception as e:  # missing cache / unreachable source for this name
            log.debug("pullback_setups: fetch failed for %s: %s", sym, e)
            continue
        try:
            row = evaluate_symbol(sym, df)
        except Exception:
            log.exception("pullback_setups: evaluate failed for %s", sym)
            continue
        if row["status"] == "interest_faded" and not show_faded:
            faded += 1
            continue
        rows.append(row)

    if faded:
        log.info(
            "  [pullback_setups] %d previous pick(s) excluded — interest faded "
            "(set STOCKYA_PULLBACK_SHOW_FADED=1 to show them)",
            faded,
        )

    rows.sort(key=lambda r: (_STATUS_ORDER.get(r["status"], 99), r["symbol"]))
    return rows
