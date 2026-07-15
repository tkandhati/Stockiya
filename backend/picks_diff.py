"""Attach a `change_since_prev_pick` diff to each of today's picks.

When the pipeline re-fires a symbol on a later day, the user wants to see
what changed vs the previous appearance. This module compares today's
pick payload against the most recent prior appearance of the same symbol
in `data/picks_<date>.json` and returns a compact delta.

Deterministic: reads only on-disk files. No network. Never mutates prior
files. Fail-safe: if any single pick's diff fails, that pick is left
without a `change_since_prev_pick` field and the others still get theirs.

Fix points:
    PICK_DIFF_LOOKBACK_DAYS : how many calendar days back to scan for a
                              prior pick. Default 30 (~6 trading weeks).
    PICK_DIFF_SCORE_ROUND   : decimal places for score deltas.
    PICK_DIFF_PRICE_ROUND   : decimal places for price deltas.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"

log = logging.getLogger("picks_diff")

PICK_DIFF_LOOKBACK_DAYS: int = 30
PICK_DIFF_SCORE_ROUND: int = 3
PICK_DIFF_PRICE_ROUND: int = 2


def _load_picks_for_date(iso: str) -> Optional[dict]:
    p = _DATA_DIR / f"picks_{iso}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read %s: %s", p.name, e)
        return None


def _find_previous_pick(
    symbol: str, today_iso: str, lookback_days: int,
) -> tuple[Optional[dict], Optional[str]]:
    """Return (prev_pick_dict, prev_date_iso) or (None, None) if no prior
    pick for `symbol` exists within `lookback_days` of `today_iso`."""
    try:
        today = date.fromisoformat(today_iso)
    except ValueError:
        return None, None

    for delta in range(1, lookback_days + 1):
        d_iso = (today - timedelta(days=delta)).isoformat()
        payload = _load_picks_for_date(d_iso)
        if payload is None:
            continue
        for p in payload.get("picks") or []:
            if p.get("symbol") == symbol:
                return p, d_iso
    return None, None


def _score_delta(now_val: Any, prev_val: Any) -> Optional[dict]:
    try:
        n = float(now_val)
        p = float(prev_val)
    except (TypeError, ValueError):
        return None
    r = PICK_DIFF_SCORE_ROUND
    return {"was": round(p, r), "now": round(n, r), "delta": round(n - p, r)}


def _price_delta(now_val: Any, prev_val: Any) -> Optional[dict]:
    try:
        n = float(now_val)
        p = float(prev_val)
    except (TypeError, ValueError):
        return None
    r = PICK_DIFF_PRICE_ROUND
    delta_pct = ((n / p - 1.0) * 100.0) if p != 0.0 else None
    return {
        "was": round(p, r),
        "now": round(n, r),
        "delta": round(n - p, r),
        "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
    }


def _string_change(now_val: Any, prev_val: Any) -> Optional[dict]:
    if (now_val or "") == (prev_val or ""):
        return None
    return {"was": prev_val, "now": now_val}


def _bonus_diff(now_pick: dict, prev_pick: dict) -> Optional[dict]:
    now_bonuses = set(
        (now_pick.get("confirmation") or {}).get("bonuses_fired") or []
    )
    prev_bonuses = set(
        (prev_pick.get("confirmation") or {}).get("bonuses_fired") or []
    )
    added = sorted(now_bonuses - prev_bonuses)
    removed = sorted(prev_bonuses - now_bonuses)
    if not added and not removed:
        return None
    return {"added": added, "removed": removed}


def compute_pick_diff(
    today_pick: dict,
    today_iso: str,
    lookback_days: int = PICK_DIFF_LOOKBACK_DAYS,
) -> Optional[dict]:
    """Return a delta struct vs. the last time this symbol was picked, or
    None if no prior pick within `lookback_days`.

    Emits keys only when something actually changed, except `prev_date`
    and `days_ago` which are always present when a prior pick was found.
    That lets the UI show "re-fired unchanged" as a distinct state.
    """
    sym = today_pick.get("symbol")
    if not sym:
        return None

    prev_pick, prev_date = _find_previous_pick(sym, today_iso, lookback_days)
    if prev_pick is None or prev_date is None:
        return None

    try:
        today_d = date.fromisoformat(today_iso)
        prev_d = date.fromisoformat(prev_date)
        days_ago: Optional[int] = (today_d - prev_d).days
    except ValueError:
        days_ago = None

    diff: dict = {"prev_date": prev_date, "days_ago": days_ago}

    conf_now = today_pick.get("confirmation") or {}
    conf_prev = prev_pick.get("confirmation") or {}
    sd = _score_delta(conf_now.get("score"), conf_prev.get("score"))
    if sd is not None:
        diff["confirmation_score"] = sd

    bd = _bonus_diff(today_pick, prev_pick)
    if bd is not None:
        diff["bonuses"] = bd

    for field_name in ("entry_timing", "weinstein_stage"):
        sc = _string_change(today_pick.get(field_name), prev_pick.get(field_name))
        if sc is not None:
            diff[field_name] = sc

    if _string_change(today_pick.get("headline"), prev_pick.get("headline")) is not None:
        diff["headline_changed"] = True

    plan_now = today_pick.get("price_plan") or {}
    plan_prev = prev_pick.get("price_plan") or {}
    plan_delta: dict = {}
    for key in ("entry", "stop", "t1", "t2"):
        pd_ = _price_delta(plan_now.get(key), plan_prev.get(key))
        if pd_ is not None and pd_["delta"] != 0:
            plan_delta[key] = pd_
    if plan_delta:
        diff["price_plan_delta"] = plan_delta

    r_now = today_pick.get("rank")
    r_prev = prev_pick.get("rank")
    if r_now is not None and r_prev is not None:
        try:
            diff["rank_change"] = {
                "was": int(r_prev),
                "now": int(r_now),
                "delta": int(r_now) - int(r_prev),
            }
        except (TypeError, ValueError):
            pass

    return diff


def attach_change_diffs(pick_payloads: list[dict], today_iso: str) -> None:
    """Mutate each pick in place, attaching `change_since_prev_pick` where
    a prior pick exists within lookback. Safe on an empty list."""
    for p in pick_payloads:
        try:
            diff = compute_pick_diff(p, today_iso)
        except Exception:
            log.exception("pick diff failed for %s", p.get("symbol"))
            diff = None
        if diff is not None:
            p["change_since_prev_pick"] = diff
