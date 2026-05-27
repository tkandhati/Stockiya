"""Active-positions view — what the user currently holds and what to do.

Reads `data/portfolio.csv` (rows with status in {open, partial_t1}),
enriches each with the latest close, and computes today's recommended
action based on the T1/T2 ladder + 45/90/180-day time stops from
PRINCIPLES Section 4.

Pure-ish: takes a `fetch_close` callable so it's reusable from tests.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PORTFOLIO_CSV = _PROJECT_ROOT / "data" / "portfolio.csv"


# --------------------------------------------------------------------------- #
# Tunables — kept consistent with hypothesis stage milestones
# --------------------------------------------------------------------------- #

DAY_45_TIGHTEN_PCT: float = 0.04   # entry - 4 % on day 45 if T1 not hit
DAY_45: int = 45
DAY_90: int = 90
DAY_180: int = 180


def _ist_today() -> date:
    return datetime.now(IST).date()


def _read_portfolio() -> list[dict]:
    if not _PORTFOLIO_CSV.exists():
        return []
    with _PORTFOLIO_CSV.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _action_for(
    *, close: Optional[float], entry: float, stop: float, t1: float, t2: float,
    hit_t1: bool, days_held: int, shares_at_t1: int, shares_at_t2: int,
) -> tuple[str, str, Optional[float]]:
    """Decide today's action + note + (optional new_stop)."""
    # Time-stop overrides (most urgent first)
    if days_held >= DAY_180:
        return (
            "exit_final",
            f"Day {days_held} (>= {DAY_180}). Unconditional final exit.",
            None,
        )
    if days_held >= DAY_90 and not hit_t1:
        return (
            "exit_time_stop",
            f"Day {days_held} (>= {DAY_90}) and T1 never hit. "
            "Exit at market — capital frozen in a non-moving trade.",
            None,
        )

    # Price-driven exits
    if close is None:
        return ("hold", "Price unavailable today — hold and recheck.", None)

    if close <= stop:
        return (
            "exit_stop",
            f"Stop hit (close {close:.2f} <= stop {stop:.2f}). Exit at next open.",
            None,
        )
    if t2 > 0 and close >= t2:
        return (
            "exit_t2",
            f"T2 hit (close {close:.2f} >= T2 {t2:.2f}). "
            f"Sell remaining {shares_at_t2} shares.",
            None,
        )
    if t1 > 0 and close >= t1 and not hit_t1:
        return (
            "exit_t1",
            f"T1 hit (close {close:.2f} >= T1 {t1:.2f}). "
            f"Sell {shares_at_t1} shares; raise stop to entry {entry:.2f} on the rest.",
            entry,
        )

    # Day-45 stop-tighten (only if still holding pre-T1)
    if DAY_45 <= days_held < DAY_90 and not hit_t1:
        new_stop = entry * (1 - DAY_45_TIGHTEN_PCT)
        return (
            "tighten_stop_45",
            f"Day {days_held} (>= {DAY_45}) and T1 not hit. "
            f"Tighten stop to {new_stop:.2f} (entry -{int(DAY_45_TIGHTEN_PCT*100)}%).",
            new_stop,
        )

    return ("hold", "Hold normally.", None)


def list_active_positions(
    fetch_close: Callable[[str], Optional[float]],
    today: Optional[date] = None,
) -> list[dict]:
    """Return enriched position dicts ready for the UI."""
    today = today or _ist_today()
    rows = _read_portfolio()
    out: list[dict] = []

    for r in rows:
        if r.get("status") not in ("open", "partial_t1"):
            continue

        sym = r["symbol"]
        try:
            close = fetch_close(sym)
        except Exception:
            close = None

        try:
            entry_d = date.fromisoformat(r["entry_date"])
        except (KeyError, ValueError):
            continue

        days_held = (today - entry_d).days
        entry = float(r.get("entry_price") or 0)
        stop = float(r.get("stop_price") or 0)
        t1 = float(r.get("t1_price") or 0)
        t2 = float(r.get("t2_price") or r.get("target_price") or 0)
        hit_t1 = r.get("hit_t1") == "true"
        shares_at_t1 = int(r.get("shares_at_t1") or 0)
        shares_at_t2 = int(r.get("shares_at_t2") or 0)

        action, action_note, new_stop = _action_for(
            close=close, entry=entry, stop=stop, t1=t1, t2=t2,
            hit_t1=hit_t1, days_held=days_held,
            shares_at_t1=shares_at_t1, shares_at_t2=shares_at_t2,
        )

        pnl_pct = ((close / entry - 1) * 100) if (close is not None and entry > 0) else None

        out.append({
            "pick_id": r["pick_id"],
            "trace_id": r.get("trace_id", ""),
            "symbol": sym,
            "company": r.get("company") or sym,
            "entry_date": r["entry_date"],
            "days_held": days_held,
            "entry_price": entry,
            "stop_price": stop,
            "t1_price": t1,
            "t2_price": t2,
            "current_price": close,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "status": r.get("status", "open"),
            "hit_t1": hit_t1,
            "hit_t1_date": r.get("hit_t1_date", ""),
            "shares_total": int(r.get("shares_total") or 0),
            "shares_at_t1": shares_at_t1,
            "shares_at_t2": shares_at_t2,
            "confirmation_score": float(r.get("confirmation_score") or 0),
            "headline": r.get("headline", ""),
            "action": action,
            "action_note": action_note,
            "new_stop": new_stop,
            "time_stops": {
                "day_45": (entry_d + timedelta(days=DAY_45)).isoformat(),
                "day_90": (entry_d + timedelta(days=DAY_90)).isoformat(),
                "day_180": (entry_d + timedelta(days=DAY_180)).isoformat(),
            },
        })

    # Sort: action urgency first (exits before holds), then by days_held desc
    urgency = {
        "exit_stop": 0, "exit_final": 1, "exit_time_stop": 2,
        "exit_t2": 3, "exit_t1": 4, "tighten_stop_45": 5, "hold": 6,
    }
    out.sort(key=lambda x: (urgency.get(x["action"], 99), -x["days_held"]))
    return out
