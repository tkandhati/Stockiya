"""Weekly tracking report — run every Friday after market close.

    python -m scripts.weekly_report

Prints the 5 metrics from WEEKLY_TRACKING.md so you can paste them into a
running log:

    1. Open-pick MTM
    2. Volume-distribution exit triggers firing?
    3. LT-score drift on open positions
    4. Universe coverage (tickers passing the 60-floor today)
    5. Block-deal coverage (top-10 ranked tickers with named institutional flow)

Reads from already-written files:
    data/portfolio.csv, data/portfolio_weekly.csv  — open picks + last close
    data/picks_<TODAY>.json                        — today's run
    data/traces/run_<TODAY>_<sym>.jsonl            — per-stage trace
"""

from __future__ import annotations

import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# Ensure stdout handles unicode on Windows consoles.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA = _PROJECT_ROOT / "data"
_TRACES = _DATA / "traces"


def _today_iso() -> str:
    return datetime.now(IST).date().isoformat()


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _latest_trace_for(symbol: str, today_iso: str) -> Optional[Path]:
    """Most recent trace file for `symbol` on or before today."""
    candidates = sorted(_TRACES.glob(f"run_*_{symbol}.jsonl"))
    return candidates[-1] if candidates else None


def _trace_stage(trace_path: Path, stage_id: str) -> Optional[dict]:
    """Last row in the trace file for the given stage."""
    if not trace_path.exists():
        return None
    last = None
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("stage") == stage_id:
            last = row
    return last


# --------------------------------------------------------------------------- #
# 1. Open-pick MTM
# --------------------------------------------------------------------------- #

def report_open_mtm() -> str:
    rows = _read_csv(_DATA / "portfolio_weekly.csv")
    if not rows:
        return "  no portfolio_weekly.csv yet"
    latest_week = max(r["week_ending"] for r in rows)
    out: list[str] = []
    for r in rows:
        if r["week_ending"] != latest_week:
            continue
        pnl = r.get("pnl_from_entry_pct", "?")
        out.append(f"  {r['symbol']:14s} {pnl}% from entry "
                   f"(close={r.get('close')}, week={latest_week})")
    return "\n".join(out) or "  no open picks"


# --------------------------------------------------------------------------- #
# 2. Volume-distribution exit triggers
# --------------------------------------------------------------------------- #

def report_exit_triggers() -> str:
    today = _today_iso()
    port = _read_csv(_DATA / "portfolio.csv")
    open_syms = [r["symbol"] for r in port if r.get("status") == "open"]
    if not open_syms:
        return "  no open picks"

    out: list[str] = []
    for sym in open_syms:
        trace = _latest_trace_for(sym, today)
        if not trace:
            out.append(f"  {sym:14s} no recent trace — re-run pipeline today")
            continue
        mt = _trace_stage(trace, "MT")
        if not mt:
            out.append(f"  {sym:14s} no MT stage in trace — re-run")
            continue
        feats = mt.get("features", {})
        flags: list[str] = []
        if (feats.get("obv_30d_pct") or 0) < 0:
            flags.append(f"OBV-30d {feats.get('obv_30d_pct'):+.1f}%")
        if (feats.get("cmf_21d") or 0) < 0:
            flags.append(f"CMF-21d {feats.get('cmf_21d'):+.2f}")
        if (feats.get("updown_30d") or 0) < 0.85:
            flags.append(f"U/D-30d {feats.get('updown_30d'):.2f}")
        if flags:
            out.append(f"  {sym:14s} EXIT TRIGGER FIRING — {', '.join(flags)}")
        else:
            out.append(f"  {sym:14s} clear (no exit triggers)")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 3. LT-score drift
# --------------------------------------------------------------------------- #

def report_lt_drift() -> str:
    today = _today_iso()
    port = _read_csv(_DATA / "portfolio.csv")
    open_syms = [r["symbol"] for r in port if r.get("status") == "open"]
    if not open_syms:
        return "  no open picks"
    out: list[str] = []
    for sym in open_syms:
        trace = _latest_trace_for(sym, today)
        if not trace:
            out.append(f"  {sym:14s} no recent trace")
            continue
        lt = _trace_stage(trace, "LT")
        if not lt:
            continue
        score = lt.get("score", 0)
        flag = " ⚠ below 0.60" if score < 0.60 else ""
        out.append(f"  {sym:14s} LT score {score:.2f}{flag}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 4. Universe coverage
# --------------------------------------------------------------------------- #

def report_universe_coverage() -> str:
    today = _today_iso()
    picks_file = _DATA / f"picks_{today}.json"
    if not picks_file.exists():
        return "  no picks file for today — run pipeline first"

    cleared = 0
    rejected_by_stage: dict[str, int] = {}
    for trace in _TRACES.glob(f"run_{today}_*.jsonl"):
        final = _trace_stage(trace, "FINAL")
        if final:
            comp = final.get("composite", 0)
            if comp >= 60:
                cleared += 1
            continue
        # No FINAL row -> failed a gate. Find the last stage row, that's where it died.
        last_stage_id = None
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("passed", True):
                last_stage_id = row.get("stage")
                break
        if last_stage_id:
            rejected_by_stage[last_stage_id] = rejected_by_stage.get(last_stage_id, 0) + 1

    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(rejected_by_stage.items()))
    return f"  {cleared} tickers cleared 60-floor.  Rejected: {breakdown or 'none'}"


# --------------------------------------------------------------------------- #
# 5. Block-deal coverage
# --------------------------------------------------------------------------- #

def report_block_deals() -> str:
    today = _today_iso()
    picks_file = _DATA / f"picks_{today}.json"
    if not picks_file.exists():
        return "  no picks file for today"
    payload = json.loads(picks_file.read_text(encoding="utf-8"))
    picks = payload.get("picks", [])
    if not picks:
        return "  no picks today (nothing actionable)"

    out: list[str] = []
    for p in picks:
        trace = _latest_trace_for(p["symbol"], today)
        if not trace:
            continue
        dd = _trace_stage(trace, "DD")
        if not dd:
            continue
        feats = dd.get("features", {})
        total = (feats.get("buy_count") or 0) + (feats.get("sell_count") or 0)
        ratio = feats.get("net_qty_ratio", 0)
        out.append(f"  {p['symbol']:14s} {feats.get('buy_count', 0)}B/"
                   f"{feats.get('sell_count', 0)}S, net {ratio:+.0%} "
                   f"({'covered' if total >= 2 else 'sparse'})")
    return "\n".join(out) or "  no DD data"


# --------------------------------------------------------------------------- #

def main() -> None:
    today = _today_iso()
    print(f"=== Stockiya weekly report — {today} ===\n")

    print("1. Open-pick MTM")
    print(report_open_mtm())
    print()

    print("2. Volume-distribution exit triggers firing?")
    print(report_exit_triggers())
    print()

    print("3. LT-score drift on open positions")
    print(report_lt_drift())
    print()

    print("4. Universe coverage today")
    print(report_universe_coverage())
    print()

    print("5. Block-deal coverage on today's picks")
    print(report_block_deals())
    print()

    print("Action: write a one-line summary + decisions into your weekly log.")


if __name__ == "__main__":
    main()
