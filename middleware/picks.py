"""Thin picks server — reads the day's precomputed picks file, or runs the
pipeline on demand if it's missing.

The intelligence lives in `backend/orchestrator.py` + `backend/stages/*`.
This module's only job is to map an HTTP request to the JSON the UI needs.
"""

from __future__ import annotations

import logging
import os

from backend.orchestrator import run_universe
from backend.stages.render import PICKS_SCHEMA_VERSION

from .picks_cache import ist_today_iso, read_picks, write_picks
from .schemas import PicksResponse

log = logging.getLogger("picks")


def generate_picks() -> PicksResponse:
    """Run the pipeline and persist results. Returns the validated DTO."""
    today = ist_today_iso()
    log.info("Running orchestrator for %s", today)
    response = run_universe(today_iso=today)
    write_picks(today, response)
    return PicksResponse(**response)


def get_or_generate_picks() -> PicksResponse:
    """Read the cached picks for today, or run the pipeline if missing."""
    today = ist_today_iso()
    cached = read_picks(today)
    if cached and int(cached.get("schema_version") or 0) >= PICKS_SCHEMA_VERSION:
        return PicksResponse(**cached)
    return generate_picks()
