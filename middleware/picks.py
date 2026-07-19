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

from backend.orchestrator import run_universe
from backend.stages.render import PICKS_SCHEMA_VERSION
from backend.trading_day import (
    classify_pre_pipeline,
    load_previous_picks,
)

from .picks_cache import ist_today_iso, read_picks, write_picks
from .schemas import PicksResponse

log = logging.getLogger("picks")


def generate_picks() -> PicksResponse:
    """Run the pipeline and persist results. Returns the validated DTO.

    Only writes `picks_<today>.json` when the pipeline actually ran for
    today. On a non-trading day the orchestrator returns the previous
    active day's picks unchanged — writing that back under today's key
    would obscure the source date and pollute the historical archive.
    """
    today = ist_today_iso()
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
    """Read the cached picks for today, or run the pipeline if missing.

    Order of preference:
      1. Today's cached picks file — happy path.
      2. If today is a non-trading day (weekend), serve the previous
         active trading day's file directly without invoking the
         pipeline. Cheap, avoids spinning up the fetch layer on Sun.
      3. Trading day with no cache → run the pipeline. If ingest
         returns no data (holiday), the orchestrator itself falls
         through to the previous file.
    """
    today = ist_today_iso()
    cached = read_picks(today)
    if cached and int(cached.get("schema_version") or 0) >= PICKS_SCHEMA_VERSION:
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
