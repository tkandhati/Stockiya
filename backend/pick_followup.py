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
        {date, score(0-100), level(1-5), color, label, close,
         obv90(continuous OBV-90d slope %), ud90(up/down-vol ratio 90d)}

    NOTE: `score` is the coarse 0-100 health gauge — it saturates at 100 for any
    strong name, so it is NOT a good discriminator. `obv90` is the CONTINUOUS
    accumulation figure the chart plots and the coil-quality ranker uses.

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
        obv90 = _to_float(feat.get("obv_90d_slope_pct"))
        ud90 = _to_float(feat.get("up_down_vol_ratio_90d"))
        out.append({
            "date": d,
            "score": g["score"],
            "level": g["level"],
            "color": g["color"],
            "label": g["label"],
            "close": round(close, 2) if close is not None else None,
            "obv90": round(obv90, 1) if obv90 is not None else None,
            "ud90": round(ud90, 2) if ud90 is not None else None,
        })
    return out


# --------------------------------------------------------------------------- #
# Coil quality — the CONTINUOUS ranking metric (fixes the saturating gauge).
#
# The owner ask (2026-08-29): put "volume still being added but price barely
# moved" on top. The 0-100 health gauge pegs at 100 for every strong name and
# cannot order them. This blends two continuous axes instead:
#   * volume_add    — how strongly volume is still accumulating (OBV-90d + up/down)
#   * price_stillness — how little price has moved since we suggested it
# so the tightest coils (strong accumulation, flat price) score highest, and a
# name that already ran is discounted even if its volume is strong.
# --------------------------------------------------------------------------- #

# Price move (%) at/above which "stillness" is fully spent — a name that has
# moved this much off the suggestion is no longer a slight-change coil.
COIL_PRICE_FLAT_REF_PCT: float = 10.0
# OBV-90d slope (%) that maps to full volume-add credit. Scaled (not stepped) so
# strong names still spread out instead of all pegging at the top.
COIL_OBV_FULL_PCT: float = 25.0
# up/down-vol-90d that maps to full credit (1.0 = balanced buying/selling).
COIL_UD_FULL: float = 1.4
# Weights on the two axes of the coil score (sum to 1.0). Stillness is weighted
# a touch higher so "price barely moved" is the tie-breaker among strong-volume
# names — exactly the owner's ordering ask.
COIL_W_VOLUME: float = 0.45
COIL_W_STILLNESS: float = 0.55


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def coil_quality(
    obv90: Optional[float],
    ud90: Optional[float],
    price_change_pct: Optional[float],
) -> dict:
    """Continuous coil-quality: {score(0-100)|None, volume_add(0-1), price_stillness(0-1)}.

    None score when we cannot measure volume (no obv90/ud90) — such rows sort
    last rather than faking a number.
    """
    if not _is_num(obv90) and not _is_num(ud90):
        return {"score": None, "volume_add": None, "price_stillness": None}

    f_obv = _clamp01((obv90 / COIL_OBV_FULL_PCT)) if _is_num(obv90) else 0.0
    f_ud = _clamp01(((ud90 - 1.0) / (COIL_UD_FULL - 1.0))) if _is_num(ud90) else 0.0
    # Blend the two volume signals; if only one is present, use it alone.
    if _is_num(obv90) and _is_num(ud90):
        volume_add = 0.65 * f_obv + 0.35 * f_ud
    else:
        volume_add = f_obv if _is_num(obv90) else f_ud

    if _is_num(price_change_pct):
        price_stillness = _clamp01(1.0 - abs(price_change_pct) / COIL_PRICE_FLAT_REF_PCT)
    else:
        price_stillness = 0.5  # unknown price move -> neutral, don't over-reward

    score = 100.0 * (COIL_W_VOLUME * volume_add + COIL_W_STILLNESS * price_stillness)
    return {
        "score": round(score),
        "volume_add": round(volume_add, 2),
        "price_stillness": round(price_stillness, 2),
    }


# --------------------------------------------------------------------------- #
# Traction — leading clues that a loaded coil is starting to FIRE.
#
# Coil quality says "is this a loaded spring?"; traction says "is it starting to
# go off?" — the early, volume-based tells that precede the breakout, read from
# the latest scan trace (owner ask, 2026-08-29 "some clue to find traction").
# All file-only, presentation-only.
# --------------------------------------------------------------------------- #

# Within this % below the 20d breakout counts as "coiled right under the pivot".
TRACTION_NEAR_PIVOT_PCT: float = 4.0
# Today's / 5d volume vs its 50d average at/above which demand is "expanding".
TRACTION_VOL_EXPAND: float = 1.3
# Close-in-upper-third ratio at/above which price is "closing strong".
TRACTION_UPPER_THIRD: float = 0.5


def assess_traction(feat: dict) -> dict:
    """Leading traction clues from the latest scan trace.

    Returns {level, distance_to_pivot_pct, pivot_price, clues:[str], note}.
    level: breaking_out | building | early | quiet | unknown.
    Never raises; unknown when the trace has no breakout stage.
    """
    feat = feat or {}
    break_pct = _to_float(feat.get("break_pct"))
    pivot = _to_float(feat.get("resistance_20d"))
    infl = feat.get("obv_flow_inflection")
    s_short = _to_float(feat.get("obv_slope_short_pct"))
    s_long = _to_float(feat.get("obv_slope_long_pct"))
    vol_today = _to_float(feat.get("vol_ratio_today_50d"))
    vol_5_50 = _to_float(feat.get("vol_ratio_5_50"))
    upper = _to_float(feat.get("upper_third_ratio"))
    anomalies = _to_float(feat.get("anomaly_cluster_count_15d"))

    if not _is_num(break_pct) and not _is_num(s_short):
        return {
            "level": "unknown",
            "distance_to_pivot_pct": None,
            "pivot_price": round(pivot, 2) if _is_num(pivot) else None,
            "clues": [],
            "note": "No recent breakout trace to read traction from.",
        }

    clues: list[str] = []
    flow_up = (infl == "healing") or (
        _is_num(s_short) and _is_num(s_long) and s_short > s_long and s_short > 0
    )
    if flow_up:
        clues.append("OBV flow turning up (short-term accelerating)")
    if (_is_num(vol_today) and vol_today >= TRACTION_VOL_EXPAND) or (
        _is_num(vol_5_50) and vol_5_50 >= TRACTION_VOL_EXPAND
    ):
        clues.append("volume expanding vs its 50-day average")
    if _is_num(upper) and upper >= TRACTION_UPPER_THIRD:
        clues.append("closing in the upper part of its range")
    if _is_num(anomalies) and anomalies >= 1:
        n = int(anomalies)
        clues.append(f"{n} volume-spike day{'s' if n != 1 else ''} in the last 15")

    near_pivot = _is_num(break_pct) and -TRACTION_NEAR_PIVOT_PCT <= break_pct < 0
    dist = round(-break_pct, 1) if (_is_num(break_pct) and break_pct < 0) else 0.0

    if _is_num(break_pct) and break_pct >= 0:
        level = "breaking_out"
    elif near_pivot and len(clues) >= 2:
        level = "building"
    elif clues:
        level = "early"
    else:
        level = "quiet"

    if level == "breaking_out":
        note = (
            f"Above its 20-day breakout"
            + (f" (~{pivot:.0f})" if _is_num(pivot) else "")
            + " — the trigger is firing now."
        )
    elif _is_num(pivot):
        note = f"Trigger: a close above ~{pivot:.0f} ({dist:.1f}% away)."
    else:
        note = "Watch for a close above the recent 20-day high."

    return {
        "level": level,
        "distance_to_pivot_pct": dist,
        "pivot_price": round(pivot, 2) if _is_num(pivot) else None,
        "clues": clues,
        "note": note,
    }


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
    ranked by CONTINUOUS coil quality (volume-add x price-stillness), strongest
    first, so "volume still adding, price barely moved" tops the list and one row
    is flagged is_top_pick. Env-gated: [] when STOCKYA_FOLLOWUP_WATCH=0. Never
    raises — a single bad row is skipped.

    Each row:
        {
          symbol, company, pick_id,
          suggested_date, days_tracked,
          entry_price, stop_price, expected_price, reached_expected,
          current_price, price_change_pct,
          coil_score(0-100)|None,          # THE ranking metric (continuous)
          volume_add(0-1), price_stillness(0-1),   # its two components
          obv90_now, obv90_start, ud90_now,        # continuous accumulation figures
          is_top_pick,                     # the single best live coil to act on
          accum_now:   {score, level, color, label} | None,  # coarse health gauge
          accum_at_suggest: {score, level, ...} | None,
          strength_change,                 # gauge score now - at suggestion
          volume_still_building,           # OBV-90d positive and not falling since D
          status,                          # coiling | firing | weakening | broke_down | watch | no-data
          consolidation: {size, range_pct, days},
          support1, support1_basis,        # 25-bar base low
          support2, support2_basis,        # protective stop
          trajectory: [ {date, score, level, color, label, close, obv90, ud90}, ... ],
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
            obv90_now = None
            obv90_start = None
            ud90_now = None
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
                # Continuous accumulation figures (first vs latest) — these drive
                # the coil-quality ranker and the chart, not the saturating gauge.
                obv90_now = last.get("obv90")
                obv90_start = first.get("obv90")
                ud90_now = last.get("ud90")

            price_change_pct = None
            if _is_num(current_price) and _is_num(entry_price) and entry_price > 0:
                price_change_pct = round((current_price / entry_price - 1) * 100, 2)

            strength_change = None
            if accum_now and accum_at_suggest:
                strength_change = accum_now["score"] - accum_at_suggest["score"]

            level_now = accum_now["level"] if accum_now else None

            # Continuous coil quality — the real ranking metric (volume-add x
            # price-stillness). Replaces the pegged 0-100 gauge for ordering.
            coil = coil_quality(obv90_now, ud90_now, price_change_pct)

            # Traction — leading clues the coil is starting to fire, from the
            # latest scan trace (distance to the 20d pivot, flow turning up,
            # volume expanding, closing strong, recent volume spikes).
            latest_feat = (
                _merge_trace_features(symbol, series[-1]["date"]) if series else {}
            )
            traction = assess_traction(latest_feat)
            # "Volume still adding up" now reads the CONTINUOUS OBV trend, not the
            # saturating gauge: accumulation is building if the 90d OBV slope is
            # positive and has not fallen since we suggested it.
            volume_still_building = bool(
                _is_num(obv90_now) and obv90_now > 0
                and (not _is_num(obv90_start) or obv90_now >= obv90_start - 2.0)
            )

            # Has price delivered the expected move? Prefer the concrete target;
            # fall back to a % move only when no target is on file.
            if _is_num(current_price) and _is_num(expected_price):
                reached_expected = current_price >= expected_price
            elif _is_num(price_change_pct):
                reached_expected = price_change_pct >= FIRING_FALLBACK_PCT
            else:
                reached_expected = None

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
                "volume_still_building": volume_still_building,
                # Continuous coil ranking metric + its two components, plus the
                # raw OBV-90d figures so the UI can show real numbers, not 100s.
                "coil_score": coil["score"],
                "volume_add": coil["volume_add"],
                "price_stillness": coil["price_stillness"],
                "obv90_now": obv90_now,
                "obv90_start": obv90_start,
                "ud90_now": ud90_now,
                "traction": traction,
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

    # Rank by CONTINUOUS coil quality (volume-add x price-stillness), so
    # "volume still adding, price barely moved" floats to the top — the owner's
    # ordering ask. The coarse gauge score is NOT used for ordering (it pegs at
    # 100). Status only breaks ties (coiling above firing/weak).
    _status_rank = {"coiling": 3, "firing": 2, "watch": 1, "weakening": 0, "broke_down": -1, "no-data": -2}

    def _sort_key(row: dict):
        cs = row.get("coil_score")
        return (
            cs if _is_num(cs) else -1.0,
            _status_rank.get(row.get("status"), -3),
            row.get("days_tracked") or 0,
        )

    rows.sort(key=_sort_key, reverse=True)

    # Direct the user to the single best one: the top-ranked row that is a live
    # coil (still accumulating, not yet fired/broken) with a real score.
    for row in rows:
        row["is_top_pick"] = False
    for row in rows:
        if row.get("status") in ("coiling", "watch") and _is_num(row.get("coil_score")):
            row["is_top_pick"] = True
            break

    return rows
