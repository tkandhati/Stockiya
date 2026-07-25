"""Historical position replay — "show me this position as of date D".

Powers the positions date-picker: list the dates a symbol has a persisted trace
for, and reconstruct that day's accumulation gauge FROM FILES ONLY.

NO NETWORK. This module never fetches OHLCV. It reads the per-date scan traces
`data/traces/run_<date>_<symbol>.jsonl` (which already carry every stage's
features, including the day's close in the [I] stage and atr_pct in [CS]) and
the open-position targets from `data/portfolio.csv`. That keeps historical
replay deterministic and firewall-safe, and — because those files are only ever
written once per day and never rewritten for a past date — it also honours the
"freeze the day's snapshot after EOD" rule for free (see day_freshness): a past
date is inherently frozen, and today's date simply won't appear in the list
until its trace exists (holiday => today absent => latest = previous working
day).

Fix points:
    _TRACES_DIR / _PORTFOLIO_CSV   — data locations
    available_position_dates       — which dates are offered in the picker
    position_as_of                 — reconstruct one day's card
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

from .accumulation_gauge import gauge_from_trace_features

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"
_PORTFOLIO_CSV = _PROJECT_ROOT / "data" / "portfolio.csv"

_ACTIVE_STATUSES = {"open", "partial_t1", "partial"}


def _safe(symbol: str) -> str:
    """Trace-file-safe symbol — mirrors signal_trajectory._load_stage_features."""
    return symbol.replace("\\", "_").replace(":", "_")


def _is_iso_date(s: str) -> bool:
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit()


def available_position_dates(symbol: str) -> list[str]:
    """All trace dates for `symbol`, newest first. Empty if none on disk."""
    safe = _safe(symbol)
    prefix, suffix = "run_", f"_{safe}.jsonl"
    dates: set[str] = set()
    if _TRACES_DIR.exists():
        for p in _TRACES_DIR.glob(f"run_*_{safe}.jsonl"):
            name = p.name
            if name.startswith(prefix) and name.endswith(suffix):
                d = name[len(prefix):-len(suffix)]
                if _is_iso_date(d):
                    dates.add(d)
    return sorted(dates, reverse=True)


def _read_trace_stages(symbol: str, date_iso: str) -> dict[str, dict]:
    """{stage_id: features} for one day's trace. Empty dict if the file is
    absent (holiday / never scanned)."""
    p = _TRACES_DIR / f"run_{date_iso}_{_safe(symbol)}.jsonl"
    if not p.exists():
        return {}
    stages: dict[str, dict] = {}
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = row.get("stage_id") or row.get("stage")
            feats = row.get("features")
            if sid and isinstance(feats, dict):
                stages[sid] = feats
    return stages


def entry_atr_pct(symbol: str, entry_date_iso: str) -> Optional[float]:
    """ATR(14)/close for the stock at entry, from its [CS] trace stage.

    Powers the per-stock adversity buffer on the LIVE card. None-safe: returns
    None if the trace or the field is missing (buffer then shows as unknown).
    """
    stages = _read_trace_stages(symbol, entry_date_iso)
    cs = stages.get("CS") or {}
    val = cs.get("atr_pct")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _read_portfolio_rows() -> list[dict]:
    if not _PORTFOLIO_CSV.exists():
        return []
    try:
        with _PORTFOLIO_CSV.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def open_position_targets(symbol: str) -> Optional[dict]:
    """Entry / stop / T1 / T2 / entry_date for an active position, from
    portfolio.csv. Network-free. None if the symbol has no active row.

    Entry uses the user's actual fill when present (mirrors positions_view),
    else the scanner's. T2 falls back to the legacy `target_price` column.
    """
    def _f(r: dict, key: str, alt: Optional[str] = None) -> float:
        raw = r.get(key)
        if (raw is None or raw == "") and alt:
            raw = r.get(alt)
        try:
            return float(raw or 0)
        except (TypeError, ValueError):
            return 0.0

    for r in _read_portfolio_rows():
        if (r.get("symbol") or "").upper() != symbol.upper():
            continue
        if (r.get("status") or "open").strip().lower() not in _ACTIVE_STATUSES:
            continue
        user_entry = _f(r, "user_entry_price")
        scanner_entry = _f(r, "entry_price")
        entry = user_entry if user_entry > 0 else scanner_entry
        entry_date = (r.get("user_entry_date") or "").strip() \
            or (r.get("entry_date") or "").strip()
        return {
            "entry_price": entry,
            "stop_price": _f(r, "stop_price"),
            "t1_price": _f(r, "t1_price"),
            "t2_price": _f(r, "t2_price", "target_price"),
            "entry_date": entry_date,
        }
    return None


def position_as_of(
    symbol: str,
    date_iso: str,
    *,
    entry_price: Optional[float],
    stop_price: Optional[float],
    t1_price: Optional[float] = None,
    t2_price: Optional[float] = None,
    entry_date: Optional[str] = None,
) -> dict:
    """Reconstruct a position's accumulation card as of `date_iso`, from files.

    Returns {"available": False, ...} when there is no trace for that day.
    """
    stages = _read_trace_stages(symbol, date_iso)
    if not stages:
        return {"available": False, "symbol": symbol, "date": date_iso}

    merged: dict = {}
    for feats in stages.values():
        merged.update(feats)

    close = (stages.get("I") or {}).get("current")
    if close is None:
        close = merged.get("current")
    atr_pct = (stages.get("CS") or {}).get("atr_pct")
    if atr_pct is None:
        atr_pct = merged.get("atr_pct")

    gauge = gauge_from_trace_features(
        merged,
        close=close,
        stop=stop_price,
        entry=entry_price,
        atr_pct=atr_pct,
        as_of=date_iso,
    )

    pnl_pct = None
    try:
        if close is not None and entry_price and float(entry_price) > 0:
            pnl_pct = round((float(close) / float(entry_price) - 1) * 100, 2)
    except (TypeError, ValueError):
        pnl_pct = None

    return {
        "available": True,
        "symbol": symbol,
        "date": date_iso,
        "close": close,
        "pnl_pct": pnl_pct,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "t1_price": t1_price,
        "t2_price": t2_price,
        "entry_date": entry_date,
        "accumulation_gauge": gauge,
        "stages_present": sorted(stages.keys()),
    }
