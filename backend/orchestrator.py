"""Universe-level orchestrator — the one entry point for "run the picker."

Gates-based flow (PRINCIPLES Section 2):

    Phase 0  [RG]  Market regime gate (one shot, NIFTY 100).
                   FAIL -> write empty picks file with regime info, return.
    Phase 1  per-ticker pipeline (parallel) over Nifty 100.
    Phase 2  [RK]  Confirmation-strength ranking; select top N.
    Phase 3  [PS] + [H]  Build pick payloads for the selected.
    Phase 4  [R]   Render to disk + append final trace rows.
    Phase 5  Portfolio ledger update.

Called by:
  - `backend/nightly.py`              — cron entry, runs after market close
  - `middleware/picks.py`             — on-demand from the API
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from collections import defaultdict

from .indicators import volume_spike_event
from .pipeline import (
    COMPOSITE_TAU,
    COMPOSITE_WEIGHTS,
    PipelineResult,
    append_final_trace,
    hard_gates_passed,
    run_pipeline,
)
from .stages import PER_TICKER_CHAIN
from .stages import breakout as _br_stage
from .stages.hypothesis import build_pick_payload
from .stages.rank import rank_survivors
from .stages.regime import check_regime
from .stages.render import render_picks_response, write_picks_file
from .universe import UNIVERSE

# Stages in canonical order — used for the per-gate breakdown log.
_GATE_ORDER = ["U", "I", "HR", "LT", "CS", "VD", "BR"]
_GATE_LABEL = {
    "U": "Universe", "I": "Ingest", "HR": "Hard rejects",
    "LT": "Long-term flow", "CS": "Consolidation",
    "VD": "Volume/Divergence", "BR": "Breakout",
}

# Near-miss qualification: a ticker must have cleared at least this many
# gates to be interesting enough to surface on the empty-picks page.
_NEAR_MISS_MIN_PASSED = 3


def _collect_near_misses(results: list[PipelineResult], n: int = 5) -> list[dict]:
    """Pick the top-N tickers that cleared the most gates and then failed.

    Each entry shows what passed and the specific reason it failed at the
    killing-blow gate. This is the panel the user sees at the bottom of the
    picks page on a zero-pick day — proof that the chain is filtering on
    real data, not silently dropping things.
    """
    rows = []
    for r in results:
        # Build the ordered chain of evaluated stages
        chain = [
            (sid, r.stage_results[sid])
            for sid in _GATE_ORDER
            if sid in r.stage_results
        ]
        if not chain:
            continue
        last_sid, last_sr = chain[-1]
        # Only "near miss" if the final stage was a failure (else it's a survivor)
        if last_sr.passed:
            continue
        passed = [(sid, sr) for sid, sr in chain if sr.passed]
        if len(passed) < _NEAR_MISS_MIN_PASSED:
            continue

        rows.append({
            "symbol": r.symbol,
            "company": (r.snapshot or {}).get("company") or r.symbol,
            "passed_gates": [
                {
                    "stage_id": sid,
                    "label": _GATE_LABEL.get(sid, sid),
                    "evidence": list(sr.evidence or [])[:2],   # top-2 lines per gate
                }
                for sid, sr in passed
            ],
            "failed_gate": {
                "stage_id": last_sid,
                "label": _GATE_LABEL.get(last_sid, last_sid),
                "reason": last_sr.reason or "",
                "evidence": list(last_sr.evidence or []),       # all failure lines
            },
            "passed_count": len(passed),
        })

    # Closer to passing = more interesting. Tiebreak by latest stage reached.
    def _sort_key(row):
        last_idx = _GATE_ORDER.index(row["failed_gate"]["stage_id"])
        return (-row["passed_count"], -last_idx)

    rows.sort(key=_sort_key)
    return rows[:n]


def _collect_early_volume_signals(results: list[PipelineResult], n: int = 8) -> list[dict]:
    """Surface contextual volume spikes before the full buy chain completes.

    This is intentionally not a buy list. Bullish rows mean "watch for
    follow-through"; bearish rows mean "distribution/exit-risk warning".
    """
    rows: list[dict] = []
    for r in results:
        if r.ohlcv is None or r.ohlcv.empty:
            continue
        event = volume_spike_event(r.ohlcv)
        if event.kind == "neutral":
            continue

        evaluated = [
            sid for sid in _GATE_ORDER
            if sid in r.stage_results
        ]
        failed_sid = None
        failed_reason = ""
        for sid in _GATE_ORDER:
            sr = r.stage_results.get(sid)
            if sr is not None and not sr.passed:
                failed_sid = sid
                failed_reason = sr.reason or ""
                break

        rows.append({
            "symbol": r.symbol,
            "company": (r.snapshot or {}).get("company") or r.symbol,
            "direction": event.direction,
            "kind": event.kind,
            "score": event.score,
            "label": event.label,
            "detail": event.detail,
            "event": event.as_dict(),
            "stage_reached": evaluated[-1] if evaluated else None,
            "failed_gate": (
                {
                    "stage_id": failed_sid,
                    "label": _GATE_LABEL.get(failed_sid, failed_sid) if failed_sid else "",
                    "reason": failed_reason,
                }
                if failed_sid else None
            ),
            "selected": r.selected,
        })

    # Prefer actionable bullish early indications, then high-risk bearish
    # warnings, then sort within each by confidence score.
    priority = {
        "bullish_ignition": 0,
        "early_accumulation": 1,
        "support_absorption": 2,
        "bearish_distribution": 3,
        "climax_warning": 4,
    }
    rows.sort(key=lambda row: (priority.get(row["kind"], 9), -float(row["score"] or 0)))
    return rows[:n]


def _br_sub_check_breakdown(features: dict) -> list[dict]:
    """Decompose a failed [BR] result into per-sub-check status and gap.

    Each sub-check returns the live measurement, the threshold from the
    breakout module's current constants (so Tier-1 relaxations are honored),
    a pass/fail flag, the gap to threshold in percent, and a ready-to-render
    `gap_detail` string.
    """
    checks: list[dict] = []

    close = features.get("close")
    resistance = features.get("resistance_20d")
    vol_ratio = features.get("vol_ratio_today_50d")
    upper_third = features.get("upper_third_ratio")

    if close is not None and resistance is not None and resistance > 0:
        passed = close > resistance
        gap_pct = (resistance - close) / resistance * 100  # +ve = under
        checks.append({
            "name": "resistance_break",
            "label": f"Close > {_br_stage.RESISTANCE_LOOKBACK}d high",
            "current": round(float(close), 2),
            "threshold": round(float(resistance), 2),
            "passed": passed,
            "gap_pct": round(gap_pct, 2),
            "gap_detail": (
                f"close {close:.2f} vs {_br_stage.RESISTANCE_LOOKBACK}d high "
                f"{resistance:.2f} — needs +{gap_pct:.2f}% to clear"
                if not passed
                else f"close {close:.2f} > {resistance:.2f} ({-gap_pct:+.2f}%)"
            ),
        })

    if vol_ratio is not None:
        thr = float(_br_stage.VOLUME_BREAKOUT_MULT)
        passed = vol_ratio >= thr
        gap = thr - vol_ratio
        checks.append({
            "name": "volume_confirm",
            "label": f"Volume >= {thr:.1f}x ADV50",
            "current": round(float(vol_ratio), 3),
            "threshold": thr,
            "passed": passed,
            "gap_pct": round((gap / thr) * 100, 2) if thr > 0 else 0.0,
            "gap_detail": (
                f"volume {vol_ratio:.2f}x ADV50 — needs {thr:.1f}x (short {gap:.2f}x)"
                if not passed
                else f"volume {vol_ratio:.2f}x ADV50 (>= {thr:.1f}x)"
            ),
        })

    if upper_third is not None:
        thr = float(_br_stage.UPPER_THIRD_RATIO_MIN)
        passed = upper_third >= thr
        gap = thr - upper_third
        checks.append({
            "name": "upper_third_close",
            "label": f"Close in top {int((1 - thr) * 100)}% of candle",
            "current": round(float(upper_third), 3),
            "threshold": thr,
            "passed": passed,
            "gap_pct": round(gap * 100, 2),
            "gap_detail": (
                f"closed at {upper_third*100:.0f}% of candle — needs {int(thr*100)}%"
                if not passed
                else f"closed at {upper_third*100:.0f}% of candle (>= {int(thr*100)}%)"
            ),
        })

    return checks


def _collect_ready_to_break(results: list[PipelineResult], n: int = 10) -> list[dict]:
    """Stocks that cleared LT+CS+VD but didn't fire BR today.

    The institutional setup is intact (long-term flow, tight base, dry-up or
    divergence); only the breakout bar hasn't printed yet. Each row exposes
    per-BR-sub-check status and the gap to threshold so the user can see
    what would have to happen for the alert to fire.
    """
    rows: list[dict] = []
    for r in results:
        if r.selected:
            continue
        sr_lt = r.stage_results.get("LT")
        sr_cs = r.stage_results.get("CS")
        sr_vd = r.stage_results.get("VD")
        sr_br = r.stage_results.get("BR")
        if not (sr_lt and sr_lt.passed
                and sr_cs and sr_cs.passed
                and sr_vd and sr_vd.passed):
            continue
        if sr_br is None or sr_br.passed:
            continue

        checks = _br_sub_check_breakdown(sr_br.features or {})
        passing = sum(1 for c in checks if c["passed"])
        total = len(checks)

        lt_score = float(sr_lt.score or 0)
        cs_score = float(sr_cs.score or 0)
        vd_score = float(sr_vd.score or 0)
        setup_strength = (lt_score + cs_score + vd_score) / 3.0
        closeness = (passing / max(1, total)) * 0.7 + setup_strength * 0.3

        rows.append({
            "symbol": r.symbol,
            "company": (r.snapshot or {}).get("company") or r.symbol,
            "lt_score": round(lt_score, 4),
            "cs_score": round(cs_score, 4),
            "vd_score": round(vd_score, 4),
            "setup_strength": round(setup_strength, 4),
            "br_checks": checks,
            "br_passing": passing,
            "br_total": total,
            "br_reason": sr_br.reason or "",
            "closeness_score": round(closeness, 4),
            "last_close": (sr_br.features or {}).get("close"),
        })

    rows.sort(key=lambda x: (
        -x["br_passing"],
        sum(
            c["gap_pct"] for c in x["br_checks"]
            if not c["passed"] and c.get("gap_pct") is not None
        ),
        -x["setup_strength"],
    ))
    return rows[:n]


def _log_gate_breakdown(results: list[PipelineResult]) -> None:
    """Print the live story: how many tickers cleared each gate, with the
    top failure reason. The middleware terminal shows this in real time so
    the user can confirm the chain is doing real work, not silently failing.
    """
    evaluated: dict[str, int] = defaultdict(int)
    passed: dict[str, int] = defaultdict(int)
    fail_reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for r in results:
        for sid, sr in r.stage_results.items():
            if sid not in _GATE_ORDER:
                continue
            evaluated[sid] += 1
            if sr.passed:
                passed[sid] += 1
            else:
                key = (sr.reason or "").split(";")[0].strip()[:50] or "(no reason)"
                fail_reasons[sid][key] += 1

    log.info("  Per-gate breakdown:")
    log.info("    Gate                eval  pass  fail   top failure reason")
    log.info("    ------------------  ----  ----  ----   -------------------------------")
    for sid in _GATE_ORDER:
        if evaluated[sid] == 0:
            log.info("    %-18s  %4d  %4d  %4d   (not reached)",
                     _GATE_LABEL[sid], 0, 0, 0)
            continue
        f = evaluated[sid] - passed[sid]
        top = sorted(fail_reasons[sid].items(), key=lambda x: -x[1])
        top_txt = f"{top[0][1]}x {top[0][0][:30]}" if top else ""
        log.info(
            "    %-18s  %4d  %4d  %4d   %s",
            _GATE_LABEL[sid], evaluated[sid], passed[sid], f, top_txt,
        )

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("orchestrator")

DEFAULT_TOP_N = 3
DEFAULT_ACCOUNT_VALUE = float(os.environ.get("STOCKYA_ACCOUNT_VALUE", "100000"))


def run_universe(
    today_iso: Optional[str] = None,
    top_n: int = DEFAULT_TOP_N,
    account_value: float = DEFAULT_ACCOUNT_VALUE,
    max_workers: int = 10,
    **_kwargs,   # absorb legacy `min_composite` arg silently
) -> dict:
    """Run the gates-based pipeline over Nifty 100. Returns the
    PicksResponse-shaped dict that's also written to disk.
    """
    today_iso = today_iso or datetime.now(IST).date().isoformat()
    demo_mode = os.environ.get("DEMO_MODE", "0") == "1"
    log.info("=" * 76)
    log.info("  PIPELINE RUN  %s   (universe=%d, top_n=%d, account=%.0f, demo=%s)",
             today_iso, len(UNIVERSE), top_n, account_value, demo_mode)
    log.info("=" * 76)

    # ---- Phase 0: Market regime gate ----
    log.info("  [Phase 0/4] Market regime gate ...")
    regime = check_regime()
    log.info("  [Phase 0/4] %s", regime.summary)
    if not regime.passed:
        response = render_picks_response(
            [], today_iso,
            demo_mode=demo_mode,
            regime=regime.as_dict(),
            message=regime.summary,
        )
        path = write_picks_file(response)
        log.info("  ABORT: regime halted -> wrote empty %s", path.name)
        log.info("=" * 76)
        return response

    # ---- Phase 1: per-ticker pipeline (parallel) ----
    log.info("  [Phase 1/4] Running per-ticker chain over %d tickers (%d workers) ...",
             len(UNIVERSE), max_workers)
    results: list[PipelineResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(run_pipeline, sym, PER_TICKER_CHAIN, today_iso): sym
            for sym in UNIVERSE
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                log.exception("pipeline crashed for %s", futures[fut])

    # ---- Soft-gate composite selection (v3 spine) ----
    # A survivor must: (a) clear all hard gates that ran, and (b) score
    # composite S = Σ wᵢ·mᵢ  >=  τ. That's it. The old "all-AND-gates"
    # requirement is replaced by a weighted linear detector — the LLR-optimal
    # thing to do with multiple noisy measurements of one latent (Wyckoff
    # accumulation). Setting τ=0 admits everything; the config controls it.
    hard_survivors = [r for r in results if hard_gates_passed(r.stage_results)]
    survivors = [r for r in hard_survivors if r.composite_score >= COMPOSITE_TAU]

    log.info(
        "  [Phase 1/4] Done. %d processed | %d cleared hard gates | "
        "%d passed composite S>=%.2f",
        len(results), len(hard_survivors), len(survivors), COMPOSITE_TAU,
    )
    if hard_survivors:
        composites = sorted(
            (r.composite_score for r in hard_survivors), reverse=True
        )
        top5 = ", ".join(f"{c:.3f}" for c in composites[:5])
        log.info("  [Phase 1/4] Composite S — top 5: %s  (median %.3f, threshold %.2f)",
                 top5, composites[len(composites) // 2], COMPOSITE_TAU)
    _log_gate_breakdown(results)

    # ---- Phase 2: rank + select ----
    log.info("  [Phase 2/4] Confirmation ranking over %d survivors ...", len(survivors))
    selected = rank_survivors(survivors, top_n=top_n)
    if selected:
        log.info("  [Phase 2/4] Selected %d:", len(selected))
        for pick in selected:
            bonuses = (pick.confirmation_components or {}).get("bonuses_fired") or []
            log.info("    #%d  %-15s  confirmation=%.3f  bonuses=%s",
                     pick.rank, pick.symbol, pick.confirmation_score,
                     ", ".join(bonuses) or "-")
    else:
        log.info("  [Phase 2/4] No survivors — 0 picks today.")
        log.info("             (Run: python -m backend.trace_audit  for the near-miss list.)")

    early_signals = _collect_early_volume_signals(results, n=8)
    if early_signals:
        log.info("  [Phase 2/4] Early volume signals: %d", len(early_signals))

    ready_to_break = _collect_ready_to_break(results, n=10)
    if ready_to_break:
        log.info("  [Phase 2/4] Ready-to-break watchlist: %d", len(ready_to_break))
        for row in ready_to_break[:5]:
            log.info("    %-15s  %d/%d BR sub-checks  closeness=%.2f",
                     row["symbol"], row["br_passing"], row["br_total"],
                     row["closeness_score"])

    # ---- Phase 3: build pick payloads for selected ----
    log.info("  [Phase 3/4] Building pick payloads + position sizing ...")
    pick_payloads: list[dict] = []
    for res in selected:
        try:
            payload = build_pick_payload(
                res, res.snapshot or {},
                account_value=account_value,
                today_iso=today_iso,
            )
            pick_payloads.append(payload)
        except Exception:
            log.exception("build_pick_payload failed for %s", res.symbol)

    # Append FINAL trace rows for every ticker so the RL replay buffer
    # captures the ranking decision (selected and not).
    for r in results:
        try:
            append_final_trace(r, today_iso)
        except Exception:
            log.exception("append_final_trace failed for %s", r.symbol)

    # ---- Phase 4: render to disk ----
    log.info("  [Phase 4/4] Rendering picks_%s.json ...", today_iso)
    message: Optional[str] = None
    near_misses: list[dict] = []
    if not pick_payloads:
        message = "Nothing actionable today — quality over quantity."
        near_misses = _collect_near_misses(results, n=5)
        if near_misses:
            log.info("  [Phase 4/4] Top %d near-misses (cleared >= %d gates):",
                     len(near_misses), _NEAR_MISS_MIN_PASSED)
            for nm in near_misses:
                log.info("    %-15s cleared %d/%d -- failed [%s] %s",
                         nm["symbol"], nm["passed_count"], len(_GATE_ORDER),
                         nm["failed_gate"]["stage_id"],
                         (nm["failed_gate"]["reason"] or "")[:60])
    response = render_picks_response(
        pick_payloads, today_iso,
        demo_mode=demo_mode,
        regime=regime.as_dict(),
        message=message,
        near_misses=near_misses,
        early_signals=early_signals,
        ready_to_break=ready_to_break,
    )
    path = write_picks_file(response)
    log.info("  [Phase 4/4] Wrote %s  (%d pick%s)",
             path.name, len(pick_payloads), "" if len(pick_payloads) == 1 else "s")
    log.info("=" * 76)

    # ---- Phase 5: portfolio ledger ----
    try:
        from .portfolio import record_picks
        added = record_picks(response)
        if added:
            log.info("portfolio.csv: appended %d new picks", added)
    except Exception:
        log.exception("portfolio recording failed (non-fatal)")

    return response


def diagnostics(today_iso: Optional[str] = None) -> dict:
    """Run the pipeline and return per-stage pass/fail counts.

    Useful in dev: shows where in the chain the universe is dropping out.
    """
    today_iso = today_iso or datetime.now(IST).date().isoformat()

    regime = check_regime()
    if not regime.passed:
        return {
            "date": today_iso,
            "regime": regime.as_dict(),
            "stage_counts": {},
            "note": "regime halted; per-ticker chain not run",
        }

    results: list[PipelineResult] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(run_pipeline, sym, PER_TICKER_CHAIN, today_iso): sym
            for sym in UNIVERSE
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                log.exception("pipeline crashed for %s", futures[fut])

    counts: dict[str, dict] = {}
    for r in results:
        for sid, sr in r.stage_results.items():
            counts.setdefault(sid, {"passed": 0, "failed": 0, "scores": []})
            counts[sid]["passed" if sr.passed else "failed"] += 1
            if sr.score:
                counts[sid]["scores"].append(sr.score)
    for sid, c in counts.items():
        scores = c.pop("scores", [])
        c["avg_score"] = round(sum(scores) / len(scores), 3) if scores else None
    return {
        "date": today_iso,
        "regime": regime.as_dict(),
        "stage_counts": counts,
        "survivors": sum(1 for r in results if r.passed_gates),
    }
