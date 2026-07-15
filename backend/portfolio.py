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
    # ---- Identity ----
    "pick_id",
    "trace_id",                  # UUID from pipeline run; joins to data/traces/run_<date>_<ticker>.jsonl
    "entry_date",
    "symbol",
    "company",
    # ---- Price plan (new gates spine) ----
    "entry_price",
    "stop_price",
    "target_price",              # == t2_price (kept for backward compat with weekly updater)
    "t1_price",
    "t2_price",
    # ---- Position sizing ----
    "shares_total",
    "shares_at_t1",
    "shares_at_t2",
    "account_value",
    "risk_pct_of_account",
    # ---- Why we picked it ----
    "confirmation_score",
    "confirmation_bonuses",      # '; '-joined list for CSV readability
    "headline",
    # ---- Time stops (computed at entry) ----
    "target_window_label",
    "target_date",
    "target_min_date",
    "target_max_date",
    # ---- Volume-based dynamic horizon (bucketed: 30/60/90/120/180) ----
    "end_date",                  # active end-of-horizon date (may be extended at revalidation)
    "horizon_days",               # last chosen bucket, in days
    "horizon_basis",              # audit string: which inputs produced the bucket
    "horizon_source",             # entry_estimate | revalidation_extension
    # ---- Legacy fields (kept so older rows still load) ----
    "weinstein_stage",
    "entry_timing",
    "risk_headline",
    # ---- User ownership + actual fill (blank = use scanner's numbers) ----
    "ownership",                 # suggested | paper | live | declined
    "user_entry_date",           # ISO date or ""; blank -> use scanner's entry_date
    "user_entry_price",          # float or 0;    0     -> use scanner's entry_price
    "user_shares",               # int or 0;      0     -> use scanner's shares_total
    "user_notes",                # free-form
    # ---- Lifecycle / outcome ----
    "status",                    # open | partial_t1 | superseded | target_hit | stopped | timed_out | hypothesis_broken
    "hit_t1",                    # 'true' once T1 has been crossed
    "hit_t1_date",
    "exit_date",
    "exit_price",
    "exit_reason",
    "pnl_pct",
    "superseded_by",              # pick_id of the replacement row (only set when status=superseded)
    "last_updated",
]

# Valid values for the `ownership` column. "suggested" = scanner emitted it,
# user hasn't acted. "paper" / "live" = user took it (with or without capital).
# "declined" = user rejected it; weekly / outcome trackers skip these.
OWNERSHIP_VALUES = frozenset({"suggested", "paper", "live", "declined"})

WEEKLY_FIELDS = [
    "pick_id", "symbol", "week_ending", "close",
    "pnl_from_entry_pct", "dist_to_target_pct", "dist_to_stop_pct",
    "dist_to_t1_pct", "dist_to_t2_pct",   # NEW: ladder distances
]


@dataclass
class PortfolioRow:
    """All fields default to safe empty/zero values so the row can be built
    progressively as the payload schema grows."""
    pick_id: str = ""
    trace_id: str = ""
    entry_date: str = ""
    symbol: str = ""
    company: str = ""
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    t1_price: float = 0.0
    t2_price: float = 0.0
    shares_total: int = 0
    shares_at_t1: int = 0
    shares_at_t2: int = 0
    account_value: float = 0.0
    risk_pct_of_account: float = 0.0
    confirmation_score: float = 0.0
    confirmation_bonuses: str = ""
    headline: str = ""
    target_window_label: str = ""
    target_date: str = ""
    target_min_date: str = ""
    target_max_date: str = ""
    end_date: str = ""
    horizon_days: int = 0
    horizon_basis: str = ""
    horizon_source: str = ""
    weinstein_stage: str = ""
    entry_timing: str = ""
    risk_headline: str = ""
    ownership: str = "suggested"
    user_entry_date: str = ""
    user_entry_price: float = 0.0
    user_shares: int = 0
    user_notes: str = ""
    status: str = "open"
    hit_t1: str = ""
    hit_t1_date: str = ""
    exit_date: str = ""
    exit_price: str = ""
    exit_reason: str = ""
    pnl_pct: str = ""
    superseded_by: str = ""
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


_OPEN_STATUSES: frozenset[str] = frozenset({"open", "partial_t1"})
_TAKEN_OWNERSHIPS: frozenset[str] = frozenset({"paper", "live"})


def record_picks(picks_payload: dict) -> int:
    """Persist today's picks to portfolio.csv with replace/append/duplicate
    semantics.

    Rules for each incoming pick (by symbol):
      1. Same (symbol, entry_date) already recorded  -> skip (idempotent).
      2. Symbol has an OPEN "suggested" row (never taken):
         -> SUPERSEDE it (status="superseded", exit_reason="superseded_by_<new>",
            superseded_by=<new_pick_id>). Add the fresh row.
      3. Symbol has an OPEN taken row (paper / live) and no suggested row:
         -> ADD fresh suggested row alongside the taken one. Two rows for
            the same symbol are legitimate: one is the user's real capital,
            the other is the fresh signal.
      4. Symbol has NO open rows -> ADD fresh row.

    Also computes and stores the dynamic end_date (volume-based horizon
    bucket from backend.horizon). Old target_date fields remain populated
    for backward compat with the weekly updater.

    Returns the number of NEW rows added (superseding does not count).
    """
    # Local import so the module loads even if horizon has an issue.
    from .horizon import estimated_horizon_days

    rows = _read_portfolio()
    today = picks_payload.get("date") or date.today().isoformat()
    entry_d = date.fromisoformat(today)
    now_iso = datetime.now(IST).isoformat(timespec="seconds")

    # Idempotency: (symbol, entry_date) already present -> skip that pick.
    same_day_keys = {(r.get("symbol"), r.get("entry_date")) for r in rows}

    # Group existing rows for lookup by symbol.
    open_by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("status") in _OPEN_STATUSES:
            open_by_symbol.setdefault(r.get("symbol", ""), []).append(r)

    added = 0
    superseded_count = 0
    for p in picks_payload.get("picks", []):
        sym = p["symbol"]
        if (sym, today) in same_day_keys:
            continue

        # Extract price plan (new shape) or fall back to legacy aliases.
        plan = p.get("price_plan") or {}
        entry_price = float(plan.get("entry") or p.get("best_buy_at") or 0)
        stop_price = float(plan.get("stop") or p.get("stop_loss") or 0)
        t1_price = float(plan.get("t1") or 0)
        t2_price = float(plan.get("t2") or p.get("sell_target") or 0)

        conf = p.get("confirmation") or {}
        bonuses_fired = conf.get("bonuses_fired") or []

        # Legacy target window (kept populated for backward compat).
        tw = p.get("target_window") or {}
        center = float(tw.get("center_months", 6.0))
        tol = float(tw.get("tolerance_months", 2.0))
        target_d = entry_d + timedelta(days=int(center * 30))
        target_min = entry_d + timedelta(days=int((center - tol) * 30))
        target_max = entry_d + timedelta(days=int((center + tol) * 30))

        # Volume-based dynamic horizon (the new primary exit clock).
        horizon = p.get("holding_horizon") or {}
        if horizon.get("days"):
            horizon_days = int(horizon["days"])
            horizon_basis = str(horizon.get("basis") or "")
            horizon_source = str(horizon.get("source") or "entry_estimate")
        else:
            horizon_days, horizon_basis = estimated_horizon_days(p)
            horizon_source = "entry_estimate"
        end_d = entry_d + timedelta(days=horizon_days)

        new_pick_id = _next_pick_id(rows)

        # Supersede any open "suggested" rows for this symbol (rule 2).
        existing_open = open_by_symbol.get(sym, [])
        for r in existing_open:
            r_owner = (r.get("ownership") or "suggested").strip()
            if r_owner in _TAKEN_OWNERSHIPS:
                continue  # rule 3: taken rows survive; duplicates OK
            r["status"] = "superseded"
            r["exit_date"] = today
            r["exit_reason"] = f"superseded_by_{new_pick_id}"
            r["superseded_by"] = new_pick_id
            r["last_updated"] = now_iso
            superseded_count += 1

        row = PortfolioRow(
            pick_id=new_pick_id,
            trace_id=str(p.get("trace_id") or ""),
            entry_date=today,
            symbol=sym,
            company=p.get("company") or sym,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=t2_price,        # legacy alias used by weekly updater
            t1_price=t1_price,
            t2_price=t2_price,
            shares_total=int(plan.get("shares_total", 0)),
            shares_at_t1=int(plan.get("shares_at_t1", 0)),
            shares_at_t2=int(plan.get("shares_at_t2", 0)),
            account_value=float(plan.get("account_value", 0)),
            risk_pct_of_account=float(plan.get("risk_pct_of_account", 0)),
            confirmation_score=float(conf.get("score", 0)),
            confirmation_bonuses="; ".join(str(b) for b in bonuses_fired),
            headline=p.get("headline", ""),
            target_window_label=tw.get("label", ""),
            target_date=target_d.isoformat(),
            target_min_date=target_min.isoformat(),
            target_max_date=target_max.isoformat(),
            end_date=end_d.isoformat(),
            horizon_days=horizon_days,
            horizon_basis=horizon_basis,
            horizon_source=horizon_source,
            weinstein_stage=p.get("weinstein_stage", ""),
            entry_timing=p.get("entry_timing", ""),
            risk_headline=p.get("risk_headline", ""),
            ownership="suggested",
            last_updated=now_iso,
        )
        rows.append(row.__dict__)
        added += 1

    if added or superseded_count:
        _write_portfolio(rows)
        log.info(
            "portfolio.csv: added=%d superseded=%d (file=%s)",
            added, superseded_count, PORTFOLIO_CSV.name,
        )
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
    summary = {"open": 0, "partial_t1": 0, "target_hit": 0, "stopped": 0, "timed_out": 0}

    for r in rows:
        if r.get("status") not in ("open", "partial_t1"):
            continue
        # Skip picks the user explicitly declined — no point tracking them.
        if r.get("ownership") == "declined":
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

        t1_px = float(r.get("t1_price") or 0)
        t2_px = float(r.get("t2_price") or target_px)
        weekly_rows.append({
            "pick_id": r["pick_id"],
            "symbol": sym,
            "week_ending": week_ending,
            "close": round(close, 2),
            "pnl_from_entry_pct": round(pnl_pct, 2),
            "dist_to_target_pct": round((close / target_px - 1) * 100, 2),
            "dist_to_stop_pct": round((close / stop_px - 1) * 100, 2),
            "dist_to_t1_pct": (
                round((close / t1_px - 1) * 100, 2) if t1_px > 0 else ""
            ),
            "dist_to_t2_pct": (
                round((close / t2_px - 1) * 100, 2) if t2_px > 0 else ""
            ),
        })

        # Lifecycle decision — T1 partial first, then T2 / stop / time.
        prior_status = r.get("status", "open")
        hit_t1_already = (r.get("hit_t1") == "true")

        new_status = prior_status
        exit_reason = ""

        # T1 ladder rung: log the cross but DON'T close the position.
        if t1_px > 0 and not hit_t1_already and close >= t1_px:
            r["hit_t1"] = "true"
            r["hit_t1_date"] = today.isoformat()
            new_status = "partial_t1"   # half exited (notional), half still riding

        # Closing conditions
        if close >= target_px:
            new_status, exit_reason = "target_hit", f"close {close:.2f} >= target {target_px:.2f}"
        elif close <= stop_px:
            new_status, exit_reason = "stopped", f"close {close:.2f} <= stop {stop_px:.2f}"
        else:
            target_max = date.fromisoformat(r["target_max_date"])
            if today > target_max:
                new_status, exit_reason = "timed_out", f"past target_max_date {target_max.isoformat()}"

        if new_status in ("target_hit", "stopped", "timed_out"):
            r["status"] = new_status
            r["exit_date"] = today.isoformat()
            r["exit_price"] = f"{close:.2f}"
            r["exit_reason"] = exit_reason
            r["pnl_pct"] = f"{pnl_pct:.2f}"
        elif new_status == "partial_t1":
            r["status"] = new_status
        r["last_updated"] = datetime.now(IST).isoformat(timespec="seconds")
        summary[new_status] = summary.get(new_status, 0) + 1

    if weekly_rows:
        _append_weekly(weekly_rows)
    _write_portfolio(rows)
    log.info("Weekly update: %s", summary)
    return summary


def list_open_pick_symbols() -> list[str]:
    """Helper for ad-hoc price refreshes."""
    return [r["symbol"] for r in _read_portfolio() if r.get("status") == "open"]


# --------------------------------------------------------------------------- #
# User-ownership operations
# --------------------------------------------------------------------------- #

def set_ownership(
    pick_id: str,
    ownership: str,
    *,
    user_entry_date: str = "",
    user_entry_price: float = 0.0,
    user_shares: int = 0,
    user_notes: str = "",
) -> Optional[dict]:
    """Update ownership + optional user-fill fields for a single pick_id.

    Returns the updated row dict on success, or None if pick_id was not found.
    Raises ValueError if `ownership` is not one of OWNERSHIP_VALUES.
    """
    if ownership not in OWNERSHIP_VALUES:
        raise ValueError(
            f"invalid ownership {ownership!r}; must be one of {sorted(OWNERSHIP_VALUES)}"
        )

    rows = _read_portfolio()
    updated: Optional[dict] = None
    for r in rows:
        if r.get("pick_id") != pick_id:
            continue
        r["ownership"] = ownership
        if user_entry_date:
            r["user_entry_date"] = user_entry_date
        if user_entry_price:
            r["user_entry_price"] = f"{float(user_entry_price):.4f}"
        if user_shares:
            r["user_shares"] = str(int(user_shares))
        if user_notes:
            r["user_notes"] = user_notes
        r["last_updated"] = datetime.now(IST).isoformat(timespec="seconds")
        updated = r
        break

    if updated is not None:
        _write_portfolio(rows)
        log.info("Updated ownership for %s -> %s", pick_id, ownership)
    return updated
