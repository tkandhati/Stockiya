"""Pick Follow-up — the persistent "continuous eye on previous picks" tracker.

PRESENTATION / MONITORING ONLY. Nothing here changes selection, scoring, sizing,
or exits — the composite/BR gates and the ranker still own which stocks are
picked (PRINCIPLES.md; feedback: additive-over-redesign).

WHY THIS EXISTS (owner ask, 2026-08-29)
---------------------------------------
The Coiled Accumulators section was single-snapshot: a name appeared one day and
disappeared the next, with no tracking, and its "volume accumulation since we
suggested it" read as static. The owner wants a PERSISTENT, PORTFOLIO-DRIVEN
follow-up: every previous pick stays on a watch TABLE, ranked by accumulation
strength, and we can see — from the day we suggested it to today — whether volume
kept accumulating while price stayed flat (the "loaded spring" / re-entry case
after a false breakout → consolidation → dip → rise).

THE COHORT = THE PORTFOLIO
--------------------------
Driven off `portfolio.csv` (the ledger `record_picks` already maintains): every
row still `open` / `partial_t1` is a previous pick under continuous watch. That
makes the portfolio actually useful and gives each row a real suggestion date.

ACCUMULATION STRENGTH, SUGGESTION -> TODAY (not static)
-------------------------------------------------------
For each symbol we walk its per-day scan traces
(`data/traces/run_<date>_<sym>.jsonl`) from the suggestion date to the latest on
file, and score EACH day through the existing `accumulation_gauge` historical
spine (`gauge_from_trace_features`, a deterministic 0-100). That yields a real
day-by-day strength series — the "precalculated trajectory" the UI reveals on
expand — using the SAME engine the position cards use, so there is no recompute
drift. Days a symbol was rejected before the flow stages ran (shallow traces)
carry no scorable features and are skipped, not plotted as a misleading flat.

COILING CLASSIFICATION (improved over the snapshot)
---------------------------------------------------
"Coiling" does NOT mean price is flat (owner clarification, 2026-08-29). It means
volume KEEPS ADDING UP since the day we suggested it, while price merely
FLUCTUATES in a consolidation and has NOT yet delivered the expected move. Price
can drift up or down within the range — what defines the coil is (a) accumulation
still building and (b) the expected breakout hasn't happened. So we classify on
two independent axes, not a narrow price band:
  * volume axis  — is accumulation strength still strong / still rising since D?
  * price  axis  — has price reached its planned target ("the expected"), or is
                   it still consolidating below it (fluctuating), or broken down?

  coiling    volume still accumulating AND price still consolidating below its
             expected target (the re-entry tell)
  firing     price has reached / exceeded its planned target — the move delivered
  weakening  accumulation strength has fallen away (base going quiet / distribution)
  broke_down price fell hard through the base
  watch      anything else / not yet conclusive
Presentation-only, honest labels — nothing here gates a buy.

Pure + file-only: no network, never raises, deterministic (same files in ->
same rows out), so it runs live, in an offline replay, and in tests identically.
Reversible via env STOCKYA_FOLLOWUP_WATCH=0 (then the section is empty / absent).

Fix points (top of file):
    FOLLOWUP_LOOKBACK_DAYS      : how far back a suggestion may be and still be
                                  tracked (calendar days from today).
    BROKE_DOWN_PCT              : price-change % below which the thesis is
                                  breaking (price fell through the base).
    FIRING_FALLBACK_PCT         : "delivered the move" % used only when a pick has
                                  no planned target on file.
    COILING_MIN_LEVEL           : gauge level (1-5) at/above which volume is
                                  genuinely still adding up (the accumulation leg).
    COILING_STRENGTH_FADE       : strength drop (pts, now vs suggestion) at/below
                                  which accumulation is judged to be fading.
    CONSOLIDATION_TIGHT_PCT     : window close-range % at/below which the base is
                                  "small/tight" (else "big/wide").
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .accumulation_gauge import gauge_from_trace_features
from .position_history import (
    _all_trace_dates,
    _read_trace_stages,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"

# --------------------------------------------------------------------------- #
# Fix points
# --------------------------------------------------------------------------- #

# A suggestion older than this (calendar days) drops off the follow-up table.
# The owner asked for "yesterday to a month back"; 45 gives a little slack so a
# name suggested ~a month ago that is still coiling does not vanish mid-base.
FOLLOWUP_LOOKBACK_DAYS: int = 45

# Price change (%) since suggestion below which the thesis is breaking, not
# coiling — price fell through the base (a hard drawdown, not mere fluctuation).
BROKE_DOWN_PCT: float = -12.0

# "Delivered the expected move" threshold (%), used ONLY when the pick has no
# planned target on file. When a target IS known, "reached expected" is
# current_price >= the planned T1/target instead of this fallback.
FIRING_FALLBACK_PCT: float = 12.0

# Accumulation gauge level (1-5) at/above which volume is genuinely still "adding
# up" — the accumulation leg of a coil. A level-3 name only counts as coiling
# when its strength is still RISING since we suggested it.
COILING_MIN_LEVEL: int = 4

# Strength change (pts, now vs suggestion) at/below which accumulation is judged
# to be FADING (the base going quiet) rather than building.
COILING_STRENGTH_FADE: float = -15.0

# Close-range over the tracked window at/below which the base is "small/tight".
CONSOLIDATION_TIGHT_PCT: float = 12.0


def _is_num(x) -> bool:
    """True for a real, finite number (not None, not NaN, not bool)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _to_float(x) -> Optional[float]:
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _enabled() -> bool:
    """On by default; STOCKYA_FOLLOWUP_WATCH=0 disables the section entirely."""
    return os.environ.get("STOCKYA_FOLLOWUP_WATCH", "1") != "0"


# --------------------------------------------------------------------------- #
# Portfolio cohort — the previous picks we keep an eye on.
# --------------------------------------------------------------------------- #

_OPEN_STATUSES = frozenset({"open", "partial_t1"})


def _tracked_rows(today_iso: str) -> list[dict]:
    """Open portfolio rows suggested within the lookback window, newest first.

    Reads portfolio.csv via the portfolio module (single source of truth for the
    schema). Skips user-declined rows — no point watching something rejected.
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
    cutoff = today - timedelta(days=FOLLOWUP_LOOKBACK_DAYS)

    kept: list[dict] = []
    for r in rows:
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
        kept.append(r)

    # Newest suggestion first; the UI re-sorts by strength anyway.
    kept.sort(key=lambda r: r.get("entry_date") or "", reverse=True)
    return kept


# --------------------------------------------------------------------------- #
# Day-by-day accumulation strength (the precalculated trajectory).
# --------------------------------------------------------------------------- #

def _merge_trace_features(symbol: str, date_iso: str) -> dict:
    """Union of every stage's `features` dict for one trace day (later stages
    win on key clashes). Empty when the day's trace is absent or shallow."""
    feat: dict = {}
    for _sid, f in _read_trace_stages(symbol, date_iso).items():
        if isinstance(f, dict):
            feat.update(f)
    return feat


def accumulation_trajectory(symbol: str, since_date: str) -> list[dict]:
    """Strength score for each scannable day from `since_date` to the latest.

    One point per trace day that carries scorable flow features (shallow days —
    where the symbol was rejected before the flow stages ran — are skipped, not
    plotted as a fake flat). Each point:
        {date, score(0-100), level(1-5), color, label, close}

    Pure + file-only. Returns [] when there are no usable traces.
    """
    out: list[dict] = []
    for d in _all_trace_dates(symbol):
        if d < since_date:
            continue
        feat = _merge_trace_features(symbol, d)
        if not feat:
            continue
        close = _to_float(feat.get("current"))
        g = gauge_from_trace_features(feat, close=close, stop=None, as_of=d)
        # No scorable features that day -> skip rather than record a default 3.
        if g.get("score") is None:
            continue
        out.append({
            "date": d,
            "score": g["score"],
            "level": g["level"],
            "color": g["color"],
            "label": g["label"],
            "close": round(close, 2) if close is not None else None,
        })
    return out


# --------------------------------------------------------------------------- #
# Supports (file-based, rolling — no static S/R, per PRINCIPLES §9).
# --------------------------------------------------------------------------- #

def _base_low_from_pick(symbol: str, entry_date: str) -> Optional[float]:
    """The 25-bar base low persisted on the original pick payload — support1.

    Read from data/picks_<entry_date>.json (the day we suggested it). Same level
    the coiled section uses to say "enter on or before". File-only; None if the
    file / field is missing.
    """
    p = _DATA_DIR / f"picks_{entry_date}.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    sym_u = symbol.upper()
    for pk in payload.get("picks", []) or []:
        if (pk.get("symbol") or "").upper() != sym_u:
            continue
        esf = pk.get("entry_stage_features") or {}
        bl = _to_float(esf.get("base_low_25"))
        if bl is not None:
            return round(bl, 2)
    return None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def _consolidation(series: list[dict]) -> dict:
    """Best-effort base size from the close-range over the tracked window.

    "if possible" (owner). Uses the daily closes we already have in the strength
    series — no extra data dependency. Returns size None when too few points.
    """
    closes = [p["close"] for p in series if _is_num(p.get("close"))]
    if len(closes) < 3:
        return {"size": None, "range_pct": None, "days": len(series)}
    lo, hi = min(closes), max(closes)
    range_pct = round((hi / lo - 1) * 100, 1) if lo > 0 else None
    if range_pct is None:
        size = None
    elif range_pct <= CONSOLIDATION_TIGHT_PCT:
        size = "small"      # tight coil
    else:
        size = "big"        # wide/deep base
    return {"size": size, "range_pct": range_pct, "days": len(series)}


def _classify(
    level_now: Optional[int],
    price_change_pct: Optional[float],
    strength_change: Optional[float],
    reached_expected: Optional[bool],
) -> str:
    """Honest, presentation-only status label. Never gates a buy.

    Coiling is NOT "price flat" — it is volume still adding up while price
    consolidates BELOW its expected target (owner clarification 2026-08-29).
    Two axes: the price axis (broke down / delivered the move / still below
    expected) and the volume axis (accumulation still building vs fading).
    """
    if level_now is None or price_change_pct is None:
        return "no-data"
    # Price axis first.
    if price_change_pct < BROKE_DOWN_PCT:
        return "broke_down"             # hard drawdown through the base
    if reached_expected:
        return "firing"                 # price delivered the expected move
    # Below the expected target — still consolidating. Now the volume axis
    # decides whether it is a live coil or a fading base.
    fading = _is_num(strength_change) and strength_change <= COILING_STRENGTH_FADE
    if level_now <= 2 or fading:
        return "weakening"              # accumulation has fallen away
    building = (not _is_num(strength_change)) or strength_change >= 0
    if level_now >= COILING_MIN_LEVEL or (level_now >= 3 and building):
        return "coiling"                # volume still adding up, price consolidating
    return "watch"


def _why(status: str, strength_change: Optional[float], price_change_pct: Optional[float],
         cons: dict, expected_price: Optional[float]) -> str:
    days = cons.get("days") or 0
    size = cons.get("size")
    size_txt = {"small": "tight", "big": "wide"}.get(size or "", "")
    rng = cons.get("range_pct")
    pc = f"{price_change_pct:+.1f}%" if _is_num(price_change_pct) else "—"
    sc = f"{strength_change:+.0f}" if _is_num(strength_change) else "—"
    fluct = f", fluctuating in a ~{rng:.0f}% range" if _is_num(rng) else ""
    exp = f" (expected target ~{expected_price:.2f})" if _is_num(expected_price) else ""
    base = (
        f"{size_txt + ' ' if size_txt else ''}base tracked {days} scan-days{fluct}; "
        f"price since we suggested it {pc}{exp}; accumulation strength {sc} pts."
    )
    tail = {
        "coiling": " Volume still adding up while price consolidates below its expected move — the re-entry tell; watch for the trigger.",
        "firing": " Price has reached its expected target — the move delivered.",
        "weakening": " Accumulation strength has faded — the base is going quiet.",
        "broke_down": " Price fell through the base — thesis under pressure.",
        "watch": " Building — not yet conclusive.",
        "no-data": " No recent scan data to score.",
    }.get(status, "")
    return base + tail


# --------------------------------------------------------------------------- #
# Public builder
# --------------------------------------------------------------------------- #

def build_pick_followup(today_iso: Optional[str] = None) -> list[dict]:
    """Build the persistent follow-up table from the portfolio cohort.

    One row per open previous pick suggested within FOLLOWUP_LOOKBACK_DAYS,
    ranked by current accumulation strength (strongest first). Env-gated: []
    when STOCKYA_FOLLOWUP_WATCH=0. Never raises — a single bad row is skipped.

    Each row:
        {
          symbol, company, pick_id,
          suggested_date, days_tracked,
          entry_price, stop_price, expected_price, reached_expected,
          current_price, price_change_pct,
          accum_now:   {score, level, color, label} | None,
          accum_at_suggest: {score, level, ...} | None,
          strength_change,                 # score now - score at suggestion
          volume_still_building,           # accumulation maintained/rising since D
          status,                          # coiling | firing | weakening | broke_down | watch | no-data
          consolidation: {size, range_pct, days},
          support1, support1_basis,        # 25-bar base low
          support2, support2_basis,        # protective stop
          trajectory: [ {date, score, level, color, label, close}, ... ],
          why,
        }
    """
    if not _enabled():
        return []

    today_iso = today_iso or datetime.now().date().isoformat()
    rows: list[dict] = []

    for r in _tracked_rows(today_iso):
        try:
            symbol = (r.get("symbol") or "").strip()
            if not symbol:
                continue
            suggested_date = (r.get("entry_date") or "").strip()
            entry_price = _to_float(r.get("entry_price"))
            stop_price = _to_float(r.get("stop_price"))
            # "The expected" = the pick's own planned target (T1 preferred, else
            # T2/target). Used to tell "still consolidating below expected" from
            # "delivered the move". None when the pick has no target on file.
            expected_price = (
                _to_float(r.get("t1_price"))
                or _to_float(r.get("target_price"))
                or _to_float(r.get("t2_price"))
            )

            series = accumulation_trajectory(symbol, suggested_date)

            accum_now = None
            accum_at_suggest = None
            current_price = None
            if series:
                last = series[-1]
                first = series[0]
                accum_now = {k: last[k] for k in ("score", "level", "color", "label")}
                accum_at_suggest = {k: first[k] for k in ("score", "level", "color", "label")}
                # Latest close we actually have in the series (skips shallow days).
                for p in reversed(series):
                    if _is_num(p.get("close")):
                        current_price = p["close"]
                        break

            price_change_pct = None
            if _is_num(current_price) and _is_num(entry_price) and entry_price > 0:
                price_change_pct = round((current_price / entry_price - 1) * 100, 2)

            strength_change = None
            if accum_now and accum_at_suggest:
                strength_change = accum_now["score"] - accum_at_suggest["score"]

            level_now = accum_now["level"] if accum_now else None

            # Has price delivered the expected move? Prefer the concrete target;
            # fall back to a % move only when no target is on file.
            if _is_num(current_price) and _is_num(expected_price):
                reached_expected = current_price >= expected_price
            elif _is_num(price_change_pct):
                reached_expected = price_change_pct >= FIRING_FALLBACK_PCT
            else:
                reached_expected = None

            # Is volume still adding up since we suggested it? (maintained/rising)
            volume_still_building = (
                _is_num(strength_change) and strength_change >= 0
                and (level_now is not None and level_now >= 3)
            )

            status = _classify(level_now, price_change_pct, strength_change, reached_expected)
            cons = _consolidation(series)

            support1 = _base_low_from_pick(symbol, suggested_date)
            support2 = round(stop_price, 2) if _is_num(stop_price) else None

            rows.append({
                "symbol": symbol,
                "company": r.get("company") or symbol,
                "pick_id": r.get("pick_id") or "",
                "suggested_date": suggested_date,
                "days_tracked": len(series),
                "entry_price": round(entry_price, 2) if _is_num(entry_price) else None,
                "stop_price": support2,
                "expected_price": round(expected_price, 2) if _is_num(expected_price) else None,
                "reached_expected": reached_expected,
                "current_price": current_price,
                "price_change_pct": price_change_pct,
                "accum_now": accum_now,
                "accum_at_suggest": accum_at_suggest,
                "strength_change": strength_change,
                "volume_still_building": bool(volume_still_building),
                "status": status,
                "consolidation": cons,
                "support1": support1,
                "support1_basis": "25-bar base low" if support1 is not None else None,
                "support2": support2,
                "support2_basis": "protective stop" if support2 is not None else None,
                "trajectory": series,
                "why": _why(status, strength_change, price_change_pct, cons, expected_price),
            })
        except Exception:  # never let one bad row break the section
            continue

    # Rank strongest-accumulation-first; coiling springs float above firing/weak.
    _status_rank = {"coiling": 3, "firing": 2, "watch": 1, "weakening": 0, "broke_down": -1, "no-data": -2}

    def _sort_key(row: dict):
        an = row.get("accum_now") or {}
        score = an.get("score")
        return (
            score if _is_num(score) else -1.0,
            _status_rank.get(row.get("status"), -3),
            row.get("days_tracked") or 0,
        )

    rows.sort(key=_sort_key, reverse=True)
    return rows
