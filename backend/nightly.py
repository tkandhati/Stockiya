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

    # Durable daily position-monitoring trace. Records the latest volume
    # strength + any target alteration (stop raise / horizon extend) for every
    # open position, so "what did the monitor say on day D, and did it move a
    # target — and why?" is answerable months later. Purely additive: it logs
    # what positions_view already computed and commits no decision.
    log.info("Writing daily position-monitoring trace...")
    try:
        from backend.position_trace import append_daily_position_traces
        from backend.positions_view import list_active_positions
        from backend.yahoo import snapshot

        def _close(sym: str):
            try:
                return snapshot(sym).get("current")
            except Exception:
                return None

        positions = list_active_positions(_close)
        path = append_daily_position_traces(positions)
        log.info(
            "Wrote %d position traces to %s",
            len(positions), getattr(path, "name", path),
        )
    except Exception as e:
        log.exception("daily position trace failed (non-fatal)")
        errors.append(f"position_trace: {e}")

    # Outcome documentation. For every open pick that has reached a target
    # date (its own horizon and/or the tuner's 90/180 windows), append a
    # matured outcome row to data/traces/outcomes.jsonl. Catch-up + idempotent,
    # so a missed run day never loses an outcome. Guarded: a failure here must
    # not break the pipeline. (Previously run_outcome_tracker was never called
    # from anywhere — outcomes were recorded never; fixed 2026-07-24.)
    log.info("Recording matured pick outcomes...")
    try:
        from backend.stages.outcome import run_outcome_tracker, _default_asof_close

        # Same as-of close builder the standalone script uses (single source of
        # truth) — deterministic, prices each target as of its own date.
        osum = run_outcome_tracker(_default_asof_close)
        log.info("Outcome tracker: %s", osum)
    except Exception as e:
        log.exception("outcome tracker failed (non-fatal)")
        errors.append(f"outcome: {e}")

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
