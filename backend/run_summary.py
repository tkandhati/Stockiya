"""[SUM] Per-day pick-selection summary — a very-high-level (~1–2 KB) story of
how the day's universe narrowed to 3 picks.

One file per trading day at ``data/summaries/summary_<date>.md``. This is a
READ-ONLY narration of a run that already happened: it never touches selection,
scoring, the portfolio, or the picks JSON. Purely additive.

Two ways in:

  * Live (rich)  — the orchestrator calls :func:`write_summary` at the end of a
    run with the in-scope funnel counts, so the file shows the real per-gate
    elimination (how few names clear Accumulation, Breakout, etc.).
  * Backfill (coarse) — ``python -m backend.run_summary --all`` rebuilds a
    summary from each persisted ``picks_<date>.json``. Historical runs never
    persisted per-gate counts, so backfill shows regime + the 3 picks + the
    near-misses only. Going forward every live run gets the rich funnel.

Determinism: the summary embeds the pick file's own ``generated_at`` (never a
wall clock), so regenerating a given day is byte-identical.

Fix points:
    SUMMARY_DIR     : output directory (default data/summaries)
    _GATE_LABEL     : gate id -> human label shown in the funnel
    _FUNNEL_GATES   : which soft gates get a pass-rate line (order preserved)
    NEAR_MISS_MAX   : how many near-miss names to list per strategy tab
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
SUMMARY_DIR = _DATA_DIR / "summaries"

# Human labels for the gate ids (mirrors orchestrator._GATE_LABEL; kept local so
# this module has no import dependency on the orchestrator).
_GATE_LABEL: dict[str, str] = {
    "ACS": "Accum-screen (cheap)",
    "AC": "Accumulation",
    "LT": "Long-term flow",
    "CS": "Consolidation base",
    "VD": "Volume dry-up / divergence",
    "BR": "Breakout thrust",
}
# Soft gates that get a "pass-rate" line in the funnel, in reading order. These
# are the screens the user cares about ("how few stand out at each step").
_FUNNEL_GATES: tuple[str, ...] = ("AC", "CS", "VD", "BR")

NEAR_MISS_MAX: int = 4


# --------------------------------------------------------------------------- #
# Pure builders
# --------------------------------------------------------------------------- #

def _clean(symbol: str) -> str:
    """Display form: drop the ``.NS`` suffix."""
    return symbol[:-3] if symbol.endswith(".NS") else symbol


def _status_phrase(pick: dict) -> str:
    """One-word stage summary from the gate-confirmation status."""
    status = ((pick.get("gate_confirmation_status") or {}).get("status") or "").lower()
    counts = (pick.get("gate_confirmation_status") or {}).get("counts") or {}
    frac = f"{counts.get('passed', '?')}/{counts.get('total', '?')}"
    if status == "hard_confirmed":
        return f"breakout confirmed ({frac})"
    if status == "composite_qualified":
        return f"pre-breakout base ({frac})"
    return f"{status or 'qualified'} ({frac})"


def _flow_phrase(pick: dict) -> str:
    fi = pick.get("flow_interest") or {}
    if not fi.get("available"):
        return "flow n/a"
    return f"flow {int(fi.get('score', 0))} ({fi.get('level', '?')})"


def _caution(pick: dict) -> str:
    """Advisory contradictions from the accumulation-assessment envelope, as a
    single caution line (empty when none). Read-only — see run_summary docstring."""
    contras = ((pick.get("accumulation_assessment") or {}).get("contradictions")) or []
    if not contras:
        return ""
    first = str(contras[0])
    more = f" (+{len(contras) - 1} more)" if len(contras) > 1 else ""
    return f"\n   ⚠ caution: {first}{more}"


def _pick_line(pick: dict) -> str:
    """One compact line per pick: rank, symbol, score, stage, flow, headline."""
    rank = pick.get("rank", "?")
    sym = _clean(pick.get("symbol", "?"))
    conf = (pick.get("confirmation") or {}).get("score")
    conf_txt = f"conf {conf:.2f}" if isinstance(conf, (int, float)) else "conf ?"
    headline = (pick.get("headline") or "").strip()
    return (
        f"{rank}. **{sym}** · {conf_txt} · {_status_phrase(pick)} · "
        f"{_flow_phrase(pick)}\n   {headline}{_caution(pick)}"
    )


def _near_misses(closest: Optional[dict]) -> str:
    """A single 'just missed' line from the closest-to-firing tabs."""
    if not closest:
        return ""
    bits: list[str] = []
    for tab in ("accumulation", "breakout"):
        rows = closest.get(tab) or []
        names = [_clean(r.get("symbol", "?")) for r in rows[:NEAR_MISS_MAX]]
        if names:
            bits.append(f"{tab[:6]}: {', '.join(names)}")
    return " · ".join(bits)


def _funnel_block(funnel: dict) -> list[str]:
    """Render the elimination funnel. Rich when per-gate counts are present,
    coarse otherwise."""
    screened = funnel.get("screened")
    # Back-compat: older callers passed "data_clean" (== hard-gate survivors).
    hard_gate_survivors = funnel.get("hard_gate_survivors")
    if hard_gate_survivors is None:
        hard_gate_survivors = funnel.get("data_clean")
    survivors = funnel.get("composite_survivors")
    selected = funnel.get("selected")
    tau = funnel.get("tau")
    per_gate: dict[str, Any] = funnel.get("per_gate") or {}
    dh: dict[str, Any] = funnel.get("data_health") or {}

    lines: list[str] = ["## How the field narrowed", ""]

    # Sequential spine (always honest — these are true subsets).
    spine: list[str] = []
    attempted = dh.get("attempted")
    ingested_ok = dh.get("ingested_ok")
    if attempted is not None:
        spine.append(f"{attempted} universe")
    if screened is not None:
        spine.append(f"{screened} scanned")
    if ingested_ok is not None:
        spine.append(f"{ingested_ok} ingested")
    if hard_gate_survivors is not None:
        spine.append(f"{hard_gate_survivors} cleared hard gates")
    if survivors is not None:
        tau_txt = f" S≥{tau:.2f}" if isinstance(tau, (int, float)) else ""
        spine.append(f"{survivors} passed composite{tau_txt}")
    if selected is not None:
        spine.append(f"{selected} picked")
    if spine:
        lines.append(" → ".join(spine))

    # Data-health caveat — makes silent ingest failures visible instead of
    # letting them masquerade as legitimate rejections.
    if dh:
        silent = dh.get("silent_failures") or 0
        cov = dh.get("coverage_pct")
        by = dh.get("failed_by_reason") or {}
        if silent:
            detail = " · ".join(
                f"{v} {k}" for k, v in by.items() if k != "short_history"
            )
            lines.append("")
            lines.append(
                f"⚠️ Data health: {ingested_ok}/{attempted} ingested "
                f"({cov}% coverage) — **{silent} lost to silent failure** "
                f"({detail}). These are NOT market rejections."
            )
        else:
            short = (by or {}).get("short_history", 0)
            short_txt = f" ({short} too new to score)" if short else ""
            lines.append("")
            lines.append(
                f"✓ Data health: {ingested_ok}/{attempted} ingested "
                f"({cov}% coverage){short_txt}."
            )

    # Per-gate pass-rates (independent screens, not a strict sequence).
    if per_gate:
        lines.append("")
        lines.append("Pass-rate per screen (independent, among data-clean):")
        for gid in _FUNNEL_GATES:
            g = per_gate.get(gid)
            if not g:
                continue
            passed = g.get("passed", 0)
            evaluated = g.get("evaluated", 0)
            label = _GATE_LABEL.get(gid, gid)
            lines.append(f"- {label} ({gid}): **{passed}** of {evaluated}")
    return lines


def build_summary_md(
    *,
    date: str,
    generated_at: Optional[str],
    regime: Optional[dict],
    funnel: dict,
    picks: list[dict],
    closest: Optional[dict] = None,
) -> str:
    """Build the ~1–2 KB markdown summary. Pure and deterministic."""
    regime = regime or {}
    regime_on = regime.get("passed")
    regime_txt = regime.get("summary") or (
        "Regime ON" if regime_on else "Regime OFF"
    )

    lines: list[str] = [f"# Stockya pick summary — {date}", ""]
    if generated_at:
        lines.append(f"_Generated {generated_at}_")
        lines.append("")

    n_picks = len(picks)
    lines.append(f"**{regime_txt}** · {n_picks} pick(s) today.")
    lines.append("")

    lines.extend(_funnel_block(funnel))
    lines.append("")

    if picks:
        lines.append(f"## The {n_picks} that stood out")
        lines.append("")
        for p in sorted(picks, key=lambda x: x.get("rank", 999)):
            lines.append(_pick_line(p))
    else:
        lines.append("## No picks")
        lines.append("")
        lines.append(
            "Nothing cleared the composite threshold — capital preserved for "
            "the next real signal."
        )

    near = _near_misses(closest)
    if near:
        lines.append("")
        lines.append(f"_Just missed — {near}_")

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# I/O (atomic)
# --------------------------------------------------------------------------- #

def write_summary(date: str, md: str) -> Path:
    """Atomically write the summary to data/summaries/summary_<date>.md."""
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    path = SUMMARY_DIR / f"summary_{date}.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(md, encoding="utf-8")
    tmp.replace(path)                       # atomic swap
    return path


def summarize_live(
    *,
    today_iso: str,
    generated_at: Optional[str],
    regime: Optional[dict],
    results: list,
    hard_survivors: list,
    survivors: list,
    visible_picks: list[dict],
    tau: Optional[float],
    closest: Optional[dict] = None,
    data_health: Optional[dict] = None,
) -> Path:
    """Called by the orchestrator with in-scope run objects. Computes the rich
    per-gate funnel from ``results`` and writes the file. Best-effort: the
    caller wraps this in try/except so a summary failure never breaks a run."""
    per_gate: dict[str, dict[str, int]] = {}
    for r in results:
        stage_results = getattr(r, "stage_results", {}) or {}
        for gid in _FUNNEL_GATES:
            sr = stage_results.get(gid)
            if sr is None:
                continue
            slot = per_gate.setdefault(gid, {"passed": 0, "evaluated": 0})
            slot["evaluated"] += 1
            if getattr(sr, "passed", False):
                slot["passed"] += 1

    funnel = {
        "screened": len(results),
        # NOTE: `hard_gate_survivors` (previously mislabeled "data_clean") is the
        # count that CLEARED THE HARD GATES, not the count with complete data.
        # True data-completeness now comes from `data_health` (below).
        "hard_gate_survivors": len(hard_survivors),
        "composite_survivors": len(survivors),
        "selected": len(visible_picks),
        "tau": tau,
        "per_gate": per_gate,
        "data_health": data_health,
    }
    md = build_summary_md(
        date=today_iso,
        generated_at=generated_at,
        regime=regime,
        funnel=funnel,
        picks=visible_picks,
        closest=closest,
    )
    return write_summary(today_iso, md)


# --------------------------------------------------------------------------- #
# Backfill (coarse) — rebuild summaries from persisted picks_<date>.json
# --------------------------------------------------------------------------- #

def summarize_from_picks_file(path: Path) -> Optional[Path]:
    """Coarse summary from a persisted picks JSON. Per-gate counts weren't
    persisted historically, so the funnel here shows only the true subsets we
    can recover (picks selected) plus the near-miss tabs."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    date = payload.get("date") or path.stem.replace("picks_", "")
    picks = payload.get("picks") or []
    funnel = {
        "screened": None,
        "data_clean": None,
        "composite_survivors": None,
        "selected": len(picks),
        "tau": None,
        "per_gate": {},
    }
    md = build_summary_md(
        date=date,
        generated_at=payload.get("generated_at"),
        regime=payload.get("regime"),
        funnel=funnel,
        picks=picks,
        closest=payload.get("closest_to_firing"),
    )
    return write_summary(date, md)


def _cli(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Backfill per-day pick summaries.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true",
                   help="Regenerate summaries for every picks_*.json on disk.")
    g.add_argument("--date", help="Regenerate one day (YYYY-MM-DD).")
    ns = ap.parse_args(argv)

    if ns.date:
        p = _DATA_DIR / f"picks_{ns.date}.json"
        if not p.exists():
            print(f"no picks file: {p.name}")
            return 1
        out = summarize_from_picks_file(p)
        print(f"wrote {out}" if out else f"failed: {p.name}")
        return 0 if out else 1

    files = sorted(_DATA_DIR.glob("picks_*.json"))
    n = 0
    for p in files:
        if summarize_from_picks_file(p):
            n += 1
    print(f"wrote {n} summary file(s) to {SUMMARY_DIR}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_cli(sys.argv[1:]))
