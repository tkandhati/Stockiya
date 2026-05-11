"""Nightly orchestrator entry point.

Run as a cron / Windows Task Scheduler entry shortly after IST market close
(say, 19:00 IST):

    python -m backend.nightly

Internally just dispatches to `backend.orchestrator.run_universe()` — the
real work lives there. The output is `data/picks_<YYYY-MM-DD>.json` plus
per-ticker JSONL traces under `data/traces/`.
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

from backend.block_deals import fetch_and_cache_nse_deals  # noqa: E402
from backend.orchestrator import run_universe  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] nightly: %(message)s",
)
log = logging.getLogger("nightly")


def run_nightly() -> dict:
    """Refresh NSE deals, then run the volume-only pipeline over Nifty 100."""
    log.info("Refreshing NSE block + bulk deals...")
    try:
        fetch_and_cache_nse_deals()
    except Exception:
        log.exception("NSE deal refresh failed (continuing with cached data)")

    log.info("Running pipeline...")
    response = run_universe()
    log.info("Done. Wrote %d picks to data/picks_%s.json",
             len(response.get("picks", [])), response.get("date"))
    return response


if __name__ == "__main__":
    run_nightly()
