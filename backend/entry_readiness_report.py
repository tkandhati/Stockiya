"""Offline before/after for the entry-readiness router.

Replays `entry_readiness.entry_readiness` over every persisted
``data/picks_<date>.json`` and shows, per day, which picks stay in the main BUY
list (enterable today) and which move to the awareness section — without running
the pipeline (pure file reads, firewall-safe).

Run:  python -m backend.entry_readiness_report
"""
from __future__ import annotations

import json
from pathlib import Path

from .entry_readiness import entry_readiness

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    files = 0
    total = 0
    total_moved = 0
    rows: list[str] = []

    for path in sorted(_DATA_DIR.glob("picks_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Demo-mode runs use synthetic data; their pick_history trails would
        # contaminate the recurrence signal. Real runs only.
        if doc.get("demo_mode"):
            continue
        picks = doc.get("picks") or []
        if not picks:
            continue
        files += 1
        total += len(picks)

        main_syms: list[str] = []
        moved: list[str] = []
        for pk in picks:
            r = entry_readiness(pk)
            timing = ((pk.get("confirmation") or {}).get("entry_timing"))
            if r:
                moved.append(f"{pk.get('symbol')}[{r['category']}]")
            else:
                main_syms.append(f"{pk.get('symbol')}({timing})")
        total_moved += len(moved)

        date = doc.get("date", path.stem.replace("picks_", ""))
        rows.append(
            f"  {date}: MAIN({len(main_syms)}) {', '.join(main_syms) or '-'}\n"
            f"            AWARE({len(moved)}) {', '.join(moved) or '-'}"
        )

    print("=" * 72)
    print("Entry-readiness router — offline before/after (main vs awareness)")
    print("=" * 72)
    print("\n".join(rows) if rows else "  (no picks files with picks found)")
    print("-" * 72)
    if total:
        print(
            f"  Files: {files} | picks: {total} | moved to awareness: "
            f"{total_moved} ({100.0 * total_moved / total:.1f}%) | "
            f"stayed in main: {total - total_moved}"
        )
    print("=" * 72)


if __name__ == "__main__":
    main()
