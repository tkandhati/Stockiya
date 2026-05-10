"""Daily picks file cache (middleware-side).

The middleware writes the day's picks to disk after generating them, and reads
them back on subsequent /api/picks calls so the LLM is only invoked once per
IST day. Keyed by the IST date so a fresh pick set rolls over at IST midnight.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def ist_today_iso() -> str:
    return datetime.now(IST).date().isoformat()


def ist_now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _picks_path(date_iso: str) -> Path:
    return DATA_DIR / f"picks_{date_iso}.json"


def read_picks(date_iso: str) -> Optional[dict]:
    p = _picks_path(date_iso)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_picks(date_iso: str, payload: dict) -> None:
    _picks_path(date_iso).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
