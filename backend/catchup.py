"""Startup self-healing — detect missing artifacts and trigger backfill.

When the middleware boots (or you run `python -m backend.catchup` manually),
this module checks:

  1. `data/picks_<TODAY>.json` missing  → run the nightly orchestrator.
  2. Open picks have no weekly close for the most recent Friday → run weekly.

Run manually:
    python -m backend.catchup
"""

from __future__ import annotations

import logging
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
_PORTFOLIO_CSV = _DATA_DIR / "portfolio.csv"
_WEEKLY_CSV = _DATA_DIR / "portfolio_weekly.csv"


def _ist_today() -> date:
    return datetime.now(IST).date()


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def most_recent_friday(today: Optional[date] = None) -> date:
    today = today or _ist_today()
    while today.weekday() != 4:
        today -= timedelta(days=1)
    return today


def needs_nightly() -> bool:
    """True if today's picks file is missing on a trading day."""
    today = _ist_today()
    if not is_trading_day(today):
        return False
    today_file = _DATA_DIR / f"picks_{today.isoformat()}.json"
    return not today_file.exists()


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
    return bool(open_pick_ids - seen)


def _run_nightly() -> dict:
    log.info("Running nightly orchestrator...")
    from backend.nightly import run_nightly
    return run_nightly()


def _run_weekly() -> dict:
    log.info("Running weekly close orchestrator...")
    from backend.weekly import run_weekly
    return run_weekly()


def run_catchup() -> dict:
    """Top-level self-healing. Returns a summary dict of what was done.

    Persists the outcome to `data/.last_run.json` so the data-health probe
    can surface failures in the UI instead of leaving them buried in logs.
    """
    started = datetime.now(IST).isoformat(timespec="seconds")
    summary: dict = {"started_at": started}
    errors: list[str] = []

    if needs_nightly():
        log.info("Today's picks file missing — triggering nightly")
        try:
            summary["nightly"] = _run_nightly()
        except Exception as e:
            log.exception("nightly catchup failed")
            summary["nightly_error"] = str(e)
            errors.append(f"nightly: {e}")
    else:
        log.info("Picks file is current")
        summary["nightly"] = "skipped (current)"

    if needs_weekly_update():
        log.info("Weekly closes for open picks are stale — triggering weekly")
        try:
            summary["weekly"] = _run_weekly()
        except Exception as e:
            log.exception("weekly catchup failed")
            summary["weekly_error"] = str(e)
            errors.append(f"weekly: {e}")
    else:
        log.info("Weekly closes are current (or no open picks)")
        summary["weekly"] = "skipped (current)"

    finished = datetime.now(IST).isoformat(timespec="seconds")
    summary["finished_at"] = finished
    log.info("Catchup done: %s", {k: v for k, v in summary.items() if k != "nightly"})

    # Surface to /api/health/data — replaces previous silent-swallow behavior.
    try:
        from backend.data_health import record_run
        record_run(
            kind="catchup",
            ok=not errors,
            error="; ".join(errors),
            started_at=started,
            finished_at=finished,
        )
    except Exception:
        log.exception("data_health.record_run failed (non-fatal)")

    return summary


if __name__ == "__main__":
    run_catchup()
