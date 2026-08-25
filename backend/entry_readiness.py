"""Entry-readiness router — "can I enter this pick today?"

Product rule (owner decision, 2026-08-23): the main BUY list must contain ONLY
setups a user can act on *today* — a base still coiling before its move, or a
fresh breakout that hasn't extended. Anything where the timely entry has passed
(late / extended) or should never be entered (distribution) is routed to a
separate "for awareness" section instead of being suggested as a buy.

The decision keys on `entry_timing` (backend/volume_signals.py), which the
engine already computes for every pick:

    early / mid  -> still room to enter  -> MAIN (enter today)
    late         -> extended / chasing   -> AWARE (missed the timely entry)
    missed       -> Stage-4 / distribution -> AWARE (not an entry)
    unknown      -> timing unconfirmed    -> AWARE (not confirmed enterable)

This GENERALIZES and replaces the earlier narrow pre-breakout-late guard: it
catches both late pre-breakouts (e.g. POLYCAB) and already-extended breakouts,
while keeping genuinely enterable fresh breakouts. It never blocks the scan,
changes no gate/composite/rank/score, and only ever *moves a pick out of the
buy list* into `not_actionable` (the human still sees it and can act). Pure and
deterministic; reads only persisted payload fields, so the same logic runs live
and in the offline replay. Reversible via env STOCKYA_ENTERABLE_ONLY=0.
"""
from __future__ import annotations

import os
from typing import Optional

# entry_timing values that mean "still enterable today".
ENTER_TODAY_TIMINGS = frozenset({"early", "mid"})

# --- Stale / perpetual pre-breakout ("recurs but goes nowhere") ---
# A base that keeps getting re-picked over weeks while making no net progress and
# never breaking out is not a fresh entry — it is churn (the "IndusInd keeps
# showing up but never works" case). Detected from `pick_history` (a 30-day trail
# of prior appearances with prices) — pattern-based, NOT a sector list: it fires
# for any repeat-offender, bank or not (ABB was the top recurrer, not a bank).
STALE_MIN_PRIORS = 2        # >= this many prior appearances in the 30d trail (3rd+ time)
STALE_FLAT_PCT = 3.0        # |net price move| across the trail below this = "going nowhere"

# Reason text per non-enterable timing (category, why).
_REASON_BY_TIMING: dict[str, tuple[str, str]] = {
    "late": (
        "late_entry",
        "Late in its base / extended — entering today means chasing, not a "
        "fresh entry.",
    ),
    "missed": (
        "distribution",
        "Stage-4 / distribution signature — not an entry.",
    ),
    "unknown": (
        "timing_unclear",
        "Entry timing could not be classified — not confirmed enterable today.",
    ),
}


def _enabled() -> bool:
    """On by default; STOCKYA_ENTERABLE_ONLY=0 restores the prior behaviour."""
    return os.environ.get("STOCKYA_ENTERABLE_ONLY", "1") != "0"


def main_show_all() -> bool:
    """Show ALL selected picks in the main buy list (owner ask, 2026-08-25)?

    On by default. When on, the router still classifies every pick (early / mid
    stay enterable; late / extended / distribution / stale are tagged), but ALL
    of them are rendered as cards in the main grid — each stamped with a
    ``readiness`` badge instead of being hidden in a below-the-fold section. The
    router's judgment is preserved as a visible tag, never discarded, and
    non-enterable picks are STILL kept out of the portfolio journal upstream.

    STOCKYA_MAIN_SHOW_ALL=0 restores the split view (enterable-only main list +
    a separate awareness section).
    """
    return os.environ.get("STOCKYA_MAIN_SHOW_ALL", "1") != "0"


# Short badge label + tone per readiness category. Tone drives the card colour:
# "enter" (green) = act today, "watch" (amber) = surfaced for awareness,
# "avoid" (rose) = do not enter.
_READINESS_BADGE: dict[str, tuple[str, str]] = {
    "enterable": ("Enter today", "enter"),
    "late_entry": ("Watch · late", "watch"),
    "extended_breakout": ("Watch · extended", "watch"),
    "timing_unclear": ("Watch · timing unclear", "watch"),
    "stale_base": ("Watch · stale base", "watch"),
    "distribution": ("Avoid · distribution", "avoid"),
}


def stamp_readiness(payload: dict) -> dict:
    """Attach a ``readiness`` badge dict onto ``payload`` and return it.

    Pure/deterministic; reads only persisted fields. A pick already routed to the
    awareness bin carries ``payload["not_actionable"]`` (set by split_enterable) —
    we mirror its category/why. Anything else is enterable today.

        readiness = {
          "enterable": bool,
          "category":  str,    # enterable | late_entry | extended_breakout | ...
          "timing":    str|None,  # early / mid / late / missed / unknown
          "label":     str,    # short badge text
          "tone":      str,    # enter | watch | avoid  (drives the card colour)
          "why":       str|None,  # reason (non-enterable only)
        }
    """
    if not isinstance(payload, dict):
        return {}
    na = payload.get("not_actionable")
    if isinstance(na, dict) and na.get("category"):
        category = str(na.get("category"))
        timing = na.get("entry_timing")
        why = na.get("why")
        enterable = False
    else:
        category = "enterable"
        timing = (payload.get("confirmation") or {}).get("entry_timing")
        why = None
        enterable = True
    label, tone = _READINESS_BADGE.get(category, _READINESS_BADGE["timing_unclear"])
    if enterable and timing:
        label = f"Enter today · {timing}"
    readiness = {
        "enterable": enterable,
        "category": category,
        "timing": timing,
        "label": label,
        "tone": tone,
        "why": why,
    }
    payload["readiness"] = readiness
    return readiness


def _stale_base_reason(payload: dict) -> Optional[dict]:
    """Detect a recurring pre-breakout base that is going nowhere.

    Fires when the symbol has appeared >= STALE_MIN_PRIORS times in the 30-day
    `pick_history` trail, is still pre-breakout today (BR gate failing), and has
    made no net price progress across the trail (|move| <= STALE_FLAT_PCT).
    Reads only persisted fields; returns None when the trail is too short, the
    stock already broke out, prices are missing, or it is actually progressing.
    """
    trail = payload.get("pick_history") or []
    if len(trail) < STALE_MIN_PRIORS:
        return None
    gate = payload.get("gate_confirmation_status") or {}
    if "BR" not in (gate.get("failed") or []):
        return None  # it has broken out at some point — not a stale pre-breakout base

    plan = payload.get("price_plan") or {}
    today_price = float(plan.get("entry") or payload.get("current_price") or 0.0)
    oldest = trail[-1]  # trail is newest-first, so the last entry is the oldest
    old_price = float(oldest.get("entry") or 0.0)
    if today_price <= 0.0 or old_price <= 0.0:
        return None
    net_pct = (today_price / old_price - 1.0) * 100.0
    if abs(net_pct) > STALE_FLAT_PCT:
        return None  # genuinely moving (base lifting or breaking down) — not stale

    appearances = len(trail) + 1  # priors + today
    return {
        "category": "stale_base",
        "entry_timing": (payload.get("confirmation") or {}).get("entry_timing"),
        "appearances": appearances,
        "since": oldest.get("date"),
        "net_move_pct": round(net_pct, 1),
        "why": (
            f"Surfaced {appearances}x since {oldest.get('date')} with no net "
            f"progress ({net_pct:+.1f}%) and still no breakout — a base that "
            "isn't resolving, not a fresh entry."
        ),
    }


def entry_readiness(payload: dict) -> Optional[dict]:
    """Return ``None`` if the pick is enterable today (stays in the main list),
    else a reason dict describing why it belongs in the awareness section.
    """
    if not isinstance(payload, dict):
        return None

    # A recurring-but-going-nowhere base is not a fresh entry even if its timing
    # still reads early/mid — check it before the timing verdict.
    stale = _stale_base_reason(payload)
    if stale:
        return stale

    conf = payload.get("confirmation") or {}
    timing = conf.get("entry_timing") or "unknown"
    if timing in ENTER_TODAY_TIMINGS:
        return None  # early / mid -> still room to enter -> MAIN

    gate = payload.get("gate_confirmation_status") or {}
    broke_out = "BR" not in (gate.get("failed") or [])  # BR gate passed
    category, why = _REASON_BY_TIMING.get(timing, _REASON_BY_TIMING["unknown"])
    # A LATE pick that already broke out is specifically an extended breakout —
    # the timely entry is behind us, not ahead.
    if timing == "late" and broke_out:
        category = "extended_breakout"
        why = "Breakout already fired and extended — the timely entry has passed."

    return {
        "category": category,
        "entry_timing": timing,
        "broke_out": broke_out,
        "weinstein_stage": conf.get("weinstein_stage"),
        "why": why,
    }


def split_enterable(visible_picks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split ``visible_picks`` into ``(main, not_actionable)``.

    Attaches a ``not_actionable`` reason onto each moved pick. When disabled it is
    a no-op: every pick stays in ``main`` and ``not_actionable`` is empty.
    """
    if not _enabled():
        return list(visible_picks or []), []
    main: list[dict] = []
    aware: list[dict] = []
    for p in visible_picks or []:
        reason = entry_readiness(p)
        if reason:
            p["not_actionable"] = reason
            aware.append(p)
        else:
            main.append(p)
    return main, aware
