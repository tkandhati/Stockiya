"""Sliding-window learning trigger — event-driven IC diagnostic + auto
champion-challenger.

Fires after every N newly-matured T+90 outcomes (default 5). Two things
happen on each fire, in this order:

  1. Compute the per-stage information coefficient — Pearson correlation
     between that stage's entry-day margin and the pick's T+90 return —
     over a rolling window of the last WINDOW_SIZE outcomes (default 5).
     This is the *visibility* signal — noisy at n=5, but honest.

  2. **Invoke the champion-challenger tuner** (`scripts.tune_weights.
     run_programmatic(apply=True)`). The tuner's own safeties gate the
     write:

         a. `MIN_OUTCOMES_TO_TUNE = 20` floor — below this, tuner refuses
            to fit. Every event's `champion_challenger.decision` will be
            `refused_min_outcomes` until enough labels accumulate. This
            protects against the classic small-sample thrash: weights
            shifting on random walks.
         b. Strict-beat ratchet with `EPSILON = 0.001` — even at n >= 20,
            the tuner overwrites `config/stage_weights.json` only if a
            fresh fit's replay metric exceeds the incumbent by EPSILON.
            No beat, no write.

     The full tuner decision is written back to the event file so the
     audit trail is one file per fire: IC + samples + CC outcome.

Set `CHAMPION_CHALLENGER_MODE = "dry_run"` at the top of this file to
compute the decision without touching config (useful for observing what
would happen). Set to `"disabled"` to skip CC entirely and emit
diagnostic-only events (the pre-2026-07-17-b behavior).

Design invariants:
    - Deterministic: same outcomes.jsonl + traces → same event log AND
      same CC decision (both paths are pure math + config-read).
    - Append-only for events: never rewrites past events; state file
      tracks the last-processed matured count so re-runs are idempotent.
    - Defensive: any exception in the CC step is caught here and logged
      as {invoked: False, error: ...} in the event. The event file is
      always written even if CC fails.
    - Outcome-logging pipeline is protected: the caller in
      `stages/outcome.py` wraps this whole module in try/except; a bug
      here cannot break outcome ingestion.

Fix points:
    TRIGGER_EVERY_N            (default 5)      new T+90 outcomes → fire
    WINDOW_SIZE                (default 5)      recent outcomes to score
    IC_STRONG_THRESHOLD        (default 0.5)    |r| threshold → hint
    MIN_SAMPLES_FOR_IC         (default 3)      below → IC undefined
    CHAMPION_CHALLENGER_MODE   (default "apply") apply | dry_run | disabled
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("sliding_window_learn")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"
_OUTCOMES_PATH = _TRACES_DIR / "outcomes.jsonl"
_EVENTS_DIR = _PROJECT_ROOT / "data" / "learning_events"
_STATE_PATH = _EVENTS_DIR / "state.json"

TRIGGER_EVERY_N: int = 5
WINDOW_SIZE: int = 5
IC_STRONG_THRESHOLD: float = 0.5
MIN_SAMPLES_FOR_IC: int = 3
EVAL_HORIZON_DAYS: int = 90
# Champion-challenger invocation mode. See module docstring.
#   "apply"    — call tuner with apply=True; ratchet writes config on strict beat
#   "dry_run"  — call tuner with apply=False; decision is logged but config untouched
#   "disabled" — do not call the tuner at all; diagnostic-only events
CHAMPION_CHALLENGER_MODE: str = "apply"

# Match `scripts/tune_weights.py:SCORED_STAGE_IDS` so a window promoted here
# lines up 1:1 with the real tuner's feature vector.
SCORED_STAGE_IDS: list[str] = [
    "ACS", "AC", "LT", "CS", "VD", "BR", "WY", "VSA", "AVWAP",
]


# --------------------------------------------------------------------------- #
# State file: tracks the last-processed matured-outcome count so triggers
# fire exactly every TRIGGER_EVERY_N *newly*-matured picks, no double-fires
# on re-runs.
# --------------------------------------------------------------------------- #

def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"last_processed_count": 0, "events_written": 0}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_processed_count": 0, "events_written": 0}


def _save_state(state: dict) -> None:
    _EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


# --------------------------------------------------------------------------- #
# Data access — mirrors scripts/tune_weights.py loaders. Kept independent
# so the diagnostic can run even if the tuner script is refactored, and so
# a bug here can't corrupt the tuner path.
# --------------------------------------------------------------------------- #

def _load_matured_outcomes() -> list[dict]:
    """T+90 rows only, in the order they were appended."""
    if not _OUTCOMES_PATH.exists():
        return []
    out: list[dict] = []
    with _OUTCOMES_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("horizon_days") == EVAL_HORIZON_DAYS:
                out.append(row)
    return out


def _load_stage_margins(trace_id: str) -> dict[str, float]:
    """Return {stage_id: margin} for a trace_id. Scans trace files.

    A stage that did not run or ran-and-failed contributes 0. This matches
    `compute_composite` semantics so the IC we compute is joinable to the
    weights the tuner would fit against the same data.
    """
    margins: dict[str, float] = {sid: 0.0 for sid in SCORED_STAGE_IDS}
    if not _TRACES_DIR.exists():
        return margins
    for path in _TRACES_DIR.glob("run_*.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("trace_id") != trace_id:
                        continue
                    sid = row.get("stage")
                    if sid in margins and row.get("passed"):
                        margins[sid] = float(row.get("score") or 0.0)
        except OSError:
            continue
    return margins


# --------------------------------------------------------------------------- #
# Pearson correlation — stdlib only, matches the tuner's zero-deps posture.
# --------------------------------------------------------------------------- #

def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    """Sample Pearson r. Returns None on <MIN_SAMPLES_FOR_IC points, zero
    variance in either series, or non-finite result."""
    n = len(xs)
    if n < MIN_SAMPLES_FOR_IC or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = 0.0
    ssx = 0.0
    ssy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mean_x
        dy = y - mean_y
        num += dx * dy
        ssx += dx * dx
        ssy += dy * dy
    denom = math.sqrt(ssx * ssy)
    if denom <= 0.0:
        return None
    r = num / denom
    if not math.isfinite(r):
        return None
    return max(-1.0, min(1.0, r))


# --------------------------------------------------------------------------- #
# Window scoring
# --------------------------------------------------------------------------- #

def _score_window(window: list[dict]) -> dict:
    """Compute per-stage IC + summary stats for the given outcome window.

    Returns a serializable dict suitable for the event log. If a stage has
    all-zero margin across the window (never contributed), its IC is null.
    """
    returns_pct = [float(o.get("return_pct") or 0.0) for o in window]
    trace_ids = [o.get("trace_id") or "" for o in window]

    margins_matrix: dict[str, list[float]] = {sid: [] for sid in SCORED_STAGE_IDS}
    for tid in trace_ids:
        m = _load_stage_margins(tid) if tid else {sid: 0.0 for sid in SCORED_STAGE_IDS}
        for sid in SCORED_STAGE_IDS:
            margins_matrix[sid].append(m.get(sid, 0.0))

    ic_by_stage: dict[str, Optional[float]] = {}
    for sid in SCORED_STAGE_IDS:
        series = margins_matrix[sid]
        if all(v == 0.0 for v in series):
            ic_by_stage[sid] = None
            continue
        ic_by_stage[sid] = _pearson(series, returns_pct)

    hints: list[str] = []
    for sid, ic in ic_by_stage.items():
        if ic is None:
            continue
        if ic >= IC_STRONG_THRESHOLD:
            hints.append(
                f"{sid}: score predicts return (IC={ic:+.2f}) — candidate to weight up"
            )
        elif ic <= -IC_STRONG_THRESHOLD:
            hints.append(
                f"{sid}: score INVERSELY predicts return (IC={ic:+.2f}) — investigate; "
                "possible over-fit or reversed convention"
            )

    return {
        "samples": [
            {
                "trace_id": o.get("trace_id"),
                "symbol": o.get("symbol"),
                "entry_date": o.get("entry_date"),
                "return_pct": o.get("return_pct"),
                "exit_reason": o.get("exit_reason"),
            }
            for o in window
        ],
        "n": len(window),
        "mean_return_pct": round(sum(returns_pct) / len(returns_pct), 3)
        if returns_pct else None,
        "ic_by_stage": {sid: (round(v, 3) if v is not None else None)
                        for sid, v in ic_by_stage.items()},
        "learning_hints": hints,
    }


# --------------------------------------------------------------------------- #
# Public entry point — safe to call from `stages/outcome.py` after every
# outcome append. Guarded caller-side; any exception here is logged and
# swallowed so the outcome-log pipeline stays reliable.
# --------------------------------------------------------------------------- #

def _invoke_champion_challenger() -> dict:
    """Run the champion-challenger tuner and return its decision dict.

    Fully defensive — any failure returns an error-shaped dict rather
    than raising, so the event file is always writable. The tuner's own
    safeties (MIN_OUTCOMES_TO_TUNE floor, strict-beat ratchet) are what
    prevent weight thrashing at small n; we do not add a second floor
    here.
    """
    if CHAMPION_CHALLENGER_MODE == "disabled":
        return {
            "invoked": False,
            "mode": "disabled",
            "reason": "CHAMPION_CHALLENGER_MODE=disabled — diagnostic-only event",
        }
    apply_flag = CHAMPION_CHALLENGER_MODE == "apply"
    try:
        # Lazy import so a broken tuner doesn't prevent outcome logging.
        # scripts/ has an __init__.py; this is a real package.
        from scripts.tune_weights import run_programmatic
    except Exception as e:  # noqa: BLE001
        return {
            "invoked": False,
            "mode": CHAMPION_CHALLENGER_MODE,
            "error": f"import failed: {type(e).__name__}: {e}",
        }
    try:
        result = run_programmatic(
            apply=apply_flag,
            updated_by_tag="tune_weights.py:sliding_window",
        )
        result["invoked"] = True
        result["mode"] = CHAMPION_CHALLENGER_MODE
        return result
    except Exception as e:  # noqa: BLE001
        return {
            "invoked": False,
            "mode": CHAMPION_CHALLENGER_MODE,
            "error": f"tuner crashed: {type(e).__name__}: {e}",
        }


def maybe_fire_event(new_row: Optional[dict] = None) -> Optional[Path]:
    """If a fresh T+90 outcome pushes the total across the next
    TRIGGER_EVERY_N boundary, compute the window, invoke champion-challenger,
    and write one event file that contains both.

    Returns the path to the new event file, or None if no fire.

    `new_row` is optional context (the row that just got appended); used
    only to short-circuit when the append was a T+180 row (we count T+90
    only, so a T+180 append can never trigger).
    """
    if new_row is not None and int(new_row.get("horizon_days", 0)) != EVAL_HORIZON_DAYS:
        return None

    outcomes = _load_matured_outcomes()
    total = len(outcomes)
    if total < WINDOW_SIZE:
        # Not enough tape for even one window — nothing to compute yet.
        return None

    state = _load_state()
    last = int(state.get("last_processed_count", 0))

    # Fire when the count has advanced by at least TRIGGER_EVERY_N since
    # the last event. Not "exactly TRIGGER_EVERY_N" — that would miss
    # bursts (three matures on the same day would delay a trigger).
    if total < last + TRIGGER_EVERY_N and last > 0:
        return None
    # First-ever fire: also require total >= TRIGGER_EVERY_N so we don't
    # emit at n=WINDOW_SIZE < TRIGGER_EVERY_N when the two constants differ.
    if total < TRIGGER_EVERY_N:
        return None

    window = outcomes[-WINDOW_SIZE:]
    body = _score_window(window)

    # ---- Champion-challenger step. Runs after the window is scored so
    # the CC decision is captured in the same event file (one audit row
    # per fire). Tuner's own safety floors gate the write path.
    cc_result = _invoke_champion_challenger()

    now_ist = datetime.now(IST)
    action_taken = (
        "champion_challenger_applied" if cc_result.get("config_written")
        else "champion_challenger_no_op"
    )
    event = {
        "ts": now_ist.isoformat(timespec="seconds"),
        "schema_version": 2,   # v2 adds champion_challenger block
        "trigger_every_n": TRIGGER_EVERY_N,
        "window_size": WINDOW_SIZE,
        "matured_count_total": total,
        "matured_count_since_last_event": total - last,
        "action_taken": action_taken,
        "recommendation": (
            "IC block = visibility signal (noisy at n=5); champion_challenger "
            "block = the ratchet's decision. Weights only change when the "
            "tuner reports config_written=true. Below MIN_OUTCOMES_TO_TUNE "
            "the ratchet always refuses — that's the small-sample guardrail."
        ),
        **body,
        "champion_challenger": cc_result,
    }

    _EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"sliding_{now_ist.strftime('%Y-%m-%d')}_n{total}.json"
    out_path = _EVENTS_DIR / fname
    # Atomic write — never leave a half-written event file if the process
    # dies mid-write.
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(event, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(out_path)

    state["last_processed_count"] = total
    state["events_written"] = int(state.get("events_written", 0)) + 1
    state["last_event_ts"] = event["ts"]
    _save_state(state)

    log.info(
        "sliding-window event: n=%d hints=%d cc=%s cfg_written=%s path=%s",
        total, len(body["learning_hints"]),
        cc_result.get("decision", "n/a"),
        bool(cc_result.get("config_written")),
        out_path.name,
    )
    return out_path
