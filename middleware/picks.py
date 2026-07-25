"""Thin picks server — reads the day's precomputed picks file, or runs the
pipeline on demand if it's missing.

The intelligence lives in `backend/orchestrator.py` + `backend/stages/*`.
This module's only job is to map an HTTP request to the JSON the UI needs.

Non-trading-day behavior (per backend/trading_day.py):
  - On Sat/Sun and days when no fresh OHLCV is available, the pipeline
    returns the previous active trading day's pick set without touching
    picks_<today>.json. This module honors that: on a non-trading day
    we serve the previous file and do NOT write it under today's key.
"""

from __future__ import annotations

import logging
import os

from backend.day_freshness import should_serve_cache
from backend.orchestrator import run_universe
from backend.stages.render import PICKS_SCHEMA_VERSION
from backend.trading_day import (
    classify_pre_pipeline,
    load_previous_picks,
)

from .picks_cache import delete_picks, ist_today_iso, read_picks, write_picks
from .schemas import PicksResponse

log = logging.getLogger("picks")


def generate_picks(force: bool = False) -> PicksResponse:
    """Run the pipeline and persist results. Returns the validated DTO.

    Only writes `picks_<today>.json` when the pipeline actually ran for
    today. On a non-trading day the orchestrator returns the previous
    active day's picks unchanged — writing that back under today's key
    would obscure the source date and pollute the historical archive.

    `force=True` (the Refresh button) first DELETES today's file so this is a
    clean delete-and-recreate rather than an in-place overwrite.
    """
    today = ist_today_iso()
    if force and delete_picks(today):
        log.info("Refresh: deleted existing picks file for %s", today)
    log.info("Running orchestrator for %s", today)
    response = run_universe(today_iso=today)
    if response.get("date") == today:
        write_picks(today, response)
    else:
        log.info(
            "Non-trading day: preserving previous picks file (%s); "
            "not writing under today's key (%s).",
            response.get("date"), today,
        )
    return PicksResponse(**response)


def get_or_generate_picks() -> PicksResponse:
    """Read the cached picks for today, or run the pipeline if missing/stale.

    Freshness policy (backend/day_freshness.py):
      * Before 16:00 IST — always regenerate on load so intraday data shows.
      * 16:00 IST onward — if the day's file exists it is FROZEN and served
        as-is (no recompute).
      * No data for the day (holiday) — fall through to the previous working
        day's file (below), never fabricating a snapshot.
      * Manual refresh (POST /api/picks/refresh) bypasses this entirely via
        generate_picks(force=True) — a full delete & recreate.
    """
    today = ist_today_iso()
    cached = read_picks(today)
    cache_valid = bool(
        cached and int(cached.get("schema_version") or 0) >= PICKS_SCHEMA_VERSION
    )
    if cache_valid and should_serve_cache():
        return PicksResponse(**cached)

    pre = classify_pre_pipeline(today)
    if not pre.is_trading_day:
        prev = load_previous_picks(today)
        if prev is not None:
            log.info(
                "Non-trading day (%s %s) — serving previous picks from %s.",
                pre.weekday, today, prev.get("date"),
            )
            return PicksResponse(**prev)
        log.info(
            "Non-trading day (%s %s) with no prior picks; running pipeline "
            "to render an empty response.", pre.weekday, today,
        )

    return generate_picks()
