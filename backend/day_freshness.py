"""Daily snapshot freshness — WHEN to recompute vs serve the frozen file.

Simple rule (IST market clock; NSE closes 15:30, EOD data settles by ~16:00):

  * Before 16:00 IST — always refresh: regenerate the snapshot on load so
    intraday data shows.
  * At/after 16:00 IST — frozen: if the day's file exists, serve it as-is and
    never recompute on load. If NO data exists for the day (market holiday), the
    caller falls back to the previous working day's file.
  * Manual refresh (the Refresh button) — full delete & recreate, regardless of
    the clock (handled at /api/picks/refresh via generate_picks(force=True)).

Pure: takes `now` (IST-aware; a naive datetime is assumed IST) and reads no
clock unless `now` is omitted, so tests pass a fixed time and stay deterministic.

Fix point: EOD_CUTOFF — the single before/after boundary (default 16:00 IST).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

EOD_CUTOFF: time = time(16, 0)   # before -> always refresh; at/after -> frozen


def _now_ist(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def is_frozen(now: Optional[datetime] = None) -> bool:
    """True at/after 16:00 IST — the day's snapshot is frozen (serve as-is)."""
    return _now_ist(now).time() >= EOD_CUTOFF


def should_serve_cache(now: Optional[datetime] = None) -> bool:
    """Serve an existing snapshot as-is (True) or regenerate (False)?

    At/after 16:00 IST serve the cache; before that always regenerate so new
    intraday data is reflected. Only consulted when a valid cache exists.
    """
    return is_frozen(now)
