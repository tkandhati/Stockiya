"""Universe-level orchestrator — the one entry point for "run the picker."

Gates-based flow (PRINCIPLES Section 2):

    Phase 0  [RG]  Market regime gate (one shot, NIFTY 100).
                   FAIL -> write empty picks file with regime info, return.
    Phase 1  per-ticker pipeline (parallel) over Nifty 100.
    Phase 2  [RK]  Confirmation-strength ranking; select top N.
    Phase 3  [PS] + [H]  Build pick payloads for the selected.
    Phase 4  [R]   Render to disk + append final trace rows.
    Phase 5  Portfolio ledger update.

Called by:
  - `backend/nightly.py`              — cron entry, runs after market close
  - `middleware/picks.py`             — on-demand from the API
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from collections import defaultdict

from .pipeline import PipelineResult, append_final_trace, run_pipeline
from .stages import PER_TICKER_CHAIN
from .stages.hypothesis import build_pick_payload
from .stages.rank import rank_survivors
from .stages.regime import check_regime
from .stages.render import render_picks_response, write_picks_file
from .universe import UNIVERSE

# Stages in canonical order — used for the per-gate breakdown log.
_GATE_ORDER = ["U", "I", "HR", "LT", "CS", "VD", "BR"]
_GATE_LABEL = {
    "U": "Universe", "I": "Ingest", "HR": "Hard rejects",
    "LT": "Long-term flow", "CS": "Consolidation",
    "VD": "Volume/Divergence", "BR": "Breakout",
}

# Near-miss qualification: a ticker must have cleared at least this many
# gates to be interesting enough to surface on the empty-picks page.
_NEAR_MISS_MIN_PASSED = 3


def _collect_near_misses(results: list[PipelineResult], n: int = 5) -> list[dict]:
    """Pick the top-N tickers that cleared the most gates and then failed.

    Each entry shows what passed and the specific reason it failed at the
    killing-blow gate. This is the panel the user sees at the bottom of the
    picks page on a zero-pick day — proof that the chain is filtering on
    real data, not silently dropping things.
    """
    rows = []
    for r in results:
        # Build the ordered chain of evaluated stages
        chain = [
            (sid, r.stage_results[sid])
            for sid in _GATE_ORDER
            if sid in r.stage_results
        ]
        if not chain:
            continue
        last_sid, last_sr = chain[-1]
        # Only "near miss" if the final stage was a failure (else it's a survivor)
        if last_sr.passed:
            continue
        passed = [(sid, sr) for sid, sr in chain if sr.passed]
        if len(passed) < _NEAR_MISS_MIN_PASSED:
            continue

        rows.append({
            "symbol": r.symbol,
            "company": (r.snapshot or {}).get("company") or r.symbol,
            "passed_gates": [
                {
                    "stage_id": sid,
                    "label": _GATE_LABEL.get(sid, sid),
                    "evidence": list(sr.evidence or [])[:2],   # top-2 lines per gate
                }
                for sid, sr in passed
            ],
            "failed_gate": {
                "stage_id": last_sid,
                "label": _GATE_LABEL.get(last_sid, last_sid),
                "reason": last_sr.reason or "",
                "evidence": list(last_sr.evidence or []),       # all failure lines
            },
            "passed_count": len(passed),
        })

    # Closer to passing = more interesting. Tiebreak by latest stage reached.
    def _sort_key(row):
        last_idx = _GATE_ORDER.index(row["failed_gate"]["stage_id"])
        return (-row["passed_count"], -last_idx)

    rows.sort(key=_sort_key)
    return rows[:n]


def _log_gate_breakdown(results: list[PipelineResult]) -> None:
    """Print the live story: how many tickers cleared each gate, with the
    top failure reason. The middleware terminal shows this in real time so
    the user can confirm the chain is doing real work, not silently failing.
    """
    evaluated: dict[str, int] = defaultdict(int)
    passed: dict[str, int] = defaultdict(int)
    fail_reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for r in results:
        for sid, sr in r.stage_results.items():
            if sid not in _GATE_ORDER:
                continue
            evaluated[sid] += 1
            if sr.passed:
                passed[sid] += 1
            else:
                key = (sr.reason or "").split(";")[0].strip()[:50] or "(no reason)"
                fail_reasons[sid][key] += 1

    log.info("  Per-gate breakdown:")
    log.info("    Gate                eval  pass  fail   top failure reason")
    log.info("    ------------------  ----  ----  ----   -------------------------------")
    for sid in _GATE_ORDER:
        if evaluated[sid] == 0:
            log.info("    %-18s  %4d  %4d  %4d   (not reached)",
                     _GATE_LABEL[sid], 0, 0, 0)
            continue
        f = evaluated[sid] - passed[sid]
        top = sorted(fail_reasons[sid].items(), key=lambda x: -x[1])
        top_txt = f"{top[0][1]}x {top[0][0][:30]}" if top else ""
        log.info(
            "    %-18s  %4d  %4d  %4d   %s",
            _GATE_LABEL[sid], evaluated[sid], passed[sid], f, top_txt,
        )

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("orchestrator")

DEFAULT_TOP_N = 3
DEFAULT_ACCOUNT_VALUE = float(os.environ.get("STOCKYA_ACCOUNT_VALUE", "100000"))


def run_universe(
    today_iso: Optional[str] = None,
    top_n: int = DEFAULT_TOP_N,
    account_value: float = DEFAULT_ACCOUNT_VALUE,
    max_workers: int = 10,
    **_kwargs,   # absorb legacy `min_composite` arg silently
) -> dict:
    """Run the gates-based pipeline over Nifty 100. Returns the
    PicksResponse-shaped dict that's also written to disk.
    """
    today_iso = today_iso or datetime.now(IST).date().isoformat()
    demo_mode = os.environ.get("DEMO_MODE", "0") == "1"
    log.info("=" * 76)
    log.info("  PIPELINE RUN  %s   (universe=%d, top_n=%d, account=%.0f, demo=%s)",
             today_iso, len(UNIVERSE), top_n, account_value, demo_mode)
    log.info("=" * 76)

    # ---- Phase 0: Market regime gate ----
    log.info("  [Phase 0/4] Market regime gate ...")
    regime = check_regime()
    log.info("  [Phase 0/4] %s", regime.summary)
    if not regime.passed:
        response = render_picks_response(
            [], today_iso,
            demo_mode=demo_mode,
            regime=regime.as_dict(),
            message=regime.summary,
        )
        path = write_picks_file(response)
        log.info("  ABORT: regime halted -> wrote empty %s", path.name)
        log.info("=" * 76)
        return response

    # ---- Phase 1: per-ticker pipeline (parallel) ----
    log.info("  [Phase 1/4] Running per-ticker chain over %d tickers (%d workers) ...",
             len(UNIVERSE), max_workers)
    results: list[PipelineResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(run_pipeline, sym, PER_TICKER_CHAIN, today_iso): sym
            for sym in UNIVERSE
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                log.exception("pipeline crashed for %s", futures[fut])

    survivors = [r for r in results if r.passed_gates]
    log.info("  [Phase 1/4] Done. %d processed, %d cleared ALL gates.",
             len(results), len(survivors))
    _log_gate_breakdown(results)

    # ---- Phase 2: rank + select ----
    log.info("  [Phase 2/4] Confirmation ranking over %d survivors ...", len(survivors))
    selected = rank_survivors(survivors, top_n=top_n)
    if selected:
        log.info("  [Phase 2/4] Selected %d:", len(selected))
        for pick in selected:
            bonuses = (pick.confirmation_components or {}).get("bonuses_fired") or []
            log.info("    #%d  %-15s  confirmation=%.3f  bonuses=%s",
                     pick.rank, pick.symbol, pick.confirmation_score,
                     ", ".join(bonuses) or "-")
    else:
        log.info("  [Phase 2/4] No survivors — 0 picks today.")
        log.info("             (Run: python -m backend.trace_audit  for the near-miss list.)")

    # ---- Phase 3: build pick payloads for selected ----
    log.info("  [Phase 3/4] Building pick payloads + position sizing ...")
    pick_payloads: list[dict] = []
    for res in selected:
        try:
            payload = build_pick_payload(
                res, res.snapshot or {},
                account_value=account_value,
                today_iso=today_iso,
            )
            pick_payloads.append(payload)
        except Exception:
            log.exception("build_pick_payload failed for %s", res.symbol)

    # Append FINAL trace rows for every ticker so the RL replay buffer
    # captures the ranking decision (selected and not).
    for r in results:
        try:
            append_final_trace(r, today_iso)
        except Exception:
            log.exception("append_final_trace failed for %s", r.symbol)

    # ---- Phase 4: render to disk ----
    log.info("  [Phase 4/4] Rendering picks_%s.json ...", today_iso)
    message: Optional[str] = None
    near_misses: list[dict] = []
    if not pick_payloads:
        message = "Nothing actionable today — quality over quantity."
        near_misses = _collect_near_misses(results, n=5)
        if near_misses:
            log.info("  [Phase 4/4] Top %d near-misses (cleared >= %d gates):",
                     len(near_misses), _NEAR_MISS_MIN_PASSED)
            for nm in near_misses:
                log.info("    %-15s cleared %d/%d -- failed [%s] %s",
                         nm["symbol"], nm["passed_count"], len(_GATE_ORDER),
                         nm["failed_gate"]["stage_id"],
                         (nm["failed_gate"]["reason"] or "")[:60])
    response = render_picks_response(
        pick_payloads, today_iso,
        demo_mode=demo_mode,
        regime=regime.as_dict(),
        message=message,
        near_misses=near_misses,
    )
    path = write_picks_file(response)
    log.info("  [Phase 4/4] Wrote %s  (%d pick%s)",
             path.name, len(pick_payloads), "" if len(pick_payloads) == 1 else "s")
    log.info("=" * 76)

    # ---- Phase 5: portfolio ledger ----
    try:
        from .portfolio import record_picks
        added = record_picks(response)
        if added:
            log.info("portfolio.csv: appended %d new picks", added)
    except Exception:
        log.exception("portfolio recording failed (non-fatal)")

    return response


def diagnostics(today_iso: Optional[str] = None) -> dict:
    """Run the pipeline and return per-stage pass/fail counts.

    Useful in dev: shows where in the chain the universe is dropping out.
    """
    today_iso = today_iso or datetime.now(IST).date().isoformat()

    regime = check_regime()
    if not regime.passed:
        return {
            "date": today_iso,
            "regime": regime.as_dict(),
            "stage_counts": {},
            "note": "regime halted; per-ticker chain not run",
        }

    results: list[PipelineResult] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(run_pipeline, sym, PER_TICKER_CHAIN, today_iso): sym
            for sym in UNIVERSE
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                log.exception("pipeline crashed for %s", futures[fut])

    counts: dict[str, dict] = {}
    for r in results:
        for sid, sr in r.stage_results.items():
            counts.setdefault(sid, {"passed": 0, "failed": 0, "scores": []})
            counts[sid]["passed" if sr.passed else "failed"] += 1
            if sr.score:
                counts[sid]["scores"].append(sr.score)
    for sid, c in counts.items():
        scores = c.pop("scores", [])
        c["avg_score"] = round(sum(scores) / len(scores), 3) if scores else None
    return {
        "date": today_iso,
        "regime": regime.as_dict(),
        "stage_counts": counts,
        "survivors": sum(1 for r in results if r.passed_gates),
    }
