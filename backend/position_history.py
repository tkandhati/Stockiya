"""Historical position replay — "if I'd entered on date D, is it safe today?"

Powers the positions date-picker: list the days a symbol was recommended, and
for a chosen day D reconstruct — FROM FILES ONLY — how a position opened on D
would stand as of the latest data on file: the accumulation trajectory from D to
now, the P&L since D, and a safe / caution / risky verdict.

NO NETWORK. Reads only the per-date scan traces
`data/traces/run_<date>_<symbol>.jsonl` and the recommended sets
`data/picks_<date>.json`. Deterministic and firewall-safe. "Today" means the
latest trace on disk for the symbol (a market holiday simply means the latest
trace is the previous working day).

Fix points:
    _TRACES_DIR / _DATA_DIR    — data locations
    available_position_dates   — which dates the picker offers (recommended days)
    position_if_entered_on     — the D -> today safe/risky reconstruction
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .accumulation_gauge import gauge_from_position
from .position_sizer import STOP_PCT
from .signal_trajectory import trajectory_between_traces

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_TRACES_DIR = _DATA_DIR / "traces"


def _safe(symbol: str) -> str:
    """Trace-file-safe symbol — mirrors signal_trajectory._load_stage_features."""
    return symbol.replace("\\", "_").replace(":", "_")


def _is_iso_date(s: str) -> bool:
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit()


def available_position_dates(symbol: str) -> list[str]:
    """Dates this symbol was RECOMMENDED (appeared in that day's Top picks),
    newest first.

    Reads `data/picks_<date>.json` — the recommended set — NOT every scanned
    day's trace. So the picker only offers the days we actually surfaced the
    stock, each of which has a trace for the reconstruction.
    """
    sym_u = symbol.upper()
    prefix, suffix = "picks_", ".json"
    dates: set[str] = set()
    if _DATA_DIR.exists():
        for p in _DATA_DIR.glob("picks_*.json"):
            name = p.name
            d = name[len(prefix):-len(suffix)]
            if not _is_iso_date(d):
                continue
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            picks = payload.get("picks") or []
            if any((pk.get("symbol") or "").upper() == sym_u for pk in picks):
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


def _all_trace_dates(symbol: str) -> list[str]:
    """All on-disk trace dates for the symbol, ascending."""
    safe = _safe(symbol)
    prefix, suffix = "run_", f"_{safe}.jsonl"
    dates: list[str] = []
    if _TRACES_DIR.exists():
        for p in _TRACES_DIR.glob(f"run_*_{safe}.jsonl"):
            d = p.name[len(prefix):-len(suffix)]
            if _is_iso_date(d):
                dates.append(d)
    return sorted(dates)


def _latest_trace_date(symbol: str) -> Optional[str]:
    dates = _all_trace_dates(symbol)
    return dates[-1] if dates else None


def _trading_days_between(symbol: str, start: str, end: str) -> int:
    """Count of on-disk trace dates in (start, end] — a trading-day proxy for
    the windowed trajectory rules."""
    return sum(1 for d in _all_trace_dates(symbol) if start < d <= end)


def position_if_entered_on(symbol: str, entry_date_iso: str) -> dict:
    """Reconstruct: if a position had been opened on `entry_date_iso`, how does
    it stand as of the latest data on file — safe, caution, or risky?

    File-only. Compares the accumulation trajectory from the entry day to the
    latest trace (same classifier as the live bar), derives the hypothetical
    entry (that day's close), a -STOP_PCT stop, P&L since entry, and a verdict.
    Returns {"available": False, ...} if there is no trace for the entry day.
    """
    entry_stages = _read_trace_stages(symbol, entry_date_iso)
    latest = _latest_trace_date(symbol)
    if not entry_stages or latest is None:
        return {"available": False, "symbol": symbol, "date": entry_date_iso}

    entry_close = (entry_stages.get("I") or {}).get("current")
    entry_atr = (entry_stages.get("CS") or {}).get("atr_pct")

    tdays = _trading_days_between(symbol, entry_date_iso, latest)
    report = trajectory_between_traces(
        symbol, entry_date_iso, latest, trading_days_since_entry=tdays,
    )

    cur_stages = _read_trace_stages(symbol, latest)
    current_close = (cur_stages.get("I") or {}).get("current")

    stop = None
    if entry_close:
        try:
            stop = round(float(entry_close) * (1 - STOP_PCT), 2)
        except (TypeError, ValueError):
            stop = None

    # Render through the SAME gauge as the live bar, so colour + buffer are
    # consistent: a hypothetical position with D's entry and today's price.
    pos = {
        "trajectory": report.as_dict(),
        "action_label": "",
        "entry_stage": "",
        "current_price": current_close,
        "stop_price": stop,
        "trajectory_flip": report.exit_recommendation,
    }
    gauge = gauge_from_position(pos, atr_pct=entry_atr)

    pnl_pct = None
    try:
        if entry_close and current_close and float(entry_close) > 0:
            pnl_pct = round((float(current_close) / float(entry_close) - 1) * 100, 2)
    except (TypeError, ValueError):
        pnl_pct = None

    level = gauge["level"]
    verdict = "safe" if level >= 4 else ("caution" if level == 3 else "risky")

    return {
        "available": True,
        "symbol": symbol,
        "date": entry_date_iso,          # the hypothetical entry day (selected)
        "as_of_date": latest,            # "today" = latest data on file
        "entry_price": entry_close,
        "current_price": current_close,
        "stop_price": stop,
        "pnl_pct": pnl_pct,
        "verdict": verdict,
        "safe": level >= 4,
        "headline": report.headline,
        "trajectory_overall": report.overall,
        "accumulation_gauge": gauge,
    }
