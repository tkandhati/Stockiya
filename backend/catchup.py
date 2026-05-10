"""Startup self-healing — detect and fix missing data files.

When the middleware boots (or the user runs `python -m backend.catchup`), this
module checks what data is current and what's stale, then triggers the right
backfill:

  1. `data/prepared/<TODAY>/` missing  → run the nightly orchestrator.
  2. Open picks have no weekly close for the most recent Friday → run weekly.
  3. Block-deal cache stale (TODO when live downloader is wired) → refresh.

What it does NOT do (deliberately):
  - Backfill historical `data/prepared/<DATE>/` for past days. Yahoo gives us
    "data ending today", so we can't reconstruct yesterday's snapshot. Only
    today's view is meaningful.
  - Block on app startup. Catchup runs in a background thread; the API serves
    immediately and self-heals as data lands.

Run manually:
    python -m backend.catchup
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / "backend" / ".env")

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] catchup: %(message)s",
)
log = logging.getLogger("catchup")

_DATA_DIR = _PROJECT_ROOT / "data"
_PREPARED_DIR = _DATA_DIR / "prepared"
_PORTFOLIO_CSV = _DATA_DIR / "portfolio.csv"
_WEEKLY_CSV = _DATA_DIR / "portfolio_weekly.csv"


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #

def _ist_today() -> date:
    return datetime.now(IST).date()


def is_trading_day(d: date) -> bool:
    """Mon-Fri only. Indian market holidays are NOT in this calendar — they'll
    appear as 'failed nightly' which is fine for catchup purposes (we just
    won't try to backfill them).
    """
    return d.weekday() < 5


def most_recent_friday(today: Optional[date] = None) -> date:
    today = today or _ist_today()
    while today.weekday() != 4:  # 4 = Friday
        today -= timedelta(days=1)
    return today


# --------------------------------------------------------------------------- #
# State checks
# --------------------------------------------------------------------------- #

def latest_prepared_date() -> Optional[date]:
    """Most recent `data/prepared/<DATE>/` directory that has a manifest."""
    if not _PREPARED_DIR.exists():
        return None
    candidates: list[date] = []
    for child in _PREPARED_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            d = date.fromisoformat(child.name)
        except ValueError:
            continue
        if (child / "_manifest.json").exists():
            candidates.append(d)
    return max(candidates) if candidates else None


def needs_nightly() -> bool:
    """True if today's prepared/ directory is missing or has no manifest."""
    today = _ist_today()
    if not is_trading_day(today):
        return False  # don't backfill weekends
    today_dir = _PREPARED_DIR / today.isoformat()
    return not (today_dir / "_manifest.json").exists()


def needs_weekly_update() -> bool:
    """True if there are open picks AND the most recent Friday hasn't been
    recorded in portfolio_weekly.csv yet."""
    if not _PORTFOLIO_CSV.exists():
        return False
    import csv
    open_pick_ids: set[str] = set()
    with _PORTFOLIO_CSV.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status") == "open":
                open_pick_ids.add(row["pick_id"])
    if not open_pick_ids:
        return False

    last_friday = most_recent_friday()
    # If today is Friday past market close (after 16:00 IST) treat that as
    # the relevant Friday; otherwise the previous Friday.
    today = _ist_today()
    if today == last_friday and datetime.now(IST).hour < 16:
        last_friday -= timedelta(days=7)

    if not _WEEKLY_CSV.exists():
        return True

    last_friday_iso = last_friday.isoformat()
    seen: set[str] = set()
    with _WEEKLY_CSV.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("week_ending") == last_friday_iso:
                seen.add(row["pick_id"])
    # Need an update if any open pick is missing a row for the last Friday.
    return bool(open_pick_ids - seen)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #

def _run_nightly() -> dict:
    log.info("Running nightly orchestrator...")
    from backend.nightly import run_nightly
    return run_nightly()


def _run_weekly() -> dict:
    log.info("Running weekly close orchestrator...")
    from backend.weekly import run_weekly
    return run_weekly()


def run_catchup() -> dict:
    """Top-level self-healing. Returns a summary dict of what was done."""
    summary: dict = {"started_at": datetime.now(IST).isoformat(timespec="seconds")}

    # 1. Check / run nightly
    if needs_nightly():
        log.info("Today's prepared/ missing — triggering nightly")
        try:
            summary["nightly"] = _run_nightly()
        except Exception as e:
            log.exception("nightly catchup failed")
            summary["nightly_error"] = str(e)
    else:
        latest = latest_prepared_date()
        log.info("Prepared data current (latest = %s)", latest)
        summary["nightly"] = "skipped (current)"

    # 2. Check / run weekly
    if needs_weekly_update():
        log.info("Weekly closes for open picks are stale — triggering weekly")
        try:
            summary["weekly"] = _run_weekly()
        except Exception as e:
            log.exception("weekly catchup failed")
            summary["weekly_error"] = str(e)
    else:
        log.info("Weekly closes are current (or no open picks)")
        summary["weekly"] = "skipped (current)"

    summary["finished_at"] = datetime.now(IST).isoformat(timespec="seconds")
    log.info("Catchup done: %s", {k: v for k, v in summary.items() if k != "nightly"})
    return summary


if __name__ == "__main__":
    run_catchup()
