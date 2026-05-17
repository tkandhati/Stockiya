"""Data-health probe — checks that every prerequisite file is present and valid.

The screener depends on a small set of files that get refreshed at different
cadences (initial, daily, weekly). Until now, missing or stale files were
silently tolerated: e.g. an empty `data/deals/` directory meant the Direct
Deals stage scored 0 for every ticker — invisible from the UI.

This module makes those failures *visible*:

  probe() -> DataHealthReport

The middleware exposes the report at `GET /api/health/data` and the frontend
renders it as a compact tile on the main screen + a drill-down detail page.

Design notes:
- Pure-Python, no third-party deps beyond stdlib + zoneinfo.
- Idempotent and side-effect-free. Just reads the filesystem.
- For each file: classify as ok / warn / error and emit the *exact* fix command.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

Status = Literal["ok", "warn", "error"]
Overall = Literal["green", "yellow", "red"]

IST = ZoneInfo("Asia/Kolkata")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_DEALS_DIR = _DATA_DIR / "deals"
_TRACES_DIR = _DATA_DIR / "traces"
_LAST_RUN_FILE = _DATA_DIR / ".last_run.json"


@dataclass
class HealthItem:
    id: str
    label: str
    path: str
    status: Status
    detail: str
    fix: Optional[str] = None
    last_modified: Optional[str] = None  # ISO timestamp, IST


@dataclass
class HealthGroup:
    name: str
    items: list[HealthItem] = field(default_factory=list)


@dataclass
class DataHealthReport:
    overall: Overall
    checked_at: str
    summary: dict
    groups: list[HealthGroup]

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "checked_at": self.checked_at,
            "summary": self.summary,
            "groups": [
                {
                    "name": g.name,
                    "items": [
                        {
                            "id": i.id,
                            "label": i.label,
                            "path": i.path,
                            "status": i.status,
                            "detail": i.detail,
                            "fix": i.fix,
                            "last_modified": i.last_modified,
                        }
                        for i in g.items
                    ],
                }
                for g in self.groups
            ],
        }


# --------------------------------------------------------------------------- #
# Per-file probes
# --------------------------------------------------------------------------- #

def _ist_today() -> date:
    return datetime.now(IST).date()


def _ist_iso_now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _mtime_iso(p: Path) -> Optional[str]:
    if not p.exists():
        return None
    return datetime.fromtimestamp(p.stat().st_mtime, tz=IST).isoformat(timespec="seconds")


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def _most_recent_trading_day(today: Optional[date] = None) -> date:
    d = today or _ist_today()
    while not _is_trading_day(d):
        d -= timedelta(days=1)
    return d


def _check_universe() -> HealthItem:
    """Universe is in-code; verify import + count."""
    try:
        from .universe import UNIVERSE
        n = len(UNIVERSE)
    except Exception as e:
        return HealthItem(
            id="universe", label="Nifty 100 universe list",
            path="backend/universe.py",
            status="error", detail=f"import failed: {e}",
            fix="Check backend/universe.py exists and is syntactically valid.",
        )
    if n < 90:
        return HealthItem(
            id="universe", label="Nifty 100 universe list",
            path="backend/universe.py",
            status="warn", detail=f"only {n} symbols (expected ~100)",
            fix="Refresh backend/universe.py UNIVERSE list.",
        )
    return HealthItem(
        id="universe", label="Nifty 100 universe list",
        path="backend/universe.py",
        status="ok", detail=f"{n} symbols",
    )


def _check_env() -> HealthItem:
    """Optional .env file — present but contents not validated."""
    env_path = _PROJECT_ROOT / "backend" / ".env"
    if env_path.exists():
        return HealthItem(
            id="env_file", label="backend/.env",
            path="backend/.env",
            status="ok",
            detail=(
                f"DEMO_MODE={os.environ.get('DEMO_MODE', '0')} · "
                f"DATA_SOURCE={os.environ.get('DATA_SOURCE', 'yahoo')}"
            ),
            last_modified=_mtime_iso(env_path),
        )
    return HealthItem(
        id="env_file", label="backend/.env",
        path="backend/.env",
        status="warn",
        detail="missing — defaults will be used (DATA_SOURCE=yahoo, DEMO_MODE=0)",
        fix="cp backend/.env.example backend/.env  (then edit as needed)",
    )


def _check_picks_today() -> HealthItem:
    today = _ist_today()
    expected = _DATA_DIR / f"picks_{today.isoformat()}.json"
    rel = expected.relative_to(_PROJECT_ROOT).as_posix()

    if expected.exists():
        try:
            with expected.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            n_picks = len(payload.get("picks", []))
        except Exception as e:
            return HealthItem(
                id="picks_today", label="Today's picks file",
                path=rel, status="error",
                detail=f"file exists but unreadable: {e}",
                fix="python -m backend.nightly",
                last_modified=_mtime_iso(expected),
            )
        return HealthItem(
            id="picks_today", label="Today's picks file",
            path=rel, status="ok",
            detail=f"{n_picks} pick(s)",
            last_modified=_mtime_iso(expected),
        )

    # not present today — look for the most recent picks file
    candidates = sorted(_DATA_DIR.glob("picks_*.json"), reverse=True)
    if not candidates:
        return HealthItem(
            id="picks_today", label="Today's picks file",
            path=rel, status="error",
            detail="no picks file has ever been written",
            fix="python -m backend.nightly",
        )
    latest = candidates[0]
    # parse date from filename: picks_YYYY-MM-DD.json
    try:
        latest_date = date.fromisoformat(latest.stem.replace("picks_", ""))
        age_days = (today - latest_date).days
    except ValueError:
        age_days = -1

    status: Status = "warn" if 0 <= age_days <= 1 else "error"
    if not _is_trading_day(today) and age_days <= 3:
        status = "warn"
    return HealthItem(
        id="picks_today", label="Today's picks file",
        path=rel, status=status,
        detail=f"missing — latest is {latest.name} ({age_days}d old)",
        fix="python -m backend.nightly",
        last_modified=_mtime_iso(latest),
    )


def _check_deal_csv(name: str, label: str, item_id: str) -> HealthItem:
    p = _DEALS_DIR / name
    rel = p.relative_to(_PROJECT_ROOT).as_posix()

    if not p.exists():
        return HealthItem(
            id=item_id, label=label, path=rel,
            status="error",
            detail="missing — DD stage scores 0 for every ticker until this is populated",
            fix="python -m backend.block_deals  (or run start.bat — it triggers catchup on boot)",
        )

    size = p.stat().st_size
    if size == 0:
        return HealthItem(
            id=item_id, label=label, path=rel, status="error",
            detail="0 bytes — NSE download likely failed",
            fix="python -m backend.block_deals",
            last_modified=_mtime_iso(p),
        )

    # Count rows (cheap — these CSVs are small)
    try:
        with p.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            rows = sum(1 for _ in f) - 1  # subtract header
    except Exception as e:
        return HealthItem(
            id=item_id, label=label, path=rel, status="warn",
            detail=f"unreadable: {e}", fix="python -m backend.block_deals",
            last_modified=_mtime_iso(p),
        )

    age_days = (datetime.now(IST).date() - datetime.fromtimestamp(
        p.stat().st_mtime, tz=IST).date()).days
    status: Status = "ok" if age_days <= 1 else "warn"
    detail = f"{max(rows, 0)} rows · refreshed {age_days}d ago"
    fix = None if status == "ok" else "python -m backend.block_deals"
    return HealthItem(
        id=item_id, label=label, path=rel, status=status,
        detail=detail, fix=fix, last_modified=_mtime_iso(p),
    )


def _check_deals_merged() -> HealthItem:
    p = _DEALS_DIR / "all.csv"
    rel = p.relative_to(_PROJECT_ROOT).as_posix()

    if not p.exists():
        return HealthItem(
            id="deals_merged", label="Merged deals (all.csv)",
            path=rel, status="error",
            detail="missing — aggregate_30d returns 0 for every symbol",
            fix="python -m backend.block_deals",
        )

    try:
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        return HealthItem(
            id="deals_merged", label="Merged deals (all.csv)",
            path=rel, status="error",
            detail=f"unreadable: {e}", fix="python -m backend.block_deals",
            last_modified=_mtime_iso(p),
        )

    if not rows:
        return HealthItem(
            id="deals_merged", label="Merged deals (all.csv)",
            path=rel, status="error",
            detail="0 rows after merge — DD stage will score 0",
            fix="python -m backend.block_deals",
            last_modified=_mtime_iso(p),
        )

    # find newest date in file
    newest: Optional[date] = None
    for r in rows:
        try:
            d = date.fromisoformat(r.get("date", ""))
        except ValueError:
            continue
        if newest is None or d > newest:
            newest = d
    today = _ist_today()
    if newest is None:
        return HealthItem(
            id="deals_merged", label="Merged deals (all.csv)",
            path=rel, status="warn",
            detail=f"{len(rows)} rows but no parseable dates",
            fix="python -m backend.block_deals",
            last_modified=_mtime_iso(p),
        )
    age_days = (today - newest).days
    status: Status = "ok" if age_days <= 2 else "warn"
    return HealthItem(
        id="deals_merged", label="Merged deals (all.csv)",
        path=rel,
        status=status,
        detail=f"{len(rows)} rows · newest deal {newest.isoformat()} ({age_days}d old)",
        fix=None if status == "ok" else "python -m backend.block_deals",
        last_modified=_mtime_iso(p),
    )


def _check_traces_today() -> HealthItem:
    today_iso = _ist_today().isoformat()
    pattern = f"run_{today_iso}_*.jsonl"
    matches = list(_TRACES_DIR.glob(pattern)) if _TRACES_DIR.exists() else []
    n = len(matches)
    rel = f"data/traces/{pattern}"

    if n == 0:
        # Look back to most recent trace
        all_traces = sorted(_TRACES_DIR.glob("run_*.jsonl"), reverse=True) \
            if _TRACES_DIR.exists() else []
        if not all_traces:
            return HealthItem(
                id="traces_today", label="Per-ticker traces (today)",
                path=rel, status="warn",
                detail="0/100 — no traces ever written",
                fix="python -m backend.nightly",
            )
        latest = all_traces[0]
        return HealthItem(
            id="traces_today", label="Per-ticker traces (today)",
            path=rel, status="warn",
            detail=f"0/100 today · latest trace is {latest.name}",
            fix="python -m backend.nightly",
            last_modified=_mtime_iso(latest),
        )

    if n < 90:
        return HealthItem(
            id="traces_today", label="Per-ticker traces (today)",
            path=rel, status="warn",
            detail=f"{n}/100 — partial run, some tickers may have crashed",
            fix="python -m backend.nightly",
        )

    return HealthItem(
        id="traces_today", label="Per-ticker traces (today)",
        path=rel, status="ok",
        detail=f"{n}/100 traces present",
    )


def _check_portfolio() -> HealthItem:
    p = _DATA_DIR / "portfolio.csv"
    rel = "data/portfolio.csv"
    if not p.exists():
        return HealthItem(
            id="portfolio", label="Portfolio ledger",
            path=rel, status="warn",
            detail="not created yet — appears after first nightly with picks",
            fix="python -m backend.nightly",
        )
    try:
        with p.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        return HealthItem(
            id="portfolio", label="Portfolio ledger",
            path=rel, status="error",
            detail=f"unreadable: {e}", fix="Restore data/portfolio.csv from git or backup.",
            last_modified=_mtime_iso(p),
        )
    open_n = sum(1 for r in rows if r.get("status") == "open")
    closed_n = sum(1 for r in rows if r.get("status") not in (None, "", "open"))
    return HealthItem(
        id="portfolio", label="Portfolio ledger",
        path=rel, status="ok",
        detail=f"{open_n} open · {closed_n} closed",
        last_modified=_mtime_iso(p),
    )


def _check_portfolio_weekly() -> HealthItem:
    p = _DATA_DIR / "portfolio_weekly.csv"
    rel = "data/portfolio_weekly.csv"
    if not p.exists():
        return HealthItem(
            id="portfolio_weekly", label="Weekly close timeseries",
            path=rel, status="warn",
            detail="not created yet — appears after first weekly run with open picks",
            fix="python -m backend.weekly",
        )
    try:
        with p.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        return HealthItem(
            id="portfolio_weekly", label="Weekly close timeseries",
            path=rel, status="error",
            detail=f"unreadable: {e}",
            fix="Restore data/portfolio_weekly.csv from git or backup.",
            last_modified=_mtime_iso(p),
        )
    weeks = {r.get("week_ending", "") for r in rows if r.get("week_ending")}
    latest = max(weeks) if weeks else None
    if not latest:
        return HealthItem(
            id="portfolio_weekly", label="Weekly close timeseries",
            path=rel, status="warn",
            detail=f"{len(rows)} rows but no week_ending values",
            fix="python -m backend.weekly",
            last_modified=_mtime_iso(p),
        )
    return HealthItem(
        id="portfolio_weekly", label="Weekly close timeseries",
        path=rel, status="ok",
        detail=f"{len(rows)} rows · last week {latest}",
        last_modified=_mtime_iso(p),
    )


def _check_last_run() -> HealthItem:
    """Surface the result of the most recent nightly/catchup run.

    Written by `backend/catchup.py:run_catchup()` and
    `backend/orchestrator.py:run_universe()`. Replaces the previous behavior
    where errors got buried in logs only.
    """
    if not _LAST_RUN_FILE.exists():
        return HealthItem(
            id="last_run", label="Most recent run",
            path="data/.last_run.json", status="warn",
            detail="no run recorded yet (pipeline hasn't executed since this probe shipped)",
            fix="python -m backend.nightly",
        )
    try:
        with _LAST_RUN_FILE.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return HealthItem(
            id="last_run", label="Most recent run",
            path="data/.last_run.json", status="warn",
            detail=f"unreadable: {e}", fix=None,
            last_modified=_mtime_iso(_LAST_RUN_FILE),
        )

    ok = bool(payload.get("ok"))
    kind = payload.get("kind") or "run"
    when = payload.get("finished_at") or payload.get("started_at") or "?"
    err = payload.get("error") or ""
    if ok:
        return HealthItem(
            id="last_run", label="Most recent run",
            path="data/.last_run.json", status="ok",
            detail=f"{kind} succeeded at {when}",
            last_modified=_mtime_iso(_LAST_RUN_FILE),
        )
    return HealthItem(
        id="last_run", label="Most recent run",
        path="data/.last_run.json", status="error",
        detail=f"{kind} FAILED at {when}: {err[:200]}",
        fix="See backend logs; re-run with: python -m backend.nightly",
        last_modified=_mtime_iso(_LAST_RUN_FILE),
    )


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #

def _roll_up(items: list[HealthItem]) -> Overall:
    """Worst child wins. error→red, warn→yellow, all ok→green."""
    if any(i.status == "error" for i in items):
        return "red"
    if any(i.status == "warn" for i in items):
        return "yellow"
    return "green"


def probe() -> DataHealthReport:
    """Run all checks and return the report. No side effects."""
    initial = HealthGroup(name="Initial (one-time)")
    initial.items.append(_check_universe())
    initial.items.append(_check_env())

    daily = HealthGroup(name="Daily")
    daily.items.append(_check_picks_today())
    daily.items.append(_check_deal_csv("block.csv", "NSE block deals", "nse_block"))
    daily.items.append(_check_deal_csv("bulk.csv", "NSE bulk deals", "nse_bulk"))
    daily.items.append(_check_deals_merged())
    daily.items.append(_check_traces_today())

    weekly = HealthGroup(name="Weekly")
    weekly.items.append(_check_portfolio())
    weekly.items.append(_check_portfolio_weekly())

    recent = HealthGroup(name="Recent run")
    recent.items.append(_check_last_run())

    groups = [initial, daily, weekly, recent]
    all_items = [i for g in groups for i in g.items]
    overall = _roll_up(all_items)
    summary = {
        "ok":    sum(1 for i in all_items if i.status == "ok"),
        "warn":  sum(1 for i in all_items if i.status == "warn"),
        "error": sum(1 for i in all_items if i.status == "error"),
        "total": len(all_items),
    }
    return DataHealthReport(
        overall=overall,
        checked_at=_ist_iso_now(),
        summary=summary,
        groups=groups,
    )


# --------------------------------------------------------------------------- #
# Sidecar writer — called by orchestrator/catchup to surface errors to UI
# --------------------------------------------------------------------------- #

def record_run(kind: str, ok: bool, error: str = "",
               started_at: Optional[str] = None,
               finished_at: Optional[str] = None,
               extras: Optional[dict] = None) -> None:
    """Persist the outcome of the most recent orchestrator/catchup run.

    `kind` is "nightly" | "weekly" | "catchup" | etc. UI will surface the
    error string verbatim, so be specific.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": kind,
        "ok": ok,
        "error": error,
        "started_at": started_at,
        "finished_at": finished_at or _ist_iso_now(),
    }
    if extras:
        payload["extras"] = extras
    try:
        with _LAST_RUN_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        # If we can't even write the sidecar, swallow — caller already logged.
        pass


if __name__ == "__main__":
    # Manual smoke test:  python -m backend.data_health
    rep = probe()
    print(json.dumps(rep.to_dict(), indent=2))
