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
            trace_id = r.get("pick_id", "")
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
            ret = (close / entry_px - 1) * 100
            hit_target = close >= target_px
            hit_stop = close <= stop_px

            _append_outcome({
                "ts": datetime.now(IST).isoformat(timespec="seconds"),
                "trace_id": trace_id,
                "symbol": r["symbol"],
                "entry_date": entry_iso,
                "entry_price": entry_px,
                "horizon_days": horizon,
                "exit_price": round(close, 2),
                "return_pct": round(ret, 2),
                "hit_target": hit_target,
                "hit_stop": hit_stop,
                "exit_reason": (
                    "target" if hit_target else
                    "stop" if hit_stop else
                    "neither"
                ),
            })
            summary["appended"] += 1

    return summary
