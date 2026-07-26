"""Archive aged data / process-generated files — deterministic, offline.

Moves dated files older than a per-family retention window out of the live data
dirs into `data/archive/<family>/`, so `data/` stays small and fast without ever
losing history. MOVE, not delete — everything is recoverable from the archive.

SCHEDULING (mirrors the insights file `daily_diagnostic.md`, which is written as
a guarded step inside the nightly pipeline run): `run_archive()` is called from
`backend.nightly` on every nightly run, and is also a standalone entry point:

    python -m backend.archive            # archive per the default rules
    python -m backend.archive --dry-run  # show what WOULD move, move nothing

Most nights it's a no-op (nothing has aged out yet); it's cheap to run daily.

WHAT IT TOUCHES (retention = calendar days; ~180 trading days ≈ 260 calendar):
    data/delivery/       delivery_* / MTO_* / *.DAT   260 d   (~180 trading)
    data/                picks_<date>.json            260 d
    data/position_traces pos_<date>.jsonl             260 d
    data/traces/         run_<date>_*.jsonl           400 d   (safety net —
                          weekly-learn is the primary manager of these)

WHAT IT NEVER TOUCHES: cumulative / stateful files — `outcomes.jsonl`,
`portfolio.csv` (+ `.bak.*`), `portfolio_weekly.csv`, `portfolio_mutations.jsonl`,
`daily_diagnostic.md`, `.last_run.json`. The globs are specific, and a
protected-name guard is a second line of defense. A file with no parseable date
in its name is skipped — retention can only act on dated files.

Fix points:
    DEFAULT_RULES   — the per-family retention table
    _PROTECTED      — never-archive guard
    _ARCHIVE_DIR    — destination root
"""
from __future__ import annotations

import argparse
import logging
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .datekeys import date_from_filename as _file_date

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_ARCHIVE_DIR = _DATA_DIR / "archive"

log = logging.getLogger("archive")

# Never archive these, even if a glob somehow matches (defense in depth).
_PROTECTED = ("outcomes.jsonl", "portfolio", "daily_diagnostic",
              ".last_run", "README", ".bak.")


@dataclass
class ArchiveRule:
    name: str
    src: Path
    globs: list[str]
    keep_days: int
    enabled: bool = True


def _default_rules() -> list[ArchiveRule]:
    return [
        ArchiveRule("delivery", _DATA_DIR / "delivery",
                    ["delivery_*", "MTO_*", "*.DAT"], keep_days=260),
        ArchiveRule("picks", _DATA_DIR,
                    ["picks_*.json"], keep_days=260),
        ArchiveRule("position_traces", _DATA_DIR / "position_traces",
                    ["pos_*.jsonl"], keep_days=260),
        # weekly-learn is the primary manager of scan traces; this is only a
        # long-horizon safety net for anything it never digested.
        ArchiveRule("traces", _DATA_DIR / "traces",
                    ["run_*.jsonl"], keep_days=400),
    ]


DEFAULT_RULES: list[ArchiveRule] = _default_rules()


def _is_protected(name: str) -> bool:
    return any(tok in name for tok in _PROTECTED)


def _move(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        dest.unlink()               # already archived once — overwrite cleanly
    shutil.move(str(src), str(dest))


def run_archive(
    today: Optional[date] = None,
    *,
    rules: Optional[list[ArchiveRule]] = None,
    archive_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """Archive aged files per `rules`. Returns a summary; never raises on a
    single bad file (records it in `errors`). `today` defaults to IST today."""
    if today is None:
        today = datetime.now(IST).date()
    if rules is None:
        rules = DEFAULT_RULES
    if archive_dir is None:
        archive_dir = _ARCHIVE_DIR

    summary: dict = {"dry_run": dry_run, "archived": 0, "by_rule": {}, "errors": []}

    for rule in rules:
        if not rule.enabled:
            continue
        cutoff = today - timedelta(days=rule.keep_days)
        moved = 0
        if rule.src.exists():
            seen: set[Path] = set()
            for glob in rule.globs:
                for p in rule.src.glob(glob):
                    if p in seen or not p.is_file() or _is_protected(p.name):
                        continue
                    seen.add(p)
                    d = _file_date(p.name)
                    if d is None or d >= cutoff:
                        continue          # undated or still within retention
                    try:
                        if not dry_run:
                            _move(p, archive_dir / rule.name)
                        moved += 1
                    except OSError as e:
                        summary["errors"].append(f"{rule.name}:{p.name}: {e}")
        summary["by_rule"][rule.name] = {
            "moved": moved, "cutoff": cutoff.isoformat(), "keep_days": rule.keep_days,
        }
        summary["archived"] += moved
    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] archive: %(message)s",
    )
    ap = argparse.ArgumentParser(description="Archive aged data files.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would move; move nothing")
    args = ap.parse_args()

    started = datetime.now(IST).isoformat(timespec="seconds")
    summary = run_archive(dry_run=args.dry_run)
    log.info("Archive summary: %s", summary)

    # Report health like the other scheduled jobs (nightly/weekly/catchup).
    try:
        from backend.data_health import record_run
        record_run(
            kind="archive",
            ok=not summary["errors"],
            error="; ".join(summary["errors"]),
            started_at=started,
            finished_at=datetime.now(IST).isoformat(timespec="seconds"),
            extras={"archived": summary["archived"], "dry_run": args.dry_run},
        )
    except Exception:
        log.exception("data_health.record_run failed (non-fatal)")


if __name__ == "__main__":
    main()
