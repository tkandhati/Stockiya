"""Portfolio tracking — record every pick + weekly close prices until exit.

Two CSV files in `data/`:

  portfolio.csv         — one row per pick (the master ledger)
  portfolio_weekly.csv  — one row per (pick × week_ending_date) — the timeseries

Lifecycle of a pick:
  open --> target_hit | stopped | timed_out | hypothesis_broken

The nightly orchestrator calls `record_picks()` to log new picks.
The weekly orchestrator (backend/weekly.py) calls `update_open_picks()` every
Friday after market close to fetch closes and check for exits.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

PORTFOLIO_CSV = _DATA_DIR / "portfolio.csv"
WEEKLY_CSV = _DATA_DIR / "portfolio_weekly.csv"

log = logging.getLogger("portfolio")

PORTFOLIO_FIELDS = [
    "pick_id", "entry_date", "symbol", "company",
    "entry_price", "target_price", "stop_price",
    "target_window_label", "target_date", "target_min_date", "target_max_date",
    "weinstein_stage", "entry_timing",
    "headline", "risk_headline",
    "status", "exit_date", "exit_price", "exit_reason", "pnl_pct",
    "last_updated",
]

WEEKLY_FIELDS = [
    "pick_id", "symbol", "week_ending", "close",
    "pnl_from_entry_pct", "dist_to_target_pct", "dist_to_stop_pct",
]


@dataclass
class PortfolioRow:
    pick_id: str
    entry_date: str
    symbol: str
    company: str
    entry_price: float
    target_price: float
    stop_price: float
    target_window_label: str
    target_date: str
    target_min_date: str
    target_max_date: str
    weinstein_stage: str
    entry_timing: str
    headline: str
    risk_headline: str
    status: str = "open"
    exit_date: str = ""
    exit_price: str = ""
    exit_reason: str = ""
    pnl_pct: str = ""
    last_updated: str = ""


# --------------------------------------------------------------------------- #
# Read / write helpers
# --------------------------------------------------------------------------- #

def _read_portfolio() -> list[dict]:
    if not PORTFOLIO_CSV.exists():
        return []
    with PORTFOLIO_CSV.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_portfolio(rows: list[dict]) -> None:
    with PORTFOLIO_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PORTFOLIO_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in PORTFOLIO_FIELDS})


def _append_weekly(rows: Iterable[dict]) -> None:
    new = not WEEKLY_CSV.exists()
    with WEEKLY_CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=WEEKLY_FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in WEEKLY_FIELDS})


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _next_pick_id(rows: list[dict]) -> str:
    """Generate the next pick_id like P-0001."""
    n = 0
    for r in rows:
        pid = r.get("pick_id", "")
        if pid.startswith("P-"):
            try:
                n = max(n, int(pid[2:]))
            except ValueError:
                pass
    return f"P-{n+1:04d}"


def record_picks(picks_payload: dict) -> int:
    """Append today's picks to portfolio.csv if not already recorded.

    `picks_payload` is the PicksResponse dict (date, picks, ...).
    Returns the number of NEW picks added (0 if all already recorded for today).
    """
    rows = _read_portfolio()
    today = picks_payload.get("date") or date.today().isoformat()

    # Avoid duplicates: skip any (symbol, entry_date) already in the ledger.
    existing = {(r["symbol"], r["entry_date"]) for r in rows}

    added = 0
    for p in picks_payload.get("picks", []):
        key = (p["symbol"], today)
        if key in existing:
            continue
        tw = p.get("target_window") or {}
        center = float(tw.get("center_months", 4.5))
        tol = float(tw.get("tolerance_months", 1.5))
        entry_d = date.fromisoformat(today)
        target_d = entry_d + timedelta(days=int(center * 30))
        target_min = entry_d + timedelta(days=int((center - tol) * 30))
        target_max = entry_d + timedelta(days=int((center + tol) * 30))

        row = PortfolioRow(
            pick_id=_next_pick_id(rows),
            entry_date=today,
            symbol=p["symbol"],
            company=p["company"],
            entry_price=float(p["best_buy_at"]),
            target_price=float(p["sell_target"]),
            stop_price=float(p["stop_loss"]),
            target_window_label=tw.get("label", ""),
            target_date=target_d.isoformat(),
            target_min_date=target_min.isoformat(),
            target_max_date=target_max.isoformat(),
            weinstein_stage=p.get("weinstein_stage", ""),
            entry_timing=p.get("entry_timing", ""),
            headline=p.get("headline", ""),
            risk_headline=p.get("risk_headline", ""),
            last_updated=datetime.now(IST).isoformat(timespec="seconds"),
        )
        rows.append(row.__dict__)
        added += 1

    if added:
        _write_portfolio(rows)
        log.info("Recorded %d new picks to %s", added, PORTFOLIO_CSV.name)
    return added


def update_open_picks(close_price_for: callable) -> dict:
    """Run the weekly close updater.

    `close_price_for(symbol)` -> float | None — caller-provided fetcher.

    For each pick with status=open:
      - Fetch this Friday's close
      - Append a row to portfolio_weekly.csv
      - If close >= target_price → mark "target_hit", set exit fields
      - If close <= stop_price   → mark "stopped"
      - If today > target_max_date → mark "timed_out"

    Returns a summary dict {open: n, target_hit: n, stopped: n, timed_out: n}.
    """
    rows = _read_portfolio()
    today = datetime.now(IST).date()
    week_ending = today.isoformat()
    weekly_rows: list[dict] = []
    summary = {"open": 0, "target_hit": 0, "stopped": 0, "timed_out": 0}

    for r in rows:
        if r.get("status") != "open":
            continue

        sym = r["symbol"]
        try:
            close = close_price_for(sym)
        except Exception as e:
            log.warning("close fetch failed for %s: %s", sym, e)
            close = None
        if close is None:
            log.warning("no close price for %s — leaving as-is", sym)
            continue

        entry_px = float(r["entry_price"])
        target_px = float(r["target_price"])
        stop_px = float(r["stop_price"])
        pnl_pct = (close / entry_px - 1) * 100

        weekly_rows.append({
            "pick_id": r["pick_id"],
            "symbol": sym,
            "week_ending": week_ending,
            "close": round(close, 2),
            "pnl_from_entry_pct": round(pnl_pct, 2),
            "dist_to_target_pct": round((close / target_px - 1) * 100, 2),
            "dist_to_stop_pct": round((close / stop_px - 1) * 100, 2),
        })

        new_status = "open"
        exit_reason = ""
        if close >= target_px:
            new_status, exit_reason = "target_hit", f"close {close:.2f} >= target {target_px:.2f}"
        elif close <= stop_px:
            new_status, exit_reason = "stopped", f"close {close:.2f} <= stop {stop_px:.2f}"
        else:
            target_max = date.fromisoformat(r["target_max_date"])
            if today > target_max:
                new_status, exit_reason = "timed_out", f"past target_max_date {target_max.isoformat()}"

        if new_status != "open":
            r["status"] = new_status
            r["exit_date"] = today.isoformat()
            r["exit_price"] = f"{close:.2f}"
            r["exit_reason"] = exit_reason
            r["pnl_pct"] = f"{pnl_pct:.2f}"
        r["last_updated"] = datetime.now(IST).isoformat(timespec="seconds")
        summary[new_status] += 1

    if weekly_rows:
        _append_weekly(weekly_rows)
    _write_portfolio(rows)
    log.info("Weekly update: %s", summary)
    return summary


def list_open_pick_symbols() -> list[str]:
    """Helper for ad-hoc price refreshes."""
    return [r["symbol"] for r in _read_portfolio() if r.get("status") == "open"]
