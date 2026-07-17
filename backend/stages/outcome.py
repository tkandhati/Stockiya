"""[O] Outcome Tracker — at T+90 and T+180, did the pick work?

This is the RL reward signal. Without it, every other stage learns nothing.
Run daily; for each open pick whose entry_date + N is today, append a row
to `data/traces/outcomes.jsonl`.

Columns:
  trace_id, symbol, entry_date, entry_price, horizon_days, exit_price,
  return_pct, hit_target, hit_stop, exit_reason

The same file is the dataset for the contextual bandit / offline RL trainer.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"
_TRACES_DIR.mkdir(parents=True, exist_ok=True)
_OUTCOMES_PATH = _TRACES_DIR / "outcomes.jsonl"
_PORTFOLIO_CSV = _PROJECT_ROOT / "data" / "portfolio.csv"

stage_id = "O"

HORIZONS_DAYS = [90, 180]   # snapshots taken at these offsets from entry

# ---- Label schema versioning (2026-07-17) ----------------------------------
# Bumped when the semantics of any label field change. Tuners can filter by
# this to keep training data consistent across label conventions.
#
#   v1  — legacy: `return_pct` = close_at_T+N / entry - 1 (mark-to-market)
#          Rows lacked `is_open`, `realized_return_pct`, `label_schema_version`.
#   v2 (this)  — separates mark-to-market from realized:
#          * `mtm_return_pct` = same math as v1 return_pct.
#          * `return_pct`     = alias to mtm_return_pct (kept for
#                                 backwards-compat with existing readers).
#          * `is_open`        = position still open at snapshot day?
#          * `realized_return_pct` = defined only when is_open=False, from
#                                     portfolio row's exit state.
#          * `exit_reason_final`   = the terminal exit reason if closed.
#
# Extending to a v3 will require lifecycle hit-detection (parked in
# ideas.md) so realized_return_pct reflects the actual ladder P&L instead
# of the snapshot fallback. Until then v2 realized is snapshot-honest.
LABEL_SCHEMA_VERSION = 2

# Portfolio statuses that mean "position is CLOSED, realized P&L is known".
# Rows in _CLOSED_STATUSES contribute a realized_return_pct; others don't.
_CLOSED_STATUSES: frozenset[str] = frozenset({
    "stopped", "target_hit", "timed_out", "closed",
    "exit_stop", "exit_t2", "exit_t1_full", "exit_end_date",
    "exit_final", "exit_time_stop", "exit_distribution",
})


def _read_portfolio() -> list[dict]:
    if not _PORTFOLIO_CSV.exists():
        return []
    with _PORTFOLIO_CSV.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _already_logged(trace_id: str, horizon_days: int) -> bool:
    if not _OUTCOMES_PATH.exists():
        return False
    with _OUTCOMES_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("trace_id") == trace_id and row.get("horizon_days") == horizon_days:
                return True
    return False


def _append_outcome(row: dict) -> None:
    with _OUTCOMES_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # Sliding-window learning trigger (2026-07-17) — event-driven per-stage
    # IC diagnostic on the last 5 T+90 outcomes. Diagnostic-only; the real
    # champion-challenger tuner (scripts/tune_weights.py) still owns weight
    # changes and still requires >= MIN_OUTCOMES_TO_TUNE=20. Guarded so any
    # bug here cannot break outcome logging — that pipeline must remain
    # reliable regardless.
    try:
        from ..sliding_window_learn import maybe_fire_event
        maybe_fire_event(new_row=row)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("outcome").exception(
            "sliding-window learning trigger failed; outcome logged normally"
        )


def run_outcome_tracker(
    fetch_close: Callable[[str], Optional[float]],
    today: Optional[date] = None,
) -> dict:
    """Walk portfolio.csv; for each pick whose entry_date + 90 (or 180) is
    today, fetch today's close and append an outcome row.

    `fetch_close(symbol) -> float | None` is caller-supplied so we don't lock
    this stage to yfinance.
    """
    today = today or datetime.now(IST).date()
    rows = _read_portfolio()
    summary = {"checked": 0, "appended": 0, "skipped_already_logged": 0, "no_price": 0}

    for r in rows:
        # Skip picks the user explicitly declined — they never entered a
        # real (or paper) position, so their realized-return is noise
        # against the tuner's target of "did the scanner's judgment work?"
        if r.get("ownership") == "declined":
            continue

        entry_iso = r.get("entry_date")
        if not entry_iso:
            continue
        try:
            entry_d = date.fromisoformat(entry_iso)
        except ValueError:
            continue

        for horizon in HORIZONS_DAYS:
            target_d = entry_d + timedelta(days=horizon)
            if target_d != today:
                continue

            summary["checked"] += 1
            # Prefer the pipeline's UUID trace_id so this row joins to the
            # per-stage trace JSONL (data/traces/run_<date>_<ticker>.jsonl).
            # Fall back to pick_id for older rows that pre-date the field.
            trace_id = r.get("trace_id") or r.get("pick_id", "")
            if _already_logged(trace_id, horizon):
                summary["skipped_already_logged"] += 1
                continue

            close = fetch_close(r["symbol"])
            if close is None:
                summary["no_price"] += 1
                continue

            entry_px = float(r["entry_price"])
            target_px = float(r["target_price"])
            stop_px = float(r["stop_price"])
            t1_px = float(r.get("t1_price") or 0)
            t2_px = float(r.get("t2_price") or target_px)
            mtm_ret = (close / entry_px - 1) * 100
            hit_t1 = bool(t1_px and close >= t1_px) or (r.get("hit_t1") == "true")
            hit_t2 = close >= t2_px
            hit_target = close >= target_px
            hit_stop = close <= stop_px

            # ---- Label v2: separate mark-to-market from realized ----
            # `is_open` = portfolio row's status still indicates an active
            # position at snapshot day. If closed, populate realized from the
            # portfolio row (authoritative — the enforcer wrote it there).
            # If open, realized is None: the tuner treats it as "still
            # accumulating." This is what unblocks the extension-friendly
            # semantics without inventing labels.
            row_status = (r.get("status") or "open").lower()
            is_closed = row_status in _CLOSED_STATUSES
            exit_reason_final = None
            realized_return_pct = None
            if is_closed:
                # Prefer the exit_price on the portfolio row if the enforcer
                # recorded it; fall back to snapshot close otherwise. Both
                # are honest for a snapshot-based v2 — a lifecycle-accurate
                # ladder P&L is parked (fix #4 in the audit).
                exit_px = float(r.get("exit_price") or close)
                realized_return_pct = round((exit_px / entry_px - 1) * 100, 2)
                exit_reason_final = r.get("exit_reason") or None

            _append_outcome({
                "ts": datetime.now(IST).isoformat(timespec="seconds"),
                "label_schema_version": LABEL_SCHEMA_VERSION,
                "trace_id": trace_id,
                "pick_id": r.get("pick_id", ""),
                "symbol": r["symbol"],
                "entry_date": entry_iso,
                "entry_price": entry_px,
                "horizon_days": horizon,
                "exit_price": round(close, 2),
                # ---- v2 dual-label ----
                "mtm_return_pct": round(mtm_ret, 2),
                "return_pct": round(mtm_ret, 2),          # v1 alias — legacy readers
                "is_open": not is_closed,
                "realized_return_pct": realized_return_pct,  # None if is_open
                "exit_reason_final": exit_reason_final,      # None if is_open
                # ---- Hit flags (snapshot; lifecycle-accurate version parked) ----
                "t1_price": t1_px,
                "t2_price": t2_px,
                "stop_price": stop_px,
                "hit_t1": hit_t1,
                "hit_t2": hit_t2,
                "hit_target": hit_target,
                "hit_stop": hit_stop,
                "exit_reason": (
                    "target" if hit_target else
                    "stop" if hit_stop else
                    ("t1" if hit_t1 else "neither")
                ),
                "confirmation_score": float(r.get("confirmation_score") or 0),
                "shares_total": int(r.get("shares_total") or 0),
            })
            summary["appended"] += 1

    return summary
