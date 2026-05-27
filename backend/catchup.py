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


def list_missing_trading_days(today: Optional[date] = None, max_lookback: int = 30) -> list[date]:
    """All trading days between the last picks file on disk and `today`
    that have no corresponding `picks_<date>.json`.

    `max_lookback` caps how far back we'll backfill (default 30 trading days
    of catch-up — enough for a month-long gap, prevents the first run on a
    new install from scanning a year of history).
    """
    today = today or _ist_today()
    files = sorted(_DATA_DIR.glob("picks_*.json"))

    # Find the most recent picks file date
    last_date: Optional[date] = None
    for f in reversed(files):
        try:
            last_date = date.fromisoformat(f.stem.replace("picks_", ""))
            break
        except ValueError:
            continue

    if last_date is None:
        # No history at all — only the current trading day (no deep backfill)
        return [today] if is_trading_day(today) else []

    missing: list[date] = []
    cur = last_date + timedelta(days=1)
    while cur <= today:
        if is_trading_day(cur):
            missing.append(cur)
        cur += timedelta(days=1)

    # Cap the backfill so a long absence doesn't trigger a year of compute
    if len(missing) > max_lookback:
        missing = missing[-max_lookback:]
        log.warning(
            "Catchup: capping backfill at the most recent %d trading days "
            "(full gap was %d days)", max_lookback, len(missing),
        )
    return missing


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
    log.info("Running nightly orchestrator (today)...")
    from backend.nightly import run_nightly
    return run_nightly()


def _run_nightly_for(d: date) -> dict:
    """Run the orchestrator for a specific past date (backfill).

    Skips the NSE deal download (which is always 'today') because historical
    deal data may already be cached and re-fetching adds nothing.
    """
    log.info("Backfilling pipeline for %s...", d.isoformat())
    from backend.orchestrator import run_universe
    return run_universe(today_iso=d.isoformat())


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

    # ---- One-shot NSE deal refresh (covers ALL backfill days; the CSVs are
    # rolling ~3 months, so we download once even when walking many days).
    # Without this the DD bonus signal silently scores 0 for every ticker.
    import os as _os
    if _os.environ.get("DEMO_MODE", "0") != "1":
        try:
            from backend.block_deals import fetch_and_cache_nse_deals
            log.info("Refreshing NSE block + bulk deals (one-shot, before backfill)...")
            fetch_and_cache_nse_deals()
            summary["nse_deals"] = "ok"
        except Exception as e:
            log.exception("NSE deal refresh failed (continuing with cached data if present)")
            summary["nse_deals_error"] = str(e)
            errors.append(f"nse_deals: {e}")
    else:
        summary["nse_deals"] = "skipped (DEMO_MODE)"

    # Walk every missing trading day, not just today.
    missing_days = list_missing_trading_days()
    if missing_days:
        log.info("Catchup: %d missing trading days (%s -> %s)",
                 len(missing_days),
                 missing_days[0].isoformat(),
                 missing_days[-1].isoformat())
        summary["missing_days"] = [d.isoformat() for d in missing_days]
        nightly_results: dict[str, dict] = {}
        for d in missing_days:
            try:
                resp = _run_nightly_for(d)
                nightly_results[d.isoformat()] = {
                    "picks": len(resp.get("picks", [])),
                    "regime_passed": resp.get("regime", {}).get("passed"),
                }
            except Exception as e:
                log.exception("backfill for %s failed", d.isoformat())
                nightly_results[d.isoformat()] = {"error": str(e)}
                errors.append(f"nightly {d.isoformat()}: {e}")
        summary["nightly"] = nightly_results
    else:
        log.info("Picks file is current — no backfill needed")
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
