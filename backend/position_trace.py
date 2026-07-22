"""Daily position-monitoring trace — the durable audit record of what the
open-position monitor saw, and what it altered, on each trading day.

WHY THIS EXISTS
---------------
`positions_view.list_active_positions` recomputes, on every load, the latest
volume-strength trajectory for each held position plus the day's recommended
action. That includes the two — and only two — alterations the strategy is
allowed to make to a live position:

    * STOP raise      — to break-even after T1, or entry-4% at the day-45
                        checkpoint (raised only, never lowered; PRINCIPLES §8).
    * HORIZON extend  — bump the volume-based holding horizon to the next
                        bucket when the trajectory is still healthy at the
                        end_date (horizon.revalidated_horizon_days).

The T1 / T2 target PRICES are never moved. But all of the above was ephemeral:
computed for the UI and thrown away. There was no per-day record a user (or the
tuner) could inspect to answer "what did the monitor say about my position on
day D, and did it move a target — and why?".

This module writes that record. It is PURELY ADDITIVE:
    * It changes no selection, sizing, or exit DECISION.
    * It only records what `positions_view` already computed.
    * The stop-raise / horizon-extend it logs are the SAME recommendations the
      view already surfaces; persisting them here does NOT commit them to
      portfolio.csv — that stays `portfolio.update_open_picks`' job.

DESIGN
------
One append-only, schema-versioned JSONL file per trading day under
`data/position_traces/`. Re-running a day truncates and rewrites that day's
file (no duplicate rows); row content is deterministic given the position
inputs, modulo the wall-clock `ts` field (same convention as pipeline traces).
Written atomically (tmp + replace) so a crash mid-write leaves the prior file
intact. Every field is optional and consumers `.get()` with defaults, so old
rows survive schema growth (data-survives-code-changes).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "position_traces"

# Bump when the row shape changes in a way a reader must branch on.
SCHEMA_VERSION = 1

# Below this absolute-price delta a "new stop" is treated as unchanged, so a
# floating-point round-trip on a break-even raise doesn't log a phantom move.
_STOP_EPS = 1e-9


def _trace_path(today_iso: str) -> Path:
    return _TRACES_DIR / f"pos_{today_iso}.jsonl"


def _alterations_for(pos: dict) -> list[dict]:
    """Extract the day's target alterations from an enriched position dict.

    Returns a list of {target, kind, from, to, reason} records — one per
    alteration the monitor is recommending today. Empty when nothing moved
    (the common case: targets held, action == 'hold'). Pure; no I/O.
    """
    out: list[dict] = []
    action = pos.get("action") or ""
    note = pos.get("action_note") or ""

    # (1) STOP raise / tighten — the risk-side price target moves (up only).
    new_stop = pos.get("new_stop")
    cur_stop = pos.get("stop_price")
    if new_stop is not None:
        try:
            changed = cur_stop is None or abs(float(new_stop) - float(cur_stop)) > _STOP_EPS
        except (TypeError, ValueError):
            changed = True
        if changed:
            if action == "exit_t1":
                kind = "stop_raise_breakeven"
            elif action == "tighten_stop_45":
                kind = "stop_tighten_day45"
            else:
                kind = "stop_change"
            out.append({
                "target": "stop_price",
                "kind": kind,
                "from": cur_stop,
                "to": new_stop,
                "reason": note,
            })

    # (2) HORIZON extend — the time target is pushed out to the next bucket
    #     because the trajectory was still healthy at the end_date.
    ext = pos.get("horizon_extension_days")
    if action == "extend_horizon" and ext:
        out.append({
            "target": "horizon_days",
            "kind": "horizon_extend",
            "from": pos.get("horizon_days"),
            "to": ext,
            "reason": note,
        })

    # (3) HORIZON re-anchor — effective end_date differs from the stored one
    #     because the clock re-anchored to the user's actual fill date.
    end_date = (pos.get("end_date") or "").strip()
    stored = (pos.get("stored_end_date") or "").strip()
    if end_date and stored and end_date != stored:
        out.append({
            "target": "end_date",
            "kind": "horizon_reanchor",
            "from": stored,
            "to": end_date,
            "reason": "effective end_date anchored to user fill date",
        })

    return out


def build_position_trace_row(pos: dict, today_iso: str) -> dict:
    """Build one trace row from an enriched position dict.

    Records the latest volume-strength trajectory (the daily "update"), the
    current target levels (logged every day so their constancy is auditable),
    and the day's action + any alterations. Pure; no I/O.
    """
    traj = pos.get("trajectory") or {}
    return {
        "date": today_iso,
        "pick_id": pos.get("pick_id"),
        "trace_id": pos.get("trace_id", ""),
        "symbol": pos.get("symbol"),
        "days_held": pos.get("days_held"),
        "current_price": pos.get("current_price"),
        "pnl_pct": pos.get("pnl_pct"),
        "confirmation_score": pos.get("confirmation_score"),
        # ---- Daily volume-strength update (trajectory vs entry) ----
        "trajectory_overall": traj.get("overall"),
        "trajectory_exit_recommendation": bool(traj.get("exit_recommendation")),
        "trajectory_headline": traj.get("headline", ""),
        "indicators": [
            {
                "name": i.get("name"),
                "state": i.get("state"),
                "entry_value": i.get("entry_value"),
                "current_value": i.get("current_value"),
            }
            for i in (traj.get("indicators") or [])
        ],
        # ---- Targets (recorded daily so any drift is visible in the tape) ----
        "entry_price": pos.get("entry_price"),
        "stop_price": pos.get("stop_price"),
        "t1_price": pos.get("t1_price"),
        "t2_price": pos.get("t2_price"),
        "hit_t1": pos.get("hit_t1"),
        "t1_status": pos.get("t1_status"),
        "days_to_expected_t1": pos.get("days_to_expected_t1"),
        "end_date": pos.get("end_date"),
        "stored_end_date": pos.get("stored_end_date"),
        "horizon_days": pos.get("horizon_days"),
        "horizon_extension_days": pos.get("horizon_extension_days"),
        "end_date_reached": pos.get("end_date_reached"),
        # ---- Action + the "what changed and why" trace ----
        "action": pos.get("action"),
        "action_label": pos.get("action_label"),
        "action_note": pos.get("action_note"),
        "proposed_new_stop": pos.get("new_stop"),
        "alterations": _alterations_for(pos),
    }


def append_daily_position_traces(
    positions: list[dict],
    today_iso: Optional[str] = None,
) -> Optional[Path]:
    """Write one trace row per open position for `today_iso`.

    Idempotent: rewrites the day's file rather than appending, so a re-run
    produces no duplicate rows. Returns the file path, or None if there were
    no positions to trace. Never raises on write — callers treat trace failure
    as non-fatal.
    """
    if today_iso is None:
        today_iso = datetime.now(IST).date().isoformat()
    _TRACES_DIR.mkdir(parents=True, exist_ok=True)
    p = _trace_path(today_iso)

    ts = datetime.now(IST).isoformat(timespec="seconds")
    lines: list[str] = []
    for pos in positions:
        row = {
            "ts": ts,
            "schema_version": SCHEMA_VERSION,
            **build_position_trace_row(pos, today_iso),
        }
        lines.append(json.dumps(row, ensure_ascii=False, default=str))

    body = ("\n".join(lines) + "\n") if lines else ""
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)   # atomic-ish swap; prior file overwritten only on success
    return p
