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
    """Refresh NSE deals, then run the volume-only pipeline over Nifty 100.

    Persists outcome to `data/.last_run.json` so the /api/health/data probe
    can surface failures in the UI (replaces silent-log-only behavior).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    started = datetime.now(IST).isoformat(timespec="seconds")
    errors: list[str] = []

    log.info("Refreshing NSE block + bulk deals...")
    try:
        fetch_and_cache_nse_deals()
    except Exception as e:
        log.exception("NSE deal refresh failed (continuing with cached data)")
        errors.append(f"nse_deals: {e}")

    log.info("Running pipeline...")
    try:
        response = run_universe()
    except Exception as e:
        log.exception("pipeline run failed")
        errors.append(f"pipeline: {e}")
        response = {"picks": [], "date": None, "error": str(e)}
    log.info("Done. Wrote %d picks to data/picks_%s.json",
             len(response.get("picks", [])), response.get("date"))

    finished = datetime.now(IST).isoformat(timespec="seconds")
    try:
        from backend.data_health import record_run
        record_run(
            kind="nightly",
            ok=not errors,
            error="; ".join(errors),
            started_at=started,
            finished_at=finished,
            extras={"picks": len(response.get("picks", []))},
        )
    except Exception:
        log.exception("data_health.record_run failed (non-fatal)")

    return response


if __name__ == "__main__":
    run_nightly()
