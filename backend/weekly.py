"""Weekly close orchestrator.

Run every Friday after the IST market close (~16:00 IST):
    python -m backend.weekly

For every pick currently `open` in `data/portfolio.csv`:
  1. Fetch this week's close price.
  2. Append a row to `data/portfolio_weekly.csv` (the timeseries).
  3. If close >= target_price → mark `target_hit`, set exit fields.
     If close <= stop_price   → mark `stopped`.
     If today > target_max_date → mark `timed_out`.
  4. Save the updated portfolio.csv.

Schedule via Windows Task Scheduler / cron (Fri 16:30 IST).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / "backend" / ".env")

from backend.portfolio import update_open_picks  # noqa: E402
from backend.fetch import fetch_snapshot  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] weekly: %(message)s",
)
log = logging.getLogger("weekly")


def _close_for(symbol: str) -> float | None:
    """Caller-supplied price fetcher. Uses the configured data source."""
    snap = fetch_snapshot(symbol)
    return snap.get("current")


def run_weekly() -> dict:
    log.info("Starting weekly close update")
    summary = update_open_picks(_close_for)
    log.info("Done: %s", summary)
    return summary


if __name__ == "__main__":
    run_weekly()
