"""Trading-day / non-trading-day guard for the daily pipeline.

Non-trading days are Sat/Sun and any weekday where no fresh OHLCV was
available for the universe (holiday / bhavcopy not yet published). On
such days the orchestrator:

  1. does NOT write a new picks file (data/picks_<today>.json)
  2. does NOT update the portfolio ledger
  3. does NOT append a daily-diagnostic snapshot as if a real run happened
  4. logs one row to data/traces/no_fire_days.jsonl explaining the skip

Downstream (middleware/picks.py) reads fall through to the previous
active trading day's picks file via `latest_picks_file_on_or_before`,
so the UI shows the last real pick set instead of an empty page.

Design notes:
  - Pure module: no I/O beyond filesystem enumeration for the
    "latest picks on or before" lookup and the append-only no_fire log.
    Safe to import from stages / middleware / scripts.
  - Weekend check is date-only (Sat = weekday 5, Sun = 6). NSE holiday
    calendar is NOT wired here — instead we infer "holiday" from
    "ingest failed for 100% of tickers" after Phase 1 runs. Deferred
    calendar work is parked in ideas.md under pillar G.
  - The no_fire_days.jsonl schema distinguishes `weekend`,
    `holiday_no_data`, and `data_missing_error` so weekly-learn can
    tell intentional skips apart from real fetch failures.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_TRACES_DIR = _DATA_DIR / "traces"
_NO_FIRE_LOG = _TRACES_DIR / "no_fire_days.jsonl"

# Canonical picks-file naming — must match backend/stages/render.py.
_PICKS_FILE_RE = re.compile(r"^picks_(\d{4}-\d{2}-\d{2})\.json$")

NoFireReason = Literal["weekend", "holiday_no_data", "data_missing_error"]

log = logging.getLogger("trading_day")


@dataclass
class TradingDayVerdict:
    """Verdict shape returned by classify_* helpers.

    Fields:
      is_trading_day  : True only when the pipeline should produce fresh
                        picks. False on weekends and holidays.
      reason          : None when is_trading_day; otherwise the specific
                        NoFireReason used to distinguish rows in the
                        no_fire_days.jsonl log.
      weekday         : Human-readable short day name ("Mon".."Sun").
      date_iso        : The date being classified.
    """
    is_trading_day: bool
    reason: Optional[NoFireReason]
    weekday: str
    date_iso: str


def parse_iso(date_iso: str) -> date:
    return datetime.fromisoformat(date_iso).date()


def today_iso_ist() -> str:
    return datetime.now(IST).date().isoformat()


def is_weekend(date_iso: str) -> bool:
    return parse_iso(date_iso).weekday() >= 5   # 5=Sat, 6=Sun


def classify_pre_pipeline(date_iso: str) -> TradingDayVerdict:
    """Cheap up-front check. Weekend-only detection — cannot see
    'no fresh data' until after Phase 1 [I] Ingest runs.
    """
    d = parse_iso(date_iso)
    if d.weekday() >= 5:
        return TradingDayVerdict(
            is_trading_day=False,
            reason="weekend",
            weekday=d.strftime("%a"),
            date_iso=date_iso,
        )
    return TradingDayVerdict(
        is_trading_day=True,
        reason=None,
        weekday=d.strftime("%a"),
        date_iso=date_iso,
    )


def classify_post_ingest(
    date_iso: str,
    ingest_total: int,
    ingest_failed: int,
) -> TradingDayVerdict:
    """After Phase 1 runs, decide whether today was a 'no data' day.

    100% ingest failure ⇒ treat as `holiday_no_data`. This is the honest
    fallback the user asked for: "holidays (when no data found)".

    The 90-99% failure path is a *misconfiguration* signal, not a
    holiday — the orchestrator's existing data_misconfigured branch
    still fires for that case and writes a diagnostic picks file so
    the operator sees the fix. We only skip on 100% failure.
    """
    d = parse_iso(date_iso)
    if ingest_total > 0 and ingest_failed == ingest_total:
        return TradingDayVerdict(
            is_trading_day=False,
            reason="holiday_no_data",
            weekday=d.strftime("%a"),
            date_iso=date_iso,
        )
    return TradingDayVerdict(
        is_trading_day=True,
        reason=None,
        weekday=d.strftime("%a"),
        date_iso=date_iso,
    )


def log_no_fire(verdict: TradingDayVerdict, extra: Optional[dict] = None) -> None:
    """Append one row to data/traces/no_fire_days.jsonl.

    Rows carry the reason enum so weekly-learn can tell intentional
    skips (weekend / holiday) apart from bugs (data_missing_error).
    """
    _TRACES_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "ts": datetime.now(IST).isoformat(timespec="seconds"),
        **asdict(verdict),
    }
    if extra:
        payload.update(extra)
    try:
        with _NO_FIRE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except OSError:
        log.exception("failed to append no_fire_days.jsonl row (non-fatal)")


def latest_picks_file_on_or_before(target_date_iso: str) -> Optional[Path]:
    """Walk data/ for the newest picks_YYYY-MM-DD.json whose date is
    <= target_date_iso. Returns the Path, or None if nothing exists.

    Filesystem-only; does not read file contents.
    """
    if not _DATA_DIR.exists():
        return None
    try:
        target = parse_iso(target_date_iso)
    except ValueError:
        return None
    best_date: Optional[date] = None
    best_path: Optional[Path] = None
    for p in _DATA_DIR.iterdir():
        if not p.is_file():
            continue
        m = _PICKS_FILE_RE.match(p.name)
        if not m:
            continue
        try:
            file_date = parse_iso(m.group(1))
        except ValueError:
            continue
        if file_date > target:
            continue
        if best_date is None or file_date > best_date:
            best_date = file_date
            best_path = p
    return best_path


def load_previous_picks(target_date_iso: str) -> Optional[dict]:
    """Read the latest picks file at or before target date. None if absent.

    Adds a leading segment to the `message` field so the UI can render
    "Showing picks from <source_date>" without needing a new schema field.
    The on-disk file is not modified.
    """
    p = latest_picks_file_on_or_before(target_date_iso)
    if p is None:
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception("failed to load previous picks from %s", p)
        return None
    source_date = payload.get("date")
    if source_date and source_date != target_date_iso:
        existing_msg = payload.get("message") or ""
        addendum = (
            f"Showing picks from {source_date} — "
            f"{target_date_iso} is a non-trading day."
        )
        payload["message"] = (
            f"{addendum} {existing_msg}".strip() if existing_msg else addendum
        )
    return payload
