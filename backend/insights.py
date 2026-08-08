"""[INS] Insights — window-cohort ("bundle") analysis of picks + eliminations.

A SEPARATE, READ-ONLY, DETERMINISTIC module. It never touches selection,
scoring, the portfolio, the picks JSON, or the trace files — it only *reads*
artifacts that other stages already wrote, and emits its own report files under
``data/insights/``. Purely additive, in the spirit of run_summary.py.

WHAT IT ANSWERS
    1. Picks & elimination by each check — an aggregate funnel over a whole
       20-trading-day window: for every gate (U, I, HR, LT, AC, CS, VD, BR)
       how many names were evaluated, passed, and eliminated, plus a sample of
       the names dropped at each check.
    2. Every pick tracked by stock AND date — a per-pick lifecycle ledger keyed
       by (symbol, entry_date), with each pick's open/closed state, hold length,
       exit reason and realized P&L pulled from portfolio.csv.
    3. Bundles — the picks entered in a window form a *bundle* (an entry-cohort).
       A bundle stays OPEN, carried into the next window's report, until EVERY
       pick in it has reached a terminal portfolio exit; only then is it a CLOSED
       bundle. Each run reports the closed bundles and the ones still open for
       the next window.

PLANNED (not built — see WISHLIST.md "Lens 3 — indicator attribution")
    A future lens will attribute *which check is helping vs which to penalize or
    remove*, by margin-lift over MATURED picks (the closed side of the ledger).
    Advisory only — the champion-challenger tuner keeps ownership of weight
    changes. Deferred until outcomes.jsonl has enough matured picks per stage.

THE WINDOW / BUNDLE MODEL
    Trading-day calendar = the sorted set of ``picks_<date>.json`` dates. Those
    files are written once per real trading day (see backend/trading_day.py), so
    they ARE the calendar — no NSE holiday table is needed.

        W1 [trading days 1..20]   W2 [21..40]   W3 [41..60] ...   (non-overlapping)

    Bundle_N  = picks whose entry_date falls inside window N.
    closed    = portfolio status is terminal AND exit_date <= window end.
    A bundle is CLOSED iff all its (non-declined) picks are closed as-of the
    window end; otherwise OPEN and carried forward.

DETERMINISM
    Every "as-of" is a *trading date* (the target window's last day), never a
    wall clock. Closed-ness compares ``exit_date <= as_of``. Re-running the same
    window over the same files therefore produces a byte-identical report. JSON
    is written with sorted keys; markdown embeds only dates.

USAGE
    python -m backend.insights              # latest complete window
    python -m backend.insights --all        # every complete window
    python -m backend.insights --window 3   # a specific window (1-based)
    python -m backend.insights --dry-run    # print, write nothing

Fix points (top-of-file constants):
    WINDOW_TRADING_DAYS  : window length in trading days (default 20)
    FUNNEL_STAGES        : ordered (stage_id, label) the funnel reports
    CLOSED_STATUSES      : portfolio statuses that mean "position is closed"
    TOP_ELIMINATED_N     : sample names listed per check in the funnel
    INSIGHTS_DIRNAME     : output subdir under data/
    SCHEMA_VERSION       : bump when the JSON report shape changes
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from .datekeys import date_from_filename

log = logging.getLogger("insights")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"

# --------------------------------------------------------------------------- #
# Fix points
# --------------------------------------------------------------------------- #

WINDOW_TRADING_DAYS: int = 20
SCHEMA_VERSION: int = 1
INSIGHTS_DIRNAME: str = "insights"
TOP_ELIMINATED_N: int = 8

# Ordered checks shown in the elimination funnel. Hard gates first (U/I/HR),
# then the scoring gates in run order. Human labels mirror
# run_summary._GATE_LABEL so the two narrations agree.
FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
    ("U",  "Universe"),
    ("I",  "Ingest / data-clean"),
    ("HR", "Hard rejects"),
    ("LT", "Long-term flow"),
    ("AC", "Accumulation"),
    ("CS", "Consolidation base"),
    ("VD", "Volume dry-up / divergence"),
    ("BR", "Breakout thrust"),
)

# Portfolio statuses that mean a position is CLOSED (P&L booked or replaced).
# Mirrors backend/stages/outcome.py:_CLOSED_STATUSES plus "superseded" — a
# superseded pick was replaced by a newer one, so it no longer holds a bundle
# open. Kept as its own fix point so the two modules can be tuned independently.
CLOSED_STATUSES: frozenset[str] = frozenset({
    "stopped", "target_hit", "timed_out", "closed",
    "exit_stop", "exit_t2", "exit_t1_full", "exit_end_date",
    "exit_final", "exit_time_stop", "exit_distribution",
    "superseded",
})

# A pick the user explicitly declined never entered a position. It is treated as
# RESOLVED (never open) so it can't hold a bundle open forever — mirrors the
# outcome tracker, which skips declined rows entirely.
_DECLINED_OWNERSHIP: str = "declined"

_PICKS_FILE_RE = re.compile(r"^picks_(\d{4}-\d{2}-\d{2})\.json$")
_TRACE_FILE_RE = re.compile(r"^run_(\d{4}-\d{2}-\d{2})_.+\.jsonl$")


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #

@dataclass
class Window:
    """A non-overlapping block of WINDOW_TRADING_DAYS trading days."""
    index: int                      # 1-based
    start_iso: str
    end_iso: str
    trading_days: list[str] = field(default_factory=list)


@dataclass
class PickState:
    """Lifecycle of one pick, keyed by (symbol, entry_date), as-of a date."""
    symbol: str
    entry_date: str
    pick_id: str
    trace_id: str
    status: str
    closed: bool
    terminal_kind: str = ""         # "exit" | "superseded" | "declined" | ""
    exit_date: Optional[str] = None
    exit_reason: str = ""
    pnl_pct: Optional[float] = None
    horizon_days: Optional[int] = None
    days_held: Optional[int] = None
    ownership: str = ""


@dataclass
class Bundle:
    """The entry-cohort of picks born in one window."""
    window_index: int
    start_iso: str
    end_iso: str
    picks: list[PickState] = field(default_factory=list)
    closed: bool = False            # every non-declined pick is closed as-of as_of

    @property
    def label(self) -> str:
        return f"W{self.window_index}"


@dataclass
class StageFunnel:
    stage_id: str
    label: str
    evaluated: int
    passed: int
    failed: int
    fail_rate: float
    sample_eliminated: list[str] = field(default_factory=list)


@dataclass
class WindowInsight:
    """The full report for one target window."""
    schema_version: int
    window: Window
    as_of: str
    # Lens 1
    spine: dict
    funnel: list[StageFunnel]
    # Lens 2
    new_bundle: Bundle
    closed_this_window: list[Bundle]
    open_carry_forward: list[Bundle]
    already_closed_count: int


# --------------------------------------------------------------------------- #
# Calendar + windows (pure)
# --------------------------------------------------------------------------- #

def trading_days(data_dir: Path = _DATA_DIR) -> list[str]:
    """Sorted ISO dates of every ``picks_<date>.json`` — the real trading days."""
    if not data_dir.exists():
        return []
    days: list[str] = []
    for p in data_dir.iterdir():
        if not p.is_file():
            continue
        m = _PICKS_FILE_RE.match(p.name)
        if m:
            days.append(m.group(1))
    return sorted(set(days))


def partition_windows(
    days: list[str],
    size: int = WINDOW_TRADING_DAYS,
    include_partial: bool = False,
) -> list[Window]:
    """Slice the ordered trading-day list into non-overlapping windows.

    Complete windows (exactly `size` days) always returned. The trailing
    partial window is returned only when `include_partial=True`, so the default
    caller analyses whole 20-day cohorts and never a half-formed one.
    """
    windows: list[Window] = []
    for i in range(0, len(days), size):
        chunk = days[i:i + size]
        if len(chunk) < size and not include_partial:
            break
        windows.append(Window(
            index=i // size + 1,
            start_iso=chunk[0],
            end_iso=chunk[-1],
            trading_days=list(chunk),
        ))
    return windows


# --------------------------------------------------------------------------- #
# Portfolio ledger (pure classification)
# --------------------------------------------------------------------------- #

def read_portfolio(data_dir: Path = _DATA_DIR) -> list[dict]:
    path = data_dir / "portfolio.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _to_float(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None


def classify_pick(row: dict, as_of: str) -> PickState:
    """Turn a portfolio row into a PickState as-of `as_of` (a trading date).

    CLOSED iff the row's status is terminal AND (for a dated exit) the exit
    happened on or before `as_of`. Comparing exit_date to `as_of` — not to
    "now" — is what makes every historical window report reproducible.
    """
    status = (row.get("status") or "open").strip().lower()
    ownership = (row.get("ownership") or "").strip().lower()
    entry_date = row.get("entry_date") or ""
    exit_date = row.get("exit_date") or None

    if ownership == _DECLINED_OWNERSHIP:
        closed, terminal_kind = True, "declined"
    elif status in CLOSED_STATUSES:
        # A terminal status only counts once its exit is on-or-before as_of;
        # a future-dated exit is still open from as_of's point of view.
        if exit_date and exit_date > as_of:
            closed, terminal_kind = False, ""
        else:
            closed = True
            terminal_kind = "superseded" if status == "superseded" else "exit"
    else:
        closed, terminal_kind = False, ""

    days_held: Optional[int] = None
    try:
        ed = date.fromisoformat(entry_date)
        end = date.fromisoformat(exit_date) if (closed and exit_date) else date.fromisoformat(as_of)
        days_held = max((end - ed).days, 0)
    except (ValueError, TypeError):
        days_held = None

    return PickState(
        symbol=row.get("symbol", ""),
        entry_date=entry_date,
        pick_id=row.get("pick_id", ""),
        trace_id=row.get("trace_id", ""),
        status=status,
        closed=closed,
        terminal_kind=terminal_kind if closed else "",
        exit_date=exit_date if closed else None,
        exit_reason=(row.get("exit_reason") or "") if closed else "",
        pnl_pct=_to_float(row.get("pnl_pct")) if closed else None,
        horizon_days=_to_int(row.get("horizon_days")),
        days_held=days_held,
        ownership=ownership,
    )


def build_bundle(portfolio: list[dict], window: Window, as_of: str) -> Bundle:
    """The entry-cohort bundle for `window`, classified as-of `as_of`.

    A bundle is CLOSED iff every one of its actionable (non-declined) picks is
    closed. A bundle with only declined picks, or none at all, is closed
    vacuously (nothing left to carry forward).
    """
    start, end = window.start_iso, window.end_iso
    picks = [
        classify_pick(r, as_of)
        for r in portfolio
        if start <= (r.get("entry_date") or "") <= end
    ]
    picks.sort(key=lambda p: (p.entry_date, p.symbol))
    actionable = [p for p in picks if p.terminal_kind != "declined"]
    closed = all(p.closed for p in actionable) if actionable else True
    return Bundle(
        window_index=window.index,
        start_iso=start,
        end_iso=end,
        picks=picks,
        closed=closed,
    )


# --------------------------------------------------------------------------- #
# Elimination funnel (aggregate over a window's trace files)
# --------------------------------------------------------------------------- #

def _iter_window_trace_files(traces_dir: Path, day_set: set[str]):
    """Yield trace-file Paths whose embedded day is in `day_set`, via one
    ``os.scandir`` pass. O(files) once, not O(files × window-days) — essential
    when the traces dir holds thousands of files.
    """
    try:
        with os.scandir(traces_dir) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                m = _TRACE_FILE_RE.match(entry.name)
                if m and m.group(1) in day_set:
                    yield Path(entry.path)
    except OSError:
        return


def build_funnel(data_dir: Path, window: Window) -> tuple[dict, list[StageFunnel]]:
    """Aggregate per-stage pass/eliminate counts over the window's trading days.

    Reads only the ``run_<day>_*.jsonl`` traces whose day is in the window. The
    traces dir can hold thousands of files, so discovery is a SINGLE ``scandir``
    pass filtered by an in-memory set of the window's days — not one directory
    glob per day. For each check: how many (day,ticker) traces reached it, how
    many passed, and a de-duplicated alphabetical sample of the names dropped.
    """
    traces_dir = data_dir / "traces"
    known_ids = [sid for sid, _ in FUNNEL_STAGES]
    label_of = dict(FUNNEL_STAGES)

    evaluated = {sid: 0 for sid in known_ids}
    passed = {sid: 0 for sid in known_ids}
    eliminated_names: dict[str, set[str]] = {sid: set() for sid in known_ids}

    ticker_days = 0
    selected = 0
    selected_picks: set[str] = set()

    day_set = set(window.trading_days)
    if traces_dir.exists():
        for path in _iter_window_trace_files(traces_dir, day_set):
            ticker_days += 1
            seen_stage: set[str] = set()
            try:
                with path.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        stage = r.get("stage")
                        sym = r.get("symbol", "")
                        if stage == "FINAL":
                            if r.get("selected"):
                                selected += 1
                                if sym:
                                    selected_picks.add(sym)
                            continue
                        if stage not in evaluated or stage in seen_stage:
                            continue
                        seen_stage.add(stage)
                        evaluated[stage] += 1
                        if r.get("passed"):
                            passed[stage] += 1
                        elif sym:
                            eliminated_names[stage].add(sym)
            except OSError:
                continue

    funnel: list[StageFunnel] = []
    for sid in known_ids:
        ev = evaluated[sid]
        pa = passed[sid]
        fa = ev - pa
        funnel.append(StageFunnel(
            stage_id=sid,
            label=label_of[sid],
            evaluated=ev,
            passed=pa,
            failed=fa,
            fail_rate=round(fa / ev, 4) if ev else 0.0,
            sample_eliminated=sorted(eliminated_names[sid])[:TOP_ELIMINATED_N],
        ))

    spine = {
        "ticker_days_screened": ticker_days,
        "picks_selected_rows": selected,
        "distinct_picks_selected": len(selected_picks),
    }
    return spine, funnel


# --------------------------------------------------------------------------- #
# Assemble one window's insight
# --------------------------------------------------------------------------- #

def build_window_insight(
    data_dir: Path,
    windows: list[Window],
    target_index: int,
) -> WindowInsight:
    """Full report for window `target_index` (1-based).

    Bundle status is evaluated at two points — the target window's end and the
    previous window's end — so "closed this window" means *newly* closed since
    the last report, not just "closed at some point".
    """
    window = next(w for w in windows if w.index == target_index)
    as_of = window.end_iso
    prev_as_of = next(
        (w.end_iso for w in windows if w.index == target_index - 1), None
    )

    portfolio = read_portfolio(data_dir)
    spine, funnel = build_funnel(data_dir, window)

    # Bundles from every window up to and including the target.
    prior_windows = [w for w in windows if w.index <= target_index]
    bundles_now = {w.index: build_bundle(portfolio, w, as_of) for w in prior_windows}
    bundles_prev = (
        {w.index: build_bundle(portfolio, w, prev_as_of)
         for w in prior_windows if w.index < target_index}
        if prev_as_of else {}
    )

    def has_picks(b: Bundle) -> bool:
        return len(b.picks) > 0

    closed_this_window: list[Bundle] = []
    open_carry_forward: list[Bundle] = []
    already_closed_count = 0
    for idx, b in bundles_now.items():
        if not has_picks(b):
            continue
        was_closed = bundles_prev.get(idx).closed if idx in bundles_prev else False
        if b.closed and not was_closed:
            closed_this_window.append(b)
        elif b.closed and was_closed:
            already_closed_count += 1
        elif not b.closed:
            open_carry_forward.append(b)

    closed_this_window.sort(key=lambda b: b.window_index)
    open_carry_forward.sort(key=lambda b: b.window_index)

    return WindowInsight(
        schema_version=SCHEMA_VERSION,
        window=window,
        as_of=as_of,
        spine=spine,
        funnel=funnel,
        new_bundle=bundles_now[target_index],
        closed_this_window=closed_this_window,
        open_carry_forward=open_carry_forward,
        already_closed_count=already_closed_count,
    )


# --------------------------------------------------------------------------- #
# Rendering (pure)
# --------------------------------------------------------------------------- #

def _clean(symbol: str) -> str:
    return symbol[:-3] if symbol.endswith(".NS") else symbol


def _pick_line(p: PickState) -> str:
    sym = _clean(p.symbol)
    if p.closed:
        if p.terminal_kind == "declined":
            return f"  - {sym} · entered {p.entry_date} · CLOSED (declined)"
        pnl = f"{p.pnl_pct:+.1f}%" if isinstance(p.pnl_pct, (int, float)) else "n/a"
        held = f"{p.days_held}d" if p.days_held is not None else "?"
        reason = p.exit_reason or p.status
        return (f"  - {sym} · entered {p.entry_date} · CLOSED {pnl} "
                f"after {held} — {reason}")
    held = f"{p.days_held}d" if p.days_held is not None else "?"
    return f"  - {sym} · entered {p.entry_date} · OPEN (held {held})"


def _bundle_block(b: Bundle) -> list[str]:
    n = len(b.picks)
    n_closed = sum(1 for p in b.picks if p.closed)
    head = (f"**{b.label}** ({b.start_iso} → {b.end_iso}) · "
            f"{n} pick(s), {n_closed} closed")
    lines = [head]
    lines += [_pick_line(p) for p in b.picks]
    return lines


def render_md(ins: WindowInsight) -> str:
    w = ins.window
    lines: list[str] = [
        f"# Insights — window W{w.index} ({w.start_iso} → {w.end_iso})",
        "",
        f"_As of {ins.as_of} · {len(w.trading_days)} trading days · "
        f"schema v{ins.schema_version}_",
        "",
    ]

    # Lens 1 — elimination funnel
    lines.append("## Picks & elimination by each check")
    lines.append("")
    sp = ins.spine
    lines.append(
        f"{sp['ticker_days_screened']} ticker-days screened → "
        f"{sp['distinct_picks_selected']} distinct picks selected "
        f"({sp['picks_selected_rows']} selection rows)."
    )
    lines.append("")
    lines.append("| Check | Evaluated | Passed | Eliminated | Fail rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for s in ins.funnel:
        lines.append(
            f"| {s.label} ({s.stage_id}) | {s.evaluated} | {s.passed} | "
            f"{s.failed} | {s.fail_rate:.0%} |"
        )
    lines.append("")
    # Sample names dropped at the meaningful gates.
    dropped = [s for s in ins.funnel if s.sample_eliminated]
    if dropped:
        lines.append("Sample names eliminated at each check:")
        for s in dropped:
            names = ", ".join(_clean(n) for n in s.sample_eliminated)
            lines.append(f"- **{s.stage_id}**: {names}")
        lines.append("")

    # Lens 2 — bundles
    lines.append("## Bundles")
    lines.append("")
    lines.append("### New this window")
    lines.append("")
    if ins.new_bundle.picks:
        lines.extend(_bundle_block(ins.new_bundle))
    else:
        lines.append(f"_No picks entered in W{w.index}._")
    lines.append("")

    lines.append("### Closed this window (booked)")
    lines.append("")
    if ins.closed_this_window:
        for b in ins.closed_this_window:
            lines.extend(_bundle_block(b))
            lines.append("")
    else:
        lines.append("_No bundle fully closed in this window._")
        lines.append("")

    lines.append("### Open — carried to next window")
    lines.append("")
    if ins.open_carry_forward:
        for b in ins.open_carry_forward:
            if b.window_index == ins.new_bundle.window_index:
                # Just listed in full under "New this window" — don't repeat it,
                # only note that it carries forward.
                n_open = sum(1 for p in b.picks if not p.closed)
                lines.append(
                    f"**{b.label}** — the new bundle above; {n_open} pick(s) "
                    f"still open, carried forward."
                )
                lines.append("")
            else:
                lines.extend(_bundle_block(b))
                lines.append("")
    else:
        lines.append("_Nothing open — every bundle is booked._")
        lines.append("")

    if ins.already_closed_count:
        lines.append(
            f"_({ins.already_closed_count} bundle(s) closed in an earlier "
            f"window, not repeated here.)_"
        )

    return "\n".join(lines).rstrip() + "\n"


def to_json(ins: WindowInsight) -> dict:
    """Serializable report. Sorted-key JSON downstream keeps runs byte-identical."""
    return {
        "schema_version": ins.schema_version,
        "window": asdict(ins.window),
        "as_of": ins.as_of,
        "spine": ins.spine,
        "funnel": [asdict(s) for s in ins.funnel],
        "new_bundle": asdict(ins.new_bundle),
        "closed_this_window": [asdict(b) for b in ins.closed_this_window],
        "open_carry_forward": [asdict(b) for b in ins.open_carry_forward],
        "already_closed_count": ins.already_closed_count,
    }


# --------------------------------------------------------------------------- #
# I/O (atomic)
# --------------------------------------------------------------------------- #

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _report_stem(w: Window) -> str:
    return f"window_{w.index:02d}_{w.start_iso}_{w.end_iso}"


def write_insight(data_dir: Path, ins: WindowInsight) -> dict[str, Path]:
    """Write the markdown + JSON report. Returns the two paths."""
    out_dir = data_dir / INSIGHTS_DIRNAME
    stem = _report_stem(ins.window)
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    _atomic_write(md_path, render_md(ins))
    _atomic_write(
        json_path,
        json.dumps(to_json(ins), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )
    return {"md": md_path, "json": json_path}


def write_open_bundles_state(data_dir: Path, ins: WindowInsight) -> Path:
    """Persist the open set as-of this window — the 'open for next window' file.

    A derived convenience output (everything here is recomputable from the
    immutable artifacts), so it is always safe to overwrite. Tolerant readers
    should treat a missing/garbled file as 'no state yet'.
    """
    out_dir = data_dir / INSIGHTS_DIRNAME
    payload = {
        "schema_version": SCHEMA_VERSION,
        "as_of": ins.as_of,
        "window_index": ins.window.index,
        "open_bundles": [asdict(b) for b in ins.open_carry_forward],
    }
    path = out_dir / "open_bundles.json"
    _atomic_write(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )
    return path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_insights(
    data_dir: Path = _DATA_DIR,
    which: str = "latest",
    window_index: Optional[int] = None,
    dry_run: bool = False,
) -> dict:
    """Compute + write insight report(s).

    `which`:
        "latest"  — the most recent COMPLETE window (default)
        "all"     — every complete window
        "one"     — the window given by `window_index`
    """
    days = trading_days(data_dir)
    windows = partition_windows(days, WINDOW_TRADING_DAYS)
    summary = {
        "trading_days": len(days),
        "complete_windows": len(windows),
        "written": [],
        "skipped_reason": None,
    }
    if not windows:
        summary["skipped_reason"] = (
            f"need {WINDOW_TRADING_DAYS} trading days for one window; "
            f"have {len(days)}"
        )
        return summary

    if which == "all":
        targets = [w.index for w in windows]
    elif which == "one":
        if window_index is None or not any(w.index == window_index for w in windows):
            summary["skipped_reason"] = f"no complete window {window_index}"
            return summary
        targets = [window_index]
    else:
        targets = [windows[-1].index]

    for idx in targets:
        ins = build_window_insight(data_dir, windows, idx)
        if dry_run:
            summary["written"].append({"window": idx, "dry_run": True})
            continue
        paths = write_insight(data_dir, ins)
        # The open-bundles state reflects the LATEST target written.
        if idx == max(targets):
            write_open_bundles_state(data_dir, ins)
        summary["written"].append({
            "window": idx,
            "md": str(paths["md"]),
            "closed_this_window": len(ins.closed_this_window),
            "open_carry_forward": len(ins.open_carry_forward),
        })
    return summary


def _cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true",
                   help="Write a report for every complete window.")
    g.add_argument("--window", type=int, metavar="N",
                   help="Write the report for complete window N (1-based).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print, but write no files.")
    ap.add_argument("--data-dir", type=Path, default=_DATA_DIR,
                    help="Override the data directory (tests / alt trees).")
    ns = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] insights: %(message)s"
    )
    which = "all" if ns.all else ("one" if ns.window else "latest")
    summary = run_insights(
        data_dir=ns.data_dir,
        which=which,
        window_index=ns.window,
        dry_run=ns.dry_run,
    )
    log.info("insights: %s", json.dumps(summary, default=str))
    if summary.get("skipped_reason"):
        print(summary["skipped_reason"])
    for w in summary["written"]:
        if w.get("dry_run"):
            print(f"[dry-run] would write window {w['window']}")
        else:
            print(f"wrote window {w['window']}: {w['md']} "
                  f"(closed {w['closed_this_window']}, "
                  f"open {w['open_carry_forward']})")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_cli(sys.argv[1:]))
