"""[R] Render — produce the final PicksResponse JSON.

Takes the selected list from [S] + per-ticker pick payloads from [H] and
writes the day's `data/picks_<date>.json` file. The middleware reads this
file directly; no further computation is needed at request time.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

stage_id = "R"


def render_picks_response(
    selected_payloads: list[dict],
    today_iso: str,
    demo_mode: bool = False,
) -> dict:
    """Return a PicksResponse-shaped dict. Caller is responsible for writing."""
    return {
        "date": today_iso,
        "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
        "source": "pipeline",
        "demo_mode": demo_mode,
        "picks": selected_payloads,
    }


def write_picks_file(payload: dict) -> Path:
    p = _DATA_DIR / f"picks_{payload['date']}.json"
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p
