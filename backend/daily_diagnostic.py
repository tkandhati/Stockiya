"""Daily self-contained diagnostic snapshot.

Writes ONE file — `data/daily_diagnostic.md` — that overwrites itself
on every pipeline run. When uploaded to a diagnostic session, it gives
enough context to answer "why isn't X working" without needing to see
any other file.

Contents (in order):

  1. HEADER            — timestamp, IST date, git commit, python version
  2. CODE FINGERPRINTS — verifies which portfolio.py is actually loaded
                         and which schema version the pipeline emitted
  3. PIPELINE RUN      — universe count, survivors, picks visible / suppressed
  4. RECONCILIATION    — reconcile counts and suppression events
  5. PORTFOLIO STATE   — total rows, by-status breakdown, open positions
                         table, duplicate-symbol detection
  6. PICKS JSON        — schema_version, per-pick summary of the new
                         schema-v6 fields (holding_horizon, already_held,
                         change_since_prev_pick, suppressed_from_ui)
  7. ERRORS            — any exceptions captured during the run

Fail-open: any error in this module is swallowed and logged. Diagnostic
writing must never take down the pipeline.

Fix points:
    DIAGNOSTIC_PATH  — target file. Change if you want to keep history.
"""

from __future__ import annotations

import csv
import json
import logging
import platform
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIAGNOSTIC_PATH = _PROJECT_ROOT / "data" / "daily_diagnostic.md"

log = logging.getLogger("daily_diagnostic")


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), "log", "-1", "--format=%h %s"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "(git output empty)"
    except Exception as e:
        return f"(git error: {e})"


def _module_fingerprint() -> dict[str, Any]:
    """Return code-provenance info: which files, what versions loaded."""
    fp: dict[str, Any] = {}
    try:
        from . import portfolio as _portfolio
        fp["portfolio_file"] = getattr(_portfolio, "__file__", "?")
        fp["portfolio_fields_len"] = len(_portfolio.PORTFOLIO_FIELDS)
        fp["portfolio_fields_has_end_date"] = "end_date" in _portfolio.PORTFOLIO_FIELDS
        fp["portfolio_fields_has_horizon_days"] = "horizon_days" in _portfolio.PORTFOLIO_FIELDS
        fp["portfolio_fields_has_superseded_by"] = "superseded_by" in _portfolio.PORTFOLIO_FIELDS
        fp["record_picks_has_supersede_logic"] = (
            "superseded" in (getattr(_portfolio.record_picks, "__doc__", "") or "")
        )
    except Exception as e:
        fp["portfolio_load_error"] = str(e)

    try:
        from .horizon import HORIZON_BUCKETS
        fp["horizon_buckets"] = list(HORIZON_BUCKETS)
    except Exception as e:
        fp["horizon_load_error"] = str(e)

    try:
        from .stages.render import PICKS_SCHEMA_VERSION
        fp["picks_schema_version_in_code"] = PICKS_SCHEMA_VERSION
    except Exception as e:
        fp["render_load_error"] = str(e)

    try:
        from . import picks_reconcile as _rec
        fp["picks_reconcile_file"] = getattr(_rec, "__file__", "?")
    except Exception as e:
        fp["reconcile_load_error"] = str(e)

    return fp


def _portfolio_summary() -> dict[str, Any]:
    """Read portfolio.csv and summarize its state."""
    p = _PROJECT_ROOT / "data" / "portfolio.csv"
    summary: dict[str, Any] = {"path": str(p), "exists": p.exists()}
    if not p.exists():
        return summary
    try:
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            rows = list(reader)
    except Exception as e:
        summary["read_error"] = str(e)
        return summary

    summary["header_columns"] = len(headers)
    summary["header_has_end_date"] = "end_date" in headers
    summary["header_has_horizon_days"] = "horizon_days" in headers
    summary["header_has_superseded_by"] = "superseded_by" in headers
    summary["total_rows"] = len(rows)

    by_status: dict[str, int] = {}
    open_rows: list[dict[str, Any]] = []
    open_by_symbol: dict[str, int] = {}
    for r in rows:
        st = r.get("status", "") or "(blank)"
        by_status[st] = by_status.get(st, 0) + 1
        if st in ("open", "partial_t1"):
            sym = r.get("symbol", "")
            open_by_symbol[sym] = open_by_symbol.get(sym, 0) + 1
            open_rows.append({
                "pick_id": r.get("pick_id"),
                "symbol": sym,
                "entry_date": r.get("entry_date"),
                "ownership": r.get("ownership") or "(blank)",
                "end_date": r.get("end_date") or "(blank)",
                "horizon_days": r.get("horizon_days") or "(blank)",
                "horizon_source": r.get("horizon_source") or "(blank)",
                "superseded_by": r.get("superseded_by") or "",
                "status": st,
                "last_updated": r.get("last_updated"),
            })

    summary["by_status"] = by_status
    summary["open_positions"] = open_rows
    summary["duplicate_open_symbols"] = {
        sym: n for sym, n in open_by_symbol.items() if n > 1
    }
    return summary


def _picks_json_summary(today_iso: str) -> dict[str, Any]:
    p = _PROJECT_ROOT / "data" / f"picks_{today_iso}.json"
    summary: dict[str, Any] = {"path": str(p), "exists": p.exists()}
    if not p.exists():
        return summary
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        summary["read_error"] = str(e)
        return summary
    summary["schema_version"] = payload.get("schema_version")
    summary["generated_at"] = payload.get("generated_at")
    summary["regime_passed"] = (payload.get("regime") or {}).get("passed")
    picks = payload.get("picks") or []
    summary["picks_count"] = len(picks)
    picks_detail: list[dict[str, Any]] = []
    for pk in picks:
        picks_detail.append({
            "symbol": pk.get("symbol"),
            "rank": pk.get("rank"),
            "has_holding_horizon": bool(pk.get("holding_horizon")),
            "horizon_days": (pk.get("holding_horizon") or {}).get("days"),
            "has_already_held": bool(pk.get("already_held")),
            "has_change_since_prev_pick": bool(pk.get("change_since_prev_pick")),
            "prev_pick_date": (
                (pk.get("change_since_prev_pick") or {}).get("prev_date")
            ),
            "has_suppressed_from_ui": bool(pk.get("suppressed_from_ui")),
        })
    summary["picks"] = picks_detail
    return summary


def _reconcile_events(today_iso: str) -> list[dict[str, Any]]:
    """Scan today's trace files for PICKS_RECONCILE suppression events."""
    traces_dir = _PROJECT_ROOT / "data" / "traces"
    if not traces_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    pattern = f"run_{today_iso}_"
    for f in traces_dir.iterdir():
        if not f.name.startswith(pattern) or not f.name.endswith(".jsonl"):
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("stage") == "PICKS_RECONCILE":
                    events.append(row)
        except OSError:
            continue
    return events


def write_daily_diagnostic(
    today_iso: str,
    orchestrator_summary: Optional[dict[str, Any]] = None,
    errors: Optional[list[str]] = None,
) -> Optional[Path]:
    """Write the diagnostic snapshot. Returns the file path on success,
    None on failure. Never raises."""
    try:
        now = datetime.now(IST).isoformat(timespec="seconds")
        fingerprints = _module_fingerprint()
        portfolio = _portfolio_summary()
        picks = _picks_json_summary(today_iso)
        rec_events = _reconcile_events(today_iso)

        lines: list[str] = []
        add = lines.append

        add(f"# Stockya daily diagnostic — {today_iso}")
        add("")
        add(f"Generated: {now}")
        add(f"Overwrites previous file at each pipeline run.")
        add("")

        add("## 1. Environment")
        add("")
        add(f"- Python: `{platform.python_version()}` on `{platform.system()} {platform.release()}`")
        add(f"- Git HEAD: `{_git_commit()}`")
        add(f"- Executable: `{sys.executable}`")
        add(f"- Project root: `{_PROJECT_ROOT}`")
        add("")

        add("## 2. Code fingerprints (which code is actually loaded)")
        add("")
        add("```json")
        add(json.dumps(fingerprints, indent=2))
        add("```")
        add("")

        add("## 3. Pipeline run summary")
        add("")
        if orchestrator_summary:
            add("```json")
            add(json.dumps(orchestrator_summary, indent=2, default=str))
            add("```")
        else:
            add("_(orchestrator_summary not provided; run may have crashed before reaching this step)_")
        add("")

        add("## 4. Reconciliation events (from trace JSONLs)")
        add("")
        if rec_events:
            add(f"Total suppression events today: **{len(rec_events)}**")
            add("")
            add("```json")
            add(json.dumps(rec_events, indent=2))
            add("```")
        else:
            add("_No PICKS_RECONCILE events in today's traces. Either nothing was suppressed, or the reconcile module didn't run._")
        add("")

        add("## 5. Portfolio state (data/portfolio.csv)")
        add("")
        add("### 5a. Header + summary")
        add("")
        add("```json")
        add(json.dumps({
            k: v for k, v in portfolio.items()
            if k not in ("open_positions", "duplicate_open_symbols")
        }, indent=2))
        add("```")
        add("")

        add("### 5b. Open positions detail")
        add("")
        open_positions = portfolio.get("open_positions", [])
        if open_positions:
            add("| pick_id | symbol | entry_date | ownership | status | end_date | horizon_days | superseded_by |")
            add("|---|---|---|---|---|---|---|---|")
            for r in open_positions:
                add(
                    f"| {r['pick_id']} | {r['symbol']} | {r['entry_date']} | "
                    f"{r['ownership']} | {r['status']} | {r['end_date']} | "
                    f"{r['horizon_days']} | {r['superseded_by']} |"
                )
        else:
            add("_No open positions._")
        add("")

        add("### 5c. Duplicate open symbols (>1 open row for same symbol)")
        add("")
        dups = portfolio.get("duplicate_open_symbols", {})
        if dups:
            add("**These indicate record_picks did NOT supersede correctly:**")
            add("")
            add("```json")
            add(json.dumps(dups, indent=2))
            add("```")
        else:
            add("_None. Every open symbol has exactly one open row._")
        add("")

        add(f"## 6. Picks JSON (data/picks_{today_iso}.json)")
        add("")
        add("```json")
        add(json.dumps(picks, indent=2))
        add("```")
        add("")

        add("## 7. Errors captured")
        add("")
        if errors:
            for e in errors:
                add(f"- `{e}`")
        else:
            add("_No errors captured by orchestrator this run._")
        add("")

        add("---")
        add(
            "**How to use:** upload this file to the diagnostic session. "
            "It is fully self-contained; no additional files are needed to "
            "reconstruct code state, pipeline run, portfolio state, and picks."
        )
        add("")

        DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIAGNOSTIC_PATH.write_text("\n".join(lines), encoding="utf-8")
        log.info("daily diagnostic written to %s", DIAGNOSTIC_PATH.name)
        return DIAGNOSTIC_PATH
    except Exception:
        log.exception("daily_diagnostic write failed")
        try:
            # Best-effort: at least dump the traceback so upload is still useful
            DIAGNOSTIC_PATH.write_text(
                f"# Diagnostic write failed at {datetime.now(IST).isoformat()}\n\n"
                f"```\n{traceback.format_exc()}\n```\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        return None
