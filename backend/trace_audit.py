"""Trace audit -- proves the pipeline is working when 0 picks fire.

Reads data/traces/run_<date>_*.jsonl files and produces:
  1. Stage drop-off table (how many cleared each gate)
  2. Near-miss roster (tickers that passed N-1 gates) with the exact
     failure reason + the numeric values that caused the failure
  3. Top survivors (if any), ranked by confirmation score

The point of this tool is to let you look at a 0-pick day and confirm the
chain is healthy -- not silently broken. A typical healthy 0-pick day shows
a handful of tickers cleared 4 of 5 gates and failed BR (no breakout today
yet) or VD (no volume dry-up yet). That's the system working as designed.

Usage:
    python -m backend.trace_audit                 # today (IST)
    python -m backend.trace_audit 2026-05-27      # a specific date
    python -m backend.trace_audit --verbose       # show every ticker
    python -m backend.trace_audit --near-miss 10  # show top-10 near-misses
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"

# Canonical gate order; used to label "how far did this ticker get".
GATE_ORDER = ["U", "I", "LT", "CS", "VD", "BR"]
GATE_LABEL = {
    "U":  "Universe",
    "I":  "Ingest",
    "LT": "Long-term flow",
    "CS": "Consolidation",
    "VD": "Volume / Divergence",
    "BR": "Breakout",
}


def _ticker_chain(file: Path) -> dict:
    """Read one ticker's JSONL trace -> ordered chain of stage outcomes."""
    chain: list[dict] = []
    final: Optional[dict] = None
    with file.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            stage = row.get("stage")
            if not stage:
                continue
            if stage == "FINAL":
                final = row
                continue
            if stage not in GATE_ORDER:
                continue
            chain.append({
                "stage": stage,
                "passed": bool(row.get("passed")),
                "reason": (row.get("reason") or "").strip(),
                "evidence": row.get("evidence") or [],
                "features": row.get("features") or {},
                "score": row.get("score") or 0.0,
            })
    return {"chain": chain, "final": final}


def analyze(date_iso: str) -> dict:
    files = sorted(_TRACES_DIR.glob(f"run_{date_iso}_*.jsonl"))
    per_ticker: dict[str, dict] = {}
    for f in files:
        symbol = f.stem.replace(f"run_{date_iso}_", "")
        per_ticker[symbol] = _ticker_chain(f)
    return {"date": date_iso, "files": len(files), "per_ticker": per_ticker}


def _passed_gates(chain: list[dict]) -> int:
    return sum(1 for r in chain if r["passed"])


def _last_stage(chain: list[dict]) -> str:
    return chain[-1]["stage"] if chain else "-"


def _failure_summary(chain: list[dict]) -> str:
    """Return the killing-blow line for a ticker (last failed gate's reason)."""
    if not chain or chain[-1]["passed"]:
        return "passed all gates"
    last = chain[-1]
    reason = last["reason"] or "(no reason recorded)"
    return reason


def _print_dropoff(report: dict) -> None:
    """Stage-by-stage entry / exit counts."""
    print()
    print("=" * 78)
    print(f"  STAGE DROP-OFF  --  {report['date']}  ({report['files']} tickers)")
    print("=" * 78)

    # Tally how many tickers were evaluated at each gate, and how many passed.
    evaluated = {g: 0 for g in GATE_ORDER}
    passed = {g: 0 for g in GATE_ORDER}
    fail_reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for t in report["per_ticker"].values():
        for r in t["chain"]:
            g = r["stage"]
            evaluated[g] += 1
            if r["passed"]:
                passed[g] += 1
            else:
                key = r["reason"].split(";")[0].strip()[:60] or "(no reason)"
                fail_reasons[g][key] += 1

    print(f"{'Gate':<22} {'eval':>6} {'pass':>6} {'fail':>6}   top failure reason")
    print("-" * 78)
    for g in GATE_ORDER:
        if evaluated[g] == 0:
            print(f"{GATE_LABEL[g]:<22} {0:>6} {0:>6} {0:>6}   (not reached)")
            continue
        top = sorted(fail_reasons[g].items(), key=lambda x: -x[1])
        top_txt = ""
        if top:
            n, r = top[0][1], top[0][0]
            top_txt = f"{n}x {r[:42]}"
        f_count = evaluated[g] - passed[g]
        print(f"{GATE_LABEL[g]:<22} {evaluated[g]:>6} {passed[g]:>6} {f_count:>6}   {top_txt}")


def _print_near_miss(report: dict, top_n: int = 10) -> None:
    """Tickers that cleared the most gates, sorted by how far they got."""
    rows = []
    for sym, t in report["per_ticker"].items():
        chain = t["chain"]
        n_passed = _passed_gates(chain)
        last = _last_stage(chain)
        # Exclude tickers that didn't even ingest -- they're not informative
        if n_passed < 2:
            continue
        rows.append({
            "symbol": sym,
            "passed_count": n_passed,
            "last_stage": last,
            "stages_seen": [r["stage"] for r in chain],
            "killer_reason": _failure_summary(chain),
            "chain": chain,
        })

    # Sort: more passed gates = closer; tiebreak by latest stage in GATE_ORDER
    def sort_key(r):
        last_idx = GATE_ORDER.index(r["last_stage"]) if r["last_stage"] in GATE_ORDER else -1
        return (-r["passed_count"], -last_idx)

    rows.sort(key=sort_key)
    top = rows[:top_n]

    print()
    print("=" * 78)
    print(f"  TOP {len(top)} NEAR-MISSES  --  tickers that got furthest in the chain")
    print("=" * 78)
    if not top:
        print("  (no candidates passed even ingest; check data source)")
        return

    for r in top:
        seen = ", ".join(
            f"{'ok ' if c['passed'] else 'X '}{c['stage']}" for c in r["chain"]
        )
        print()
        print(f"  {r['symbol']:18}  cleared {r['passed_count']}/{len(GATE_ORDER)}   chain: {seen}")
        if not r["chain"][-1]["passed"]:
            killer = r["chain"][-1]
            print(f"    |-- killed at [{killer['stage']}] {GATE_LABEL[killer['stage']]}:")
            # Print every failed-evidence line so you can SEE the numbers
            for line in killer["evidence"] or [killer["reason"] or "(no detail)"]:
                print(f"         {line}")


def _print_passed_summary(report: dict) -> None:
    """If any tickers cleared all gates, print them in a separate section."""
    full_pass = []
    for sym, t in report["per_ticker"].items():
        chain = t["chain"]
        if chain and len(chain) == len(GATE_ORDER) and all(r["passed"] for r in chain):
            final = t["final"] or {}
            full_pass.append({
                "symbol": sym,
                "selected": final.get("selected"),
                "rank": final.get("rank"),
                "confirmation": final.get("confirmation"),
                "bonuses": (final.get("confirmation_components") or {}).get("bonuses_fired"),
            })

    print()
    print("=" * 78)
    print(f"  SURVIVORS  --  cleared all 6 gates ({len(full_pass)} ticker(s))")
    print("=" * 78)
    if not full_pass:
        print("  None today. By design 0 picks is the correct answer most days.")
        return
    for r in sorted(full_pass, key=lambda x: -(x["confirmation"] or 0)):
        flag = f"  [PICK #{r['rank']}]" if r["selected"] else "  (not selected)"
        bonuses = "; ".join(r["bonuses"] or []) or "--"
        print(f"  {r['symbol']:18}{flag}  confirmation={r['confirmation']:.3f}")
        print(f"    bonuses: {bonuses}")


def _print_verbose(report: dict) -> None:
    """Every ticker, every gate -- full transparency."""
    print()
    print("=" * 78)
    print("  PER-TICKER DETAIL  (every gate, every ticker)")
    print("=" * 78)
    for sym in sorted(report["per_ticker"]):
        t = report["per_ticker"][sym]
        chain = t["chain"]
        n = _passed_gates(chain)
        seen = " > ".join(
            f"{c['stage']}{'+' if c['passed'] else '-'}" for c in chain
        )
        last = chain[-1] if chain else None
        print(f"\n  {sym:18}  {n}/{len(GATE_ORDER)} gates   chain: {seen}")
        if last and not last["passed"]:
            print(f"    failed at {last['stage']}: {last['reason']}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Audit pipeline traces for a given date.")
    parser.add_argument(
        "date", nargs="?",
        default=datetime.now(IST).date().isoformat(),
        help="YYYY-MM-DD (default: today IST)",
    )
    parser.add_argument(
        "--near-miss", type=int, default=10,
        help="How many near-miss tickers to print (default 10)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print every ticker's full chain",
    )
    args = parser.parse_args()

    report = analyze(args.date)
    if report["files"] == 0:
        print(f"No trace files for {args.date}.")
        print(f"Looked in: {_TRACES_DIR}")
        print("Run the pipeline first:  python -m backend.nightly")
        return

    _print_dropoff(report)
    _print_passed_summary(report)
    _print_near_miss(report, top_n=args.near_miss)
    if args.verbose:
        _print_verbose(report)
    print()


if __name__ == "__main__":
    main()
