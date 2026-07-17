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

from .signal_trajectory import compute_trajectory

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

# Expected days from entry to T1 for institutional breakouts that go on to
# work. 21 trading days (~3 weeks) is the classical CAN SLIM heuristic for
# winners. If T1 isn't hit by then, the setup is at least "slow" -- worth
# re-checking the signal trajectory.
EXPECTED_T1_TRADING_DAYS: int = 21


def _add_trading_days(start: date, n: int) -> date:
    """Add n weekdays (Mon-Fri) to start. NSE holidays are not modelled --
    this is a UI-side checkpoint, not a settlement date."""
    cur = start
    added = 0
    while added < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def _trading_days_between(a: date, b: date) -> int:
    """Count weekdays in (a, b]. Negative if b < a."""
    if b < a:
        return -_trading_days_between(b, a)
    cur = a
    n = 0
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


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
    trajectory_flip: bool = False,
    end_date_reached: bool = False,
    horizon_days: int = 0,
    horizon_extension: Optional[int] = None,
) -> tuple[str, str, Optional[float]]:
    """Decide today's action + note + (optional new_stop).

    Action priority (2026-07-17 correction):

        1. Data safety     — close unavailable → hold (retain prior state)
        2. Stop hit        — hardest exit; nothing overrides
        3. T2 hit          — take profit (full or trailing setup)
        4. T1 hit          — take profit (partial 50%), raise stop to BE
        5. Distribution    — trajectory flip (still treated hard; hysteresis
                              two-session confirmation parked in ideas.md —
                              revisit once portfolio.csv persists warning
                              count)
        6. Hard time-stop  — days_held >= DAY_180 unconditional cap
        7. Time-stop pre-T1 — days_held >= DAY_90 and T1 never hit
        8. End-date review — dynamic horizon reached; extend or exit
        9. Day-45 tighten  — risk mgmt on positions pre-T1
       10. Hold            — the default maintain state

    Pre-fix (through 2026-07-16), distribution and DAY_180 checks ran
    BEFORE price-driven checks. That meant a position that hit its stop
    on day 180 got labeled `exit_final`, and a position that hit T2 on
    a day the trajectory happened to flip got labeled `exit_distribution`.
    Both are wrong for the tuner: it needs to see the HARDEST reason
    that fired, not the first one in file order. Correcting the order
    fixes label quality across the entire outcome log going forward.

    end_date_reached means today >= the volume-based dynamic end_date.
    horizon_extension (int days) is set when trajectory is healthy and the
    horizon estimator recommends extending — used to phrase the note.
    """
    # 1. Data safety — never invent an action when the tape is missing.
    #    Anti-flip rules (parked): should return the prior confirmed state,
    #    not "hold". For now "hold" is the safe default.
    if close is None:
        return ("hold", "Price unavailable today — hold and recheck.", None)

    # 2. Stop hit — hardest exit.
    if close <= stop:
        return (
            "exit_stop",
            f"Stop hit (close {close:.2f} <= stop {stop:.2f}). Exit at next open.",
            None,
        )

    # 3. T2 hit — take profit (remaining shares).
    if t2 > 0 and close >= t2:
        return (
            "exit_t2",
            f"T2 hit (close {close:.2f} >= T2 {t2:.2f}). "
            f"Sell remaining {shares_at_t2} shares.",
            None,
        )

    # 4. T1 hit — take partial profit + raise stop to BE.
    if t1 > 0 and close >= t1 and not hit_t1:
        return (
            "exit_t1",
            f"T1 hit (close {close:.2f} >= T1 {t1:.2f}). "
            f"Sell {shares_at_t1} shares; raise stop to entry {entry:.2f} on the rest.",
            entry,
        )

    # 5. Confirmed distribution — trajectory flip. Anti-flip hysteresis
    #    (require two-session confirmation) is parked in ideas.md; today
    #    a single flip still fires. The stop check above already caught
    #    the case where price is already through — that guard is redundant
    #    now but preserved for defence in depth.
    if trajectory_flip and close > stop:
        return (
            "exit_distribution",
            "Signal trajectory flipped — an institutional indicator has "
            "turned negative since entry. Exit at next open before price "
            "catches up.",
            None,
        )

    # 6. Hard time-stop at DAY_180 — capital cap. Only fires if no
    #    price-driven or distribution exit already fired above.
    if days_held >= DAY_180:
        return (
            "exit_final",
            f"Day {days_held} (>= {DAY_180}). Unconditional final exit.",
            None,
        )

    # 7. Pre-T1 time-stop at DAY_90.
    if days_held >= DAY_90 and not hit_t1:
        return (
            "exit_time_stop",
            f"Day {days_held} (>= {DAY_90}) and T1 never hit. "
            "Exit at market — capital frozen in a non-moving trade.",
            None,
        )

    # 8. Dynamic end-date reached — review, don't panic-exit.
    if end_date_reached:
        extend_valid = (
            horizon_extension is not None
            and horizon_extension > horizon_days
            and horizon_extension <= DAY_180
        )
        if extend_valid:
            return (
                "extend_horizon",
                f"End of {horizon_days}d volume horizon reached, but "
                f"trajectory still healthy. Recommend extending to "
                f"{horizon_extension}d.",
                None,
            )
        return (
            "exit_end_date",
            f"End of {horizon_days}d volume horizon reached; no valid "
            "extension available. Exit at next open.",
            None,
        )

    # 9. Day-45 stop-tighten (only if still holding pre-T1).
    if DAY_45 <= days_held < DAY_90 and not hit_t1:
        new_stop = entry * (1 - DAY_45_TIGHTEN_PCT)
        return (
            "tighten_stop_45",
            f"Day {days_held} (>= {DAY_45}) and T1 not hit. "
            f"Tighten stop to {new_stop:.2f} (entry -{int(DAY_45_TIGHTEN_PCT*100)}%).",
            new_stop,
        )

    # 10. Default — maintain.
    return ("hold", "Hold normally.", None)


def _symbols_in_todays_picks(today_iso: str) -> set[str]:
    """Return the set of symbols in `data/picks_<today>.json`, or empty
    if the file doesn't exist or can't be parsed.

    Used to hide open-*suggested* portfolio rows for symbols that are
    being freshly picked today: those rows are about to be (or have
    already been) superseded by portfolio.record_picks, and displaying
    their stale exit signal alongside a fresh buy recommendation is
    the trust-breaking contradiction we're closing.

    Taken (paper/live) rows are NEVER hidden by this filter — the user's
    real capital always shows, and picks_reconcile handles the
    contradiction on the picks side.
    """
    import json
    p = _PROJECT_ROOT / "data" / f"picks_{today_iso}.json"
    if not p.exists():
        return set()
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        pick.get("symbol", "")
        for pick in (payload.get("picks") or [])
        if pick.get("symbol")
    }


def list_active_positions(
    fetch_close: Callable[[str], Optional[float]],
    today: Optional[date] = None,
) -> list[dict]:
    """Return enriched position dicts ready for the UI."""
    today = today or _ist_today()
    rows = _read_portfolio()
    out: list[dict] = []

    # Defensive filter: hide open-suggested rows whose symbol also
    # appears in today's picks. They're either already superseded (with
    # a fresh row present) or about to be superseded on the next
    # record_picks call; either way, showing their stale exit signal
    # alongside a fresh buy recommendation is misleading.
    picks_today_symbols = _symbols_in_todays_picks(today.isoformat())

    for r in rows:
        if r.get("status") not in ("open", "partial_t1"):
            continue
        # Skip picks the user explicitly declined.
        ownership = (r.get("ownership") or "suggested").strip() or "suggested"
        if ownership == "declined":
            continue

        sym = r["symbol"]

        # Trust-safety filter: same symbol in today's picks AND this row
        # is still ownership=suggested means we're mid-transition. Hide it.
        if (
            ownership == "suggested"
            and sym in picks_today_symbols
            # Only hide if this row is older than today; a same-day fresh
            # row must be visible so the position shows up in the UI.
            and r.get("entry_date", "") != today.isoformat()
        ):
            continue
        try:
            close = fetch_close(sym)
        except Exception:
            close = None

        # Scanner's assumed entry (always populated by record_picks).
        try:
            scanner_entry_d = date.fromisoformat(r["entry_date"])
        except (KeyError, ValueError):
            continue
        scanner_entry_px = float(r.get("entry_price") or 0)
        scanner_shares = int(r.get("shares_total") or 0)

        # User's actual fill (blank / 0 => fall back to scanner's numbers).
        user_entry_iso = (r.get("user_entry_date") or "").strip()
        try:
            user_entry_px = float(r.get("user_entry_price") or 0)
        except (TypeError, ValueError):
            user_entry_px = 0.0
        try:
            user_shares_val = int(float(r.get("user_shares") or 0))
        except (TypeError, ValueError):
            user_shares_val = 0

        entry_d = scanner_entry_d
        if user_entry_iso:
            try:
                entry_d = date.fromisoformat(user_entry_iso)
            except ValueError:
                entry_d = scanner_entry_d

        entry = user_entry_px if user_entry_px > 0 else scanner_entry_px
        shares_total = user_shares_val if user_shares_val > 0 else scanner_shares

        # Stop / T1 / T2 stay at the scanner's absolute price levels
        # (they're targets on the tape, not offsets from the fill).
        days_held = (today - entry_d).days
        stop = float(r.get("stop_price") or 0)
        t1 = float(r.get("t1_price") or 0)
        t2 = float(r.get("t2_price") or r.get("target_price") or 0)
        hit_t1 = r.get("hit_t1") == "true"
        shares_at_t1 = int(r.get("shares_at_t1") or 0)
        shares_at_t2 = int(r.get("shares_at_t2") or 0)

        # ---- Signal trajectory (Q2) ----
        # Compare entry-time institutional indicators to today's values; if
        # any flipped, escalate the action to exit. Trajectory anchors on
        # the scanner's entry date (that's when the setup was scored); user's
        # actual fill day doesn't change the setup's baseline institutional
        # signals. `trading_days_since_entry` powers the windowed rules —
        # healing-velocity override (10d grace) and failed-breakout micro-stop
        # (5d arming window). Uses scanner_entry_d so the window boundaries
        # are consistent with the trace-time indicator values.
        trading_days_since_entry = max(
            0, _trading_days_between(scanner_entry_d, today)
        )
        try:
            traj = compute_trajectory(
                sym, r["entry_date"],
                trading_days_since_entry=trading_days_since_entry,
            )
        except Exception:
            traj = None
        trajectory_flip = bool(traj and traj.exit_recommendation)

        # ---- Volume-based dynamic end date + revalidation ----
        # Continuous monitoring rule: for TAKEN positions (paper/live),
        # the effective end_date shifts to the user's entry_date +
        # horizon_days. The user's real capital was deployed on their
        # fill day, so the horizon clock starts from their fill, not
        # from the scanner's original scoring day.
        #
        # For suggested rows (never taken), entry_d == scanner_entry_d,
        # so the shift is a no-op and the stored end_date is used.
        end_date_reached = False
        horizon_extension: Optional[int] = None
        try:
            horizon_days = int(r.get("horizon_days") or 0)
        except (TypeError, ValueError):
            horizon_days = 0
        stored_end_date_iso = (r.get("end_date") or "").strip()
        effective_end_date_iso = stored_end_date_iso
        if horizon_days > 0:
            # Recompute end_date from the effective entry_d so it tracks
            # the user's fill for taken positions.
            effective_end_d = entry_d + timedelta(days=horizon_days)
            effective_end_date_iso = effective_end_d.isoformat()
            if today >= effective_end_d:
                end_date_reached = True
                trajectory_healthy = not trajectory_flip
                try:
                    from .horizon import revalidated_horizon_days
                    new_days, _basis = revalidated_horizon_days(
                        horizon_days, trajectory_healthy,
                    )
                except Exception:
                    new_days = None
                horizon_extension = new_days if new_days else None

        action, action_note, new_stop = _action_for(
            close=close, entry=entry, stop=stop, t1=t1, t2=t2,
            hit_t1=hit_t1, days_held=days_held,
            shares_at_t1=shares_at_t1, shares_at_t2=shares_at_t2,
            trajectory_flip=trajectory_flip,
            end_date_reached=end_date_reached,
            horizon_days=horizon_days,
            horizon_extension=horizon_extension,
        )

        # 9-state action-ladder label (2026-07-17). Advisory only — the
        # raw `action` still drives every enforcement decision. Soft-state
        # inputs (soft_signal_count, is_dry_up) are zero today; two-session
        # hysteresis with persisted warning count is parked in ideas.md.
        try:
            from .action_labels import action_label as _label_fn
            action_label_str = _label_fn(action, close_available=(close is not None))
        except Exception:
            action_label_str = "MAINTAIN_HEALTHY"

        pnl_pct = ((close / entry - 1) * 100) if (close is not None and entry > 0) else None

        # ---- Expected T1 day (Q1) ----
        expected_t1_date = _add_trading_days(entry_d, EXPECTED_T1_TRADING_DAYS)
        days_to_expected_t1 = _trading_days_between(today, expected_t1_date)
        if hit_t1:
            t1_status = "hit"
        elif days_to_expected_t1 < 0:
            t1_status = "overdue"
        else:
            t1_status = "on_track"

        out.append({
            "pick_id": r["pick_id"],
            "trace_id": r.get("trace_id", ""),
            "symbol": sym,
            "company": r.get("company") or sym,
            "entry_date": entry_d.isoformat(),
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
            "shares_total": shares_total,
            "shares_at_t1": shares_at_t1,
            "shares_at_t2": shares_at_t2,
            "confirmation_score": float(r.get("confirmation_score") or 0),
            "headline": r.get("headline", ""),
            "action": action,
            "action_label": action_label_str,  # 9-state ladder — advisory
            "action_note": action_note,
            "new_stop": new_stop,
            "time_stops": {
                "day_45": (entry_d + timedelta(days=DAY_45)).isoformat(),
                "day_90": (entry_d + timedelta(days=DAY_90)).isoformat(),
                "day_180": (entry_d + timedelta(days=DAY_180)).isoformat(),
            },
            # ---- Volume-based dynamic horizon (new) ----
            # `end_date` is the EFFECTIVE end date (recomputed from the
            # user's fill for taken positions). `stored_end_date` is the
            # value on the CSV row (scanner-entry anchored) — kept so
            # UI can show both when they differ.
            "end_date": effective_end_date_iso,
            "stored_end_date": stored_end_date_iso,
            "horizon_days": horizon_days,
            "horizon_source": r.get("horizon_source", ""),
            "horizon_basis": r.get("horizon_basis", ""),
            "end_date_reached": end_date_reached,
            "horizon_extension_days": horizon_extension,
            # ---- Q1 expected-T1 fields ----
            "expected_t1_date": expected_t1_date.isoformat(),
            "expected_t1_trading_days": EXPECTED_T1_TRADING_DAYS,
            "t1_status": t1_status,                  # 'on_track' | 'overdue' | 'hit'
            "days_to_expected_t1": days_to_expected_t1,
            # ---- Q2 trajectory fields ----
            "trajectory": traj.as_dict() if traj else None,
            # ---- Ownership + user-fill (V1) ----
            "ownership": ownership,
            "scanner_entry_date": scanner_entry_d.isoformat(),
            "scanner_entry_price": scanner_entry_px,
            "scanner_shares": scanner_shares,
            "user_entry_date": user_entry_iso,
            "user_entry_price": user_entry_px if user_entry_px > 0 else None,
            "user_shares": user_shares_val if user_shares_val > 0 else None,
            "user_notes": r.get("user_notes") or "",
        })

    # Sort: action urgency first (exits before holds), then by days_held desc
    urgency = {
        "exit_stop": 0, "exit_distribution": 1, "exit_final": 2,
        "exit_time_stop": 3, "exit_end_date": 4,
        "exit_t2": 5, "exit_t1": 6,
        "extend_horizon": 7, "tighten_stop_45": 8, "hold": 9,
    }
    out.sort(key=lambda x: (urgency.get(x["action"], 99), -x["days_held"]))
    return out
