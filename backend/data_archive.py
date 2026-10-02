"""Central ARCHIVE / RETENTION POLICY for every Stockya data file.

Owner ask (2026-10-02): "download all the required files that help the volume
analysis, but have an archive policy for each downloaded and generated [file]."

One place that says, per data category, what it is, whether it is DOWNLOADED
(raw, from NSE) or GENERATED (by our pipeline), and how long it is kept. The
rules run at the end of every offline run (``bhavcopy_fetch.cleanup_after_run``)
so disk stays bounded without hand-maintenance. Every retention is env-tunable
and every rule is reversible.

Rules
-----
  ephemeral     erase the whole directory after each run (raw dumps — bulky,
                re-downloadable; never hoarded).
  rolling_days  keep files whose embedded/modified date is within N days of
                today; delete older ones (dated per-day files).
  rolling_rows  the file is ONE growing per-symbol series; retention is applied
                ROW-WISE at write time (see bhavcopy_fetch.pivot_and_merge /
                STOCKYA_BHAVCOPY_LOOKBACK_DAYS). data_archive only removes
                universe-orphan files, and only when STOCKYA_ARCHIVE_PRUNE_ORPHANS=1.
  overwrite     NSE serves a single rolling file; it is overwritten each day —
                no accumulation, nothing to prune.
  archive_days  KEEP forever, but move files older than N days into
                data/archive/<category>/ (used for picks_<date>.json — the
                historical record the "times recommended" count reads; must
                never be deleted).
  external      retention is enforced by another module (documented here for a
                single source of truth); data_archive does not touch it.

Nothing here raises.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("data_archive")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA = _PROJECT_ROOT / "data"
_ARCHIVE = _DATA / "archive"


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _envb(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class Policy:
    category: str
    kind: str          # "downloaded" | "generated"
    rule: str          # see module docstring
    directory: Path
    pattern: str       # glob within directory
    days_env: str      # env var for retention days (ignored for some rules)
    default_days: int
    note: str

    def days(self) -> int:
        return _envi(self.days_env, self.default_days)


# --------------------------------------------------------------------------- #
# The policy table — the single source of truth.
# --------------------------------------------------------------------------- #
def _ohlcv_dir() -> Path:
    try:
        from .fetch import _bhavcopy_ohlcv_dir
        return _bhavcopy_ohlcv_dir()
    except Exception:  # noqa: BLE001
        return _DATA / "ohlcv"


def policies() -> list[Policy]:
    return [
        Policy("bhavcopy_raw", "downloaded", "ephemeral",
               _DATA / "bhavcopy_raw", "sec_bhavdata_full_*.csv",
               "STOCKYA_ARCHIVE_BHAVCOPY_RAW_DAYS", 0,
               "Raw whole-market NSE daily dumps (~1GB/window). Erased after each run."),
        Policy("deals_raw", "downloaded", "overwrite",
               _DATA / "deals", "bulk.csv,block.csv",
               "STOCKYA_ARCHIVE_DEALS_RAW_DAYS", 0,
               "NSE bulk/block deal CSVs (single rolling ~3-month file each). Overwritten daily."),
        Policy("delivery_daily", "generated", "rolling_days",
               _DATA / "delivery", "MTO_*.DAT",
               "STOCKYA_ARCHIVE_DELIVERY_DAILY_DAYS", 45,
               "Daily delivery files generated from bhavcopy for the app-wide delivery reader."),
        Policy("ohlcv", "generated", "rolling_rows",
               _ohlcv_dir(), "*.csv",
               "STOCKYA_ARCHIVE_OHLCV_DAYS", 400,
               "Per-symbol OHLCV. Rolling window applied row-wise at write "
               "(STOCKYA_BHAVCOPY_LOOKBACK_DAYS); orphans removed only if opted in."),
        Policy("delivery_sidecar", "generated", "rolling_rows",
               _DATA / "delivery", "*.NS.csv",
               "STOCKYA_ARCHIVE_DELIVERY_DAYS", 400,
               "Per-symbol delivery side-cars. Rolling window applied row-wise at write."),
        Policy("deals_all", "generated", "external",
               _DATA / "deals", "all.csv",
               "STOCKYA_ARCHIVE_DEALS_ALL_DAYS", 30,
               "Normalized deal ledger. Pruned to a rolling month by block_deals.prune_all_csv."),
        Policy("picks", "generated", "archive_days",
               _DATA, "picks_*.json",
               "STOCKYA_ARCHIVE_PICKS_DAYS", 120,
               "Daily picks output + historical record ('times recommended' reads it). "
               "Never deleted — moved to data/archive/picks after N days."),
    ]


# --------------------------------------------------------------------------- #
# Date extraction from file names.
# --------------------------------------------------------------------------- #
_RE_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")
_RE_DDMMYYYY = re.compile(r"_(\d{2})(\d{2})(\d{4})\b")


def _file_date(name: str) -> Optional[date]:
    m = _RE_ISO.search(name)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    m = _RE_DDMMYYYY.search(name)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _iter(p: Policy):
    for pat in p.pattern.split(","):
        yield from p.directory.glob(pat.strip())


# --------------------------------------------------------------------------- #
# Enforcement.
# --------------------------------------------------------------------------- #
def _apply_one(p: Policy, today: date) -> dict:
    result = {"category": p.category, "rule": p.rule, "removed": 0, "archived": 0}
    try:
        if p.rule == "ephemeral":
            if p.directory.exists() and not _envb("STOCKYA_BHAVCOPY_KEEP_RAW", False):
                shutil.rmtree(p.directory, ignore_errors=True)
                result["removed"] = -1  # whole dir
        elif p.rule == "rolling_days":
            if not p.directory.exists():
                return result
            cutoff = today - timedelta(days=p.days())
            for f in _iter(p):
                d = _file_date(f.name)
                if d is not None and d < cutoff:
                    try:
                        f.unlink()
                        result["removed"] += 1
                    except OSError:
                        pass
        elif p.rule == "archive_days":
            if not p.directory.exists():
                return result
            cutoff = today - timedelta(days=p.days())
            dest = _ARCHIVE / p.category
            for f in _iter(p):
                d = _file_date(f.name)
                if d is not None and d < cutoff:
                    try:
                        dest.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(dest / f.name))
                        result["archived"] += 1
                    except (OSError, shutil.Error):
                        pass
        elif p.rule == "rolling_rows":
            # Window is enforced row-wise at write time. Optionally remove files
            # for symbols no longer in the universe (off by default — safe).
            if _envb("STOCKYA_ARCHIVE_PRUNE_ORPHANS", False) and p.directory.exists():
                try:
                    from .universe import VOLUME_UNIVERSE
                    keep = set()
                    for s in VOLUME_UNIVERSE:
                        base = s[:-3] if s.upper().endswith(".NS") else s
                        keep.update({f"{s}.csv", f"{base}.csv"})
                    for f in _iter(p):
                        if f.name not in keep:
                            f.unlink(missing_ok=True)
                            result["removed"] += 1
                except Exception:  # noqa: BLE001
                    pass
        # overwrite / external: nothing to do here.
    except Exception:  # noqa: BLE001
        log.exception("data_archive: policy %s failed", p.category)
    return result


def apply(today_iso: Optional[str] = None) -> list[dict]:
    """Enforce every retention policy. Never raises. Returns a per-category summary."""
    try:
        today = date.fromisoformat(today_iso) if today_iso else datetime.now().date()
    except (TypeError, ValueError):
        today = datetime.now().date()
    out = [_apply_one(p, today) for p in policies()]
    moved = sum(r["archived"] for r in out)
    pruned = sum(max(0, r["removed"]) for r in out)
    log.info("data_archive: applied %d policies (%d pruned, %d archived)",
             len(out), pruned, moved)
    return out


def describe() -> list[dict]:
    """Human-readable policy table (for logs / docs / an API surface)."""
    rows = []
    for p in policies():
        retention = (
            "erased after each run" if p.rule == "ephemeral"
            else "overwritten daily" if p.rule == "overwrite"
            else "row-window at write" if p.rule == "rolling_rows"
            else "managed by block_deals" if p.rule == "external"
            else f"keep {p.days()}d then archive" if p.rule == "archive_days"
            else f"keep {p.days()}d"
        )
        rows.append({"category": p.category, "kind": p.kind, "rule": p.rule,
                     "retention": retention, "dir": str(p.directory),
                     "pattern": p.pattern, "note": p.note})
    return rows


def _main() -> None:
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "describe"
    if cmd == "apply":
        print(json.dumps(apply(sys.argv[2] if len(sys.argv) > 2 else None), indent=2))
    else:
        print(json.dumps(describe(), indent=2))


if __name__ == "__main__":
    _main()
