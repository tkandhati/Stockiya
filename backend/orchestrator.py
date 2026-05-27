"""Universe-level orchestrator — the one entry point for "run the picker."

Gates-based flow (PRINCIPLES Section 2):

    Phase 0  [RG]  Market regime gate (one shot, NIFTY + BANKNIFTY).
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

from .pipeline import PipelineResult, append_final_trace, run_pipeline
from .stages import PER_TICKER_CHAIN
from .stages.hypothesis import build_pick_payload
from .stages.rank import rank_survivors
from .stages.regime import check_regime
from .stages.render import render_picks_response, write_picks_file
from .universe import UNIVERSE

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
    log.info(
        "Pipeline run for %s — universe=%d, top_n=%d, account=%.0f",
        today_iso, len(UNIVERSE), top_n, account_value,
    )

    # ---- Phase 0: Market regime gate ----
    regime = check_regime()
    log.info("Regime: %s", regime.summary)
    if not regime.passed:
        response = render_picks_response(
            [], today_iso,
            demo_mode=demo_mode,
            regime=regime.as_dict(),
            message=regime.summary,
        )
        path = write_picks_file(response)
        log.info("Regime halted — wrote empty %s", path.name)
        return response

    # ---- Phase 1: per-ticker pipeline (parallel) ----
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
    log.info(
        "Per-ticker: %d processed, %d cleared all gates",
        len(results), len(survivors),
    )

    # ---- Phase 2: rank + select ----
    selected = rank_survivors(survivors, top_n=top_n)
    log.info("Selected %d picks (top_n=%d)", len(selected), top_n)

    # ---- Phase 3: build pick payloads for selected ----
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
    message: Optional[str] = None
    if not pick_payloads:
        message = "Nothing actionable today — quality over quantity."
    response = render_picks_response(
        pick_payloads, today_iso,
        demo_mode=demo_mode,
        regime=regime.as_dict(),
        message=message,
    )
    path = write_picks_file(response)
    log.info("Wrote %s — %d picks", path.name, len(pick_payloads))

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
