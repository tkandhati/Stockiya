"""Universe-level orchestrator — the one entry point for "run the picker."

Runs the per-ticker pipeline over every Nifty 100 symbol in parallel, ranks
the results, builds pick payloads for the top N (via stage [H]), and writes
the day's `picks_<date>.json` (via stage [R]).

Called by:
  - `backend/nightly.py` — cron / Task Scheduler entry, runs after market close
  - `middleware/picks.py:get_or_generate_picks()` — on-demand from the API
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .pipeline import PipelineResult, run_pipeline
from .stages import PER_TICKER_CHAIN
from .stages.hypothesis import build_pick_payload
from .stages.render import render_picks_response, write_picks_file
from .stages.score import rank_and_select
from .universe import UNIVERSE

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("orchestrator")

DEFAULT_TOP_N = 3
DEFAULT_MIN_COMPOSITE = 60.0


def run_universe(
    today_iso: Optional[str] = None,
    top_n: int = DEFAULT_TOP_N,
    min_composite: float = DEFAULT_MIN_COMPOSITE,
    max_workers: int = 10,
) -> dict:
    """Run the volume-only pipeline over Nifty 100, rank, render picks file.

    Returns a PicksResponse-shaped dict (also written to disk).
    """
    today_iso = today_iso or datetime.now(IST).date().isoformat()
    log.info("Pipeline run for %s — universe=%d, top_n=%d, floor=%.0f",
             today_iso, len(UNIVERSE), top_n, min_composite)

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

    # ---- Phase 2: rank + select top N ----
    selected = rank_and_select(
        results, top_n=top_n, min_composite=min_composite, today_iso=today_iso,
    )
    log.info("Ranked %d tickers; %d cleared the %.0f floor",
             len(results), len(selected), min_composite)

    # ---- Phase 3: build pick payloads for selected ----
    pick_payloads: list[dict] = []
    for res in selected:
        snap_with_signals = dict(res.snapshot or {})
        snap_with_signals["_signals"] = res.signals
        payload = build_pick_payload(res, snap_with_signals)
        pick_payloads.append(payload)

    # ---- Phase 4: render to disk ----
    demo_mode = os.environ.get("DEMO_MODE", "0") == "1"
    response = render_picks_response(pick_payloads, today_iso, demo_mode=demo_mode)
    path = write_picks_file(response)
    log.info("Wrote %s — %d picks", path.name, len(pick_payloads))

    # ---- Phase 5: log selected picks to portfolio ledger (for [O] later) ----
    try:
        from .portfolio import record_picks
        added = record_picks(response)
        if added:
            log.info("portfolio.csv: appended %d new picks", added)
    except Exception:
        log.exception("portfolio recording failed (non-fatal)")

    return response


def diagnostics(today_iso: Optional[str] = None) -> dict:
    """Run the pipeline and return a summary: how many cleared each stage."""
    today_iso = today_iso or datetime.now(IST).date().isoformat()
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
    return {"date": today_iso, "stage_counts": counts}
