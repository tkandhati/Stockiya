"""Universe-level orchestrator — the one entry point for "run the picker."

Gates-based flow (PRINCIPLES Section 2):

    Phase 0  [RG]  Market regime gate (one shot, NIFTY 100 index proxy).
                   FAIL -> write empty picks file with regime info, return.
    Phase 1  per-ticker pipeline (parallel) over the scan universe.
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

from .pipeline import (
    COMPOSITE_TAU,
    COMPOSITE_WEIGHTS,
    PipelineResult,
    append_final_trace,
    hard_gates_passed,
    run_pipeline,
)
from .stages import PER_TICKER_CHAIN
from .stages.hypothesis import build_pick_payload
from .stages.rank import rank_lead_fallback, rank_survivors
from .stages.regime import check_regime
from .stages.render import render_picks_response, write_picks_file
from .picks_reconcile import (
    reconcile_picks_against_portfolio,
    split_visible_from_suppressed,
)
from .picks_diff import attach_change_diffs, attach_pick_history
from .horizon import estimated_horizon_days
from .trading_day import (
    classify_post_ingest,
    classify_pre_pipeline,
    load_previous_picks,
    log_no_fire,
)
from .universe import VOLUME_UNIVERSE

# Stages in canonical order — used for the per-gate breakdown log.
_GATE_ORDER = ["U", "I", "HR", "ACS", "AC", "LTV", "LT", "CS", "VD", "BR"]
_GATE_LABEL = {
    "U": "Universe", "I": "Ingest", "HR": "Hard rejects",
    "LTV": "LT-distribution veto",
    "ACS": "Accum-Screen", "AC": "Accumulation",
    "LT": "Long-term flow", "CS": "Consolidation",
    "VD": "Volume/Divergence", "BR": "Breakout",
}

# Strategy grouping for the "Closest to Firing" empty-state panel.
# Each tab shows tickers ranked by the sum of that strategy's weighted margins.
_ACCUM_STAGES: tuple[str, ...] = ("ACS", "AC")
_BREAKOUT_STAGES: tuple[str, ...] = ("LT", "CS", "VD", "BR")


def _weighted_margin(r: PipelineResult, stage_ids: tuple[str, ...]) -> float:
    """Σ wᵢ · mᵢ  over a subset of stages (a strategy). 0 for non-passing stages.

    Used to rank tickers within a strategy tab (accumulation vs breakout).
    """
    total = 0.0
    for sid in stage_ids:
        w = COMPOSITE_WEIGHTS.get(sid, 0.0)
        if w == 0.0:
            continue
        sr = r.stage_results.get(sid)
        if sr is None or not sr.passed:
            continue
        total += w * float(sr.score or 0.0)
    return total


def _pulled_down_by(r: PipelineResult) -> dict:
    """The one stage that, if it fully fired, would move S the most.

    Formally: argmax over scored stages of  wᵢ · (1 − mᵢ)  where mᵢ = 0 for
    non-passing stages. Returns {stage_id, label, current_margin, weight,
    reason}. This is the "one thing to fix" hint the trader uses to decide
    whether the ticker is close enough to watch tomorrow.
    """
    best_sid = None
    best_deficit = -1.0
    for sid, w in COMPOSITE_WEIGHTS.items():
        if w == 0.0 or sid in {"U", "I", "HR"}:
            continue
        sr = r.stage_results.get(sid)
        margin = float(sr.score or 0.0) if (sr is not None and sr.passed) else 0.0
        deficit = w * (1.0 - margin)
        if deficit > best_deficit:
            best_deficit = deficit
            best_sid = sid
    if best_sid is None:
        return {"stage_id": None, "label": "", "current_margin": 0.0,
                "weight": 0.0, "reason": ""}
    sr = r.stage_results.get(best_sid)
    return {
        "stage_id": best_sid,
        "label": _GATE_LABEL.get(best_sid, best_sid),
        "current_margin": round(float(sr.score or 0.0) if sr and sr.passed else 0.0, 4),
        "weight": round(float(COMPOSITE_WEIGHTS.get(best_sid, 0.0)), 4),
        "reason": (sr.reason or "") if sr is not None else "no result",
    }


def _closest_row(
    r: PipelineResult, tau: float, market_pcts: Optional[list] = None
) -> dict:
    """One compact row for the closest-to-firing panel.

    Enriched (2026-07-27) with the SCORING-NEUTRAL institutional-flow strength
    for this dropped candidate: `flow_interest` shows how strong bulk-deal +
    delivery accumulation is (and how it ranks vs the normal), while
    `pulled_down_by` already says which gate dropped it — together answering
    'what strength does it carry and why was it dropped'.
    """
    # Fetch the delivery advisory ONCE and reuse it for flow (avoids a 2nd read),
    # and attach it so the closest-to-firing panel can show the same delivery
    # ladder (today / week / 15 / 30) the pick cards show.
    try:
        from .delivery import delivery_advisory
        adv = delivery_advisory(r.symbol)
    except Exception:
        adv = None
    try:
        from .flow_interest import flow_interest
        fi = flow_interest(r.symbol, delivery=adv, market_pcts=market_pcts)
    except Exception:
        fi = None
    return {
        "symbol": r.symbol,
        "company": (r.snapshot or {}).get("company") or r.symbol,
        "composite_score": round(float(r.composite_score or 0.0), 4),
        "gap_to_tau": round(float(tau - (r.composite_score or 0.0)), 4),
        "pulled_down_by": _pulled_down_by(r),
        "flow_interest": fi,
        "delivery": adv,
    }


def _collect_closest_to_firing(
    results: list[PipelineResult],
    tau: float,
    n_per_tab: int = 5,
) -> dict:
    """Top-N tickers that DID NOT fire, grouped by strategy leader.

    Three tabs, each independently ranked; a ticker may appear in more than
    one tab if strong in both strategies.

      - accumulation: rank by Σ wᵢ · mᵢ over {ACS, AC}
      - breakout:     rank by Σ wᵢ · mᵢ over {LT, CS, VD, BR}
      - overall:      rank by composite S

    Only tickers that (a) passed hard gates AND (b) failed the S ≥ τ cut are
    eligible. Every row has the same 4-field shape — trader-UI rule: minimal
    columns, every one earns its place.
    """
    eligible = [
        r for r in results
        if hard_gates_passed(r.stage_results) and not r.selected
        and (r.composite_score or 0.0) < tau
    ]
    if not eligible:
        return {"accumulation": [], "breakout": [], "overall": []}

    acc_ranked = sorted(
        eligible, key=lambda r: -_weighted_margin(r, _ACCUM_STAGES)
    )
    br_ranked = sorted(
        eligible, key=lambda r: -_weighted_margin(r, _BREAKOUT_STAGES)
    )
    all_ranked = sorted(
        eligible, key=lambda r: -(r.composite_score or 0.0)
    )

    # Market delivery cross-section loaded ONCE for the 'strength vs normal'
    # percentile on every flow-enriched row below.
    try:
        from .delivery import latest_market_pcts
        market_pcts = latest_market_pcts()
    except Exception:
        market_pcts = []

    return {
        "accumulation": [_closest_row(r, tau, market_pcts) for r in acc_ranked[:n_per_tab]
                         if _weighted_margin(r, _ACCUM_STAGES) > 0],
        "breakout":     [_closest_row(r, tau, market_pcts) for r in br_ranked[:n_per_tab]
                         if _weighted_margin(r, _BREAKOUT_STAGES) > 0],
        "overall":      [_closest_row(r, tau, market_pcts) for r in all_ranked[:n_per_tab]
                         if (r.composite_score or 0.0) > 0],
    }


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

# Raised 3 -> 5 on 2026-08-03 so the extra genuine pre-breakout bases that the
# BR-relief reweight now lifts over tau (see pipeline.TRIGGER_BR_SHRINK_FRAC)
# actually surface. A cap, not a floor: thin days still show fewer.
DEFAULT_TOP_N = 5
DEFAULT_ACCOUNT_VALUE = float(os.environ.get("STOCKYA_ACCOUNT_VALUE", "100000"))


def run_universe(
    today_iso: Optional[str] = None,
    top_n: int = DEFAULT_TOP_N,
    account_value: float = DEFAULT_ACCOUNT_VALUE,
    max_workers: int = 10,
    **_kwargs,   # absorb legacy `min_composite` arg silently
) -> dict:
    """Run the gates-based pipeline over the scan universe. Returns the
    PicksResponse-shaped dict that's also written to disk.
    """
    today_iso = today_iso or datetime.now(IST).date().isoformat()
    demo_mode = os.environ.get("DEMO_MODE", "0") == "1"
    log.info("=" * 76)
    log.info("  PIPELINE RUN  %s   (universe=%d, top_n=%d, account=%.0f, demo=%s)",
             today_iso, len(VOLUME_UNIVERSE), top_n, account_value, demo_mode)
    log.info("=" * 76)

    # ---- Non-trading-day guard (weekend) ----
    # On Sat/Sun the pipeline does NOT write a new picks file and does NOT
    # touch the portfolio ledger. The middleware serves the previous active
    # trading day's picks; if none exist, returns an empty response.
    pre = classify_pre_pipeline(today_iso)
    if not pre.is_trading_day:
        log.info("  NON-TRADING DAY (%s %s): skipping pipeline. reason=%s",
                 pre.weekday, today_iso, pre.reason)
        log_no_fire(pre)
        prev = load_previous_picks(today_iso)
        if prev is not None:
            log.info("  Serving previous picks from %s (unchanged on disk).",
                     prev.get("date"))
            log.info("=" * 76)
            return prev
        log.info("  No prior picks file found; returning empty response.")
        log.info("=" * 76)
        return {
            "date": today_iso,
            "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
            "source": "pipeline",
            "demo_mode": demo_mode,
            "picks": [],
            "message": (
                f"{pre.weekday} — non-trading day. "
                "No prior picks file available yet."
            ),
        }

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
        # Still emit a per-day summary so every trading day has one; nothing
        # was screened, so the funnel is empty and it just records regime OFF.
        try:
            from .run_summary import build_summary_md, write_summary
            md = build_summary_md(
                date=today_iso,
                generated_at=response.get("generated_at"),
                regime=regime.as_dict(),
                funnel={"selected": 0},
                picks=[],
            )
            write_summary(today_iso, md)
        except Exception:
            log.exception("run_summary (regime-off) write failed (non-fatal)")
        log.info("=" * 76)
        return response

    # ---- Phase 1: per-ticker pipeline (parallel) ----
    log.info("  [Phase 1/4] Running per-ticker chain over %d tickers (%d workers) ...",
             len(VOLUME_UNIVERSE), max_workers)
    results: list[PipelineResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(run_pipeline, sym, PER_TICKER_CHAIN, today_iso): sym
            for sym in VOLUME_UNIVERSE
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                log.exception("pipeline crashed for %s", futures[fut])

    # ---- Data-availability diagnostic ----
    # If >=90% of tickers failed at [I] Ingest, the composite score is
    # meaningless and the user is looking at a data-source misconfiguration,
    # not a market-regime problem. Surface that both loudly in the log AND
    # via the response.message so the UI shows the fix instead of a
    # misleading "nothing actionable today".
    ingest_failed = sum(
        1 for r in results
        if (r.stage_results.get("I") is not None
            and not r.stage_results["I"].passed)
    )
    data_misconfigured = bool(results) and ingest_failed / len(results) >= 0.90

    # ---- Non-trading-day guard (holiday_no_data) ----
    # 100% ingest failure = no fresh OHLCV for anyone = treat as a holiday
    # ("holidays (when no data found)"). Skip file write + portfolio update
    # and serve the previous active trading day's picks. The 90-99% path
    # below is preserved for real misconfigurations (writes a diagnostic
    # file so the operator sees the fix).
    if bool(results) and ingest_failed == len(results):
        post = classify_post_ingest(today_iso, len(results), ingest_failed)
        log.info("  HOLIDAY (no fresh OHLCV for any of %d tickers): skipping.",
                 len(results))
        log_no_fire(post, extra={
            "ingest_total": len(results),
            "ingest_failed": ingest_failed,
        })
        prev = load_previous_picks(today_iso)
        if prev is not None:
            log.info("  Serving previous picks from %s (unchanged on disk).",
                     prev.get("date"))
            log.info("=" * 76)
            return prev
        log.info("  No prior picks file found; returning empty response.")
        log.info("=" * 76)
        return {
            "date": today_iso,
            "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
            "source": "pipeline",
            "demo_mode": demo_mode,
            "picks": [],
            "message": (
                "No fresh OHLCV available today (likely a market holiday). "
                "No prior picks file available yet."
            ),
        }
    if data_misconfigured:
        log.error("=" * 76)
        log.error("  DATA SOURCE MISCONFIGURED  --  %d of %d tickers failed [I] Ingest.",
                  ingest_failed, len(results))
        log.error("  Root cause: DATA_SOURCE points at a cache that does not exist.")
        log.error("")
        log.error("  Quick fix (edit backend/.env):")
        log.error("    DEMO_MODE=1                # first-run: synthetic OHLCV, no network")
        log.error("    DATA_SOURCE=yahoo          # or: live Yahoo (needs internet)")
        log.error("    STOCKYA_OHLCV_DIR=...      # or: point at your own bhavcopy cache")
        log.error("")
        log.error("  Then restart start.bat. No strategy can produce picks without data.")
        log.error("=" * 76)

    # ---- Per-run data-health snapshot (read-only observability) ----
    # Turns the per-ticker [I] Ingest outcomes into a durable record of how many
    # of the universe actually produced a complete indicator set, and where the
    # rest went (network / empty / short-history / crashed). Answers "how many of
    # the N I scanned succeeded?" — a number previously computed only for the
    # >=90% alarm and then discarded. Never touches selection (data_health.py).
    from .data_health import summarize_data_health
    data_health = summarize_data_health(results, len(VOLUME_UNIVERSE))
    if data_health["silent_failures"]:
        log.warning(
            "  [Phase 1/4] Data health: %d/%d ingested OK (%.1f%%); "
            "%d silent failure(s): %s",
            data_health["ingested_ok"], data_health["attempted"],
            data_health["coverage_pct"] or 0.0,
            data_health["silent_failures"], data_health["failed_by_reason"],
        )
    else:
        log.info(
            "  [Phase 1/4] Data health: %d/%d ingested OK (%.1f%%).",
            data_health["ingested_ok"], data_health["attempted"],
            data_health["coverage_pct"] or 0.0,
        )

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
    # Guaranteed daily pre-breakout LEAD: if nothing cleared confirmation, surface
    # the single best calm, accumulation-confirmed base that is coiling just under
    # τ (ATR ceiling + accumulation + day-0 coherence still enforced — see
    # rank.rank_lead_fallback). Never leaves the day empty when a genuine
    # pre-breakout candidate exists; badged watch-grade downstream.
    lead_fallback_fired = False
    if not selected:
        lead = rank_lead_fallback(hard_survivors)
        if lead is not None:
            selected = [lead]
            lead_fallback_fired = True
    if selected:
        if lead_fallback_fired:
            log.info(
                "  [Phase 2/4] No confirmed picks — surfaced 1 pre-breakout LEAD "
                "(watch, S=%.3f < τ=%.2f): %s",
                selected[0].composite_score, COMPOSITE_TAU, selected[0].symbol,
            )
        else:
            log.info("  [Phase 2/4] Selected %d:", len(selected))
        for pick in selected:
            bonuses = (pick.confirmation_components or {}).get("bonuses_fired") or []
            log.info("    #%d  %-15s  confirmation=%.3f  bonuses=%s",
                     pick.rank, pick.symbol, pick.confirmation_score,
                     ", ".join(bonuses) or "-")
    else:
        log.info("  [Phase 2/4] No survivors — 0 picks today.")
        log.info("             (Run: python -m backend.trace_audit  for the near-miss list.)")

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
            # Advisory NSE delivery-% (accumulation vs churn). File-only, no
            # fetch; None-safe when no delivery files are on disk. Attached here
            # (orchestrator I/O layer) so the pure stages stay I/O-free.
            try:
                from .delivery import delivery_advisory
                payload["delivery"] = delivery_advisory(res.symbol)
            except Exception:
                payload["delivery"] = None
            # Institutional-flow INTEREST — bulk deals + delivery %, SCORING-NEUTRAL.
            # A display-only indicator (does NOT touch selection or any score):
            # flags picks worth a closer look and drives presentation_rank below.
            # Reuses the delivery advisory just attached (no second disk read).
            try:
                from .flow_interest import flow_interest, why_picked
                fi = flow_interest(res.symbol, payload.get("delivery"))
                # Always record WHY the pick was chosen (price/volume basis) so a
                # flow-suppressed pick at the bottom of the list still explains
                # itself. Cheap; reads fields already on the payload.
                fi["picked_reason"] = why_picked(payload)
                payload["flow_interest"] = fi
            except Exception:
                payload["flow_interest"] = None
            # OBV-vs-delivery divergence — SCORING-NEUTRAL advisory. When the
            # tape reads accumulation but delivery is weak & falling, record it
            # on the assessment envelope so "distribution-into-strength" is
            # visible. Annotation only: never gates selection, never scores.
            try:
                from .flow_interest import obv_delivery_divergence
                _div = obv_delivery_divergence(
                    payload.get("early_accumulation"), payload.get("delivery")
                )
                if _div:
                    assess = payload.get("accumulation_assessment")
                    if isinstance(assess, dict):
                        contras = assess.setdefault("contradictions", [])
                        if _div not in contras:
                            contras.append(_div)
            except Exception:
                log.exception("obv_delivery_divergence failed for %s", res.symbol)
            # Pre-breakout TAG eligibility — the coherence guard on the
            # pre-breakout label (backend/pre_breakout_tag.py). Computed HERE,
            # after every advisory contradiction is attached, so the self-veto
            # (Instruction 1) sees the complete contradiction list. Also enforces
            # coherent multi-timeframe flow with the healing carve-out
            # (Instruction 2) and right-edge stealth demand (Instruction 3).
            # Presentation/labeling only: never changes selection or any score —
            # a failing pick keeps its place, it just loses the pre-breakout badge.
            try:
                from .pre_breakout_tag import assess_pre_breakout_tag
                payload["pre_breakout_eligibility"] = assess_pre_breakout_tag(payload)
            except Exception:
                log.exception("assess_pre_breakout_tag failed for %s", res.symbol)
                payload["pre_breakout_eligibility"] = None
            # Reasoning checklist "steps" — built AFTER delivery is attached so
            # the delivery-load-status step reflects the same advisory. Purely
            # additive: activates the (previously dormant) frontend checklist
            # and never gates selection.
            try:
                from .reasoning_points import build_reasoning
                payload["reasoning"] = build_reasoning(payload)
            except Exception:
                log.exception("build_reasoning failed for %s", res.symbol)
                payload["reasoning"] = []
            pick_payloads.append(payload)
        except Exception:
            log.exception("build_pick_payload failed for %s", res.symbol)

    # Presentation ordering — attaches `presentation_rank` to the ALREADY-SELECTED
    # picks. As of 2026-07-31 the display follows the confirmation `rank` (which
    # already prefers genuine-early setups); bulk deals + delivery % only break an
    # exact tie, so a mature high-delivery name can't jump ahead of an earlier
    # pick. Scoring-neutral: never changes selection or the canonical `rank`.
    try:
        from .flow_interest import assign_presentation_ranks
        assign_presentation_ranks(pick_payloads)
    except Exception:
        log.exception("assign_presentation_ranks failed")

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
    if lead_fallback_fired and pick_payloads:
        _lead_sym = pick_payloads[0].get("symbol", selected[0].symbol)
        message = (
            f"No setup cleared confirmation (S ≥ {COMPOSITE_TAU:.2f}) today. "
            f"Showing the strongest pre-breakout lead — {_lead_sym} — as a "
            "WATCH-grade indication: accumulation-confirmed and inside the "
            "volatility ceiling, but still approaching the confirmation line. "
            "Wait for the trigger or size cautiously; this is not a confirmed buy."
        )
    # Closest-to-firing is now computed EVERY run (not just on zero-pick days)
    # so the near-misses are always visible alongside the picks.
    closest_to_firing: dict = _collect_closest_to_firing(
        results, tau=COMPOSITE_TAU, n_per_tab=5
    )
    if not pick_payloads:
        if data_misconfigured:
            # Don't lie to the user with "nothing actionable" when the real
            # issue is upstream. Tell them exactly what to fix.
            message = (
                f"Data source misconfigured — {ingest_failed}/{len(results)} "
                "tickers failed at [I] Ingest. Set DEMO_MODE=1 in backend/.env "
                "(fastest), or provide a valid STOCKYA_OHLCV_DIR, then restart."
            )
        else:
            message = (
                f"Nothing cleared composite S ≥ {COMPOSITE_TAU:.2f} today. "
                "Quality over quantity — capital preserved is capital available "
                "for the next real signal."
            )
    n_close = (
        len(closest_to_firing["accumulation"])
        + len(closest_to_firing["breakout"])
        + len(closest_to_firing["overall"])
    )
    if n_close:
        log.info(
            "  [Phase 4/4] Closest-to-firing: %d accum, %d breakout, %d overall",
            len(closest_to_firing["accumulation"]),
            len(closest_to_firing["breakout"]),
            len(closest_to_firing["overall"]),
        )
    # Attach volume-based holding horizon to every pick BEFORE reconcile,
    # so record_picks (and the UI) sees the same value.
    for _p in pick_payloads:
        try:
            _days, _basis = estimated_horizon_days(_p)
            _p["holding_horizon"] = {
                "days": _days,
                "basis": _basis,
                "source": "entry_estimate",
            }
        except Exception:
            log.exception("horizon estimation failed for %s", _p.get("symbol"))

    # Reconcile picks against currently-held portfolio positions. Picks
    # whose symbol has a taken (paper/live) row with an active exit_*
    # action get `suppressed_from_ui` and are hidden from the UI, but
    # they remain in the list so record_picks can persist them as a
    # fresh suggested row alongside the taken one.
    pick_payloads = reconcile_picks_against_portfolio(pick_payloads, today_iso)

    # Attach `change_since_prev_pick` diffs on every pick (including
    # UI-suppressed ones — the audit trail should reflect the fresh
    # signal even when we're not surfacing it in the buy list).
    attach_change_diffs(pick_payloads, today_iso)

    # Attach multi-day `pick_history` trail — one snapshot per prior day
    # this symbol was picked, newest first, with day-over-day direction
    # tag (positive/negative/neutral). Complements the single-day diff.
    attach_pick_history(pick_payloads, today_iso)

    # Split: only visible picks go into picks_<date>.json; the portfolio
    # ledger records the full set so suppressed picks still land as fresh
    # suggested rows alongside the taken position with the exit signal.
    visible_picks, suppressed_picks = split_visible_from_suppressed(pick_payloads)

    # Entry-readiness router — the main BUY list keeps only setups enterable
    # TODAY (entry_timing early/mid); late / extended / distribution / unclear
    # picks move to `not_actionable` (a for-awareness section, still visible, the
    # human decides). Reversible via STOCKYA_ENTERABLE_ONLY=0. See
    # backend/entry_readiness.py.
    from .entry_readiness import split_enterable
    visible_picks, not_actionable = split_enterable(visible_picks)
    if not_actionable:
        _moved_ids = {id(p) for p in not_actionable}
        # Keep non-enterable picks out of the portfolio recommendation journal
        # too — they are awareness items, not buys. Every ticker's FINAL trace
        # row was already written (append_final_trace above), so outcome
        # evaluation is unaffected.
        pick_payloads = [p for p in pick_payloads if id(p) not in _moved_ids]
        log.info(
            "  [Phase 4/4] Entry-readiness: %d pick(s) not enterable today -> "
            "awareness section: %s",
            len(not_actionable),
            ", ".join(
                f"{p.get('symbol', '?')}({(p.get('not_actionable') or {}).get('category')})"
                for p in not_actionable
            ),
        )

    # Institutional-accumulation WATCHLIST (Use 1 — scoring-neutral guidance on
    # which stocks to analyze). Built from the deals/delivery corpora, never
    # enters the scan. Empty when no flow data is on disk.
    try:
        from .flow_interest import build_watchlist
        watchlist = build_watchlist()
        if watchlist:
            log.info("  [Phase 4/4] Accumulation watchlist: %d name(s)", len(watchlist))
    except Exception:
        log.exception("build_watchlist failed")
        watchlist = []

    response = render_picks_response(
        visible_picks, today_iso,
        demo_mode=demo_mode,
        regime=regime.as_dict(),
        message=message,
        closest_to_firing=closest_to_firing,
        watchlist=watchlist,
    )

    # Read-only per-run data-health block (how many of the universe ingested).
    response["data_health"] = data_health
    # Picks moved out of the buy list because they are not enterable today
    # (late / extended / distribution / unclear timing). Shown in a separate
    # for-awareness section with a reason. Additive/optional; absent when empty.
    if not_actionable:
        response["not_actionable"] = [
            {
                "symbol": p.get("symbol"),
                "company": p.get("company"),
                "rank": p.get("rank"),
                "reason": p.get("not_actionable"),
            }
            for p in not_actionable
        ]

    # Fresh, delivery-LED analysis over TODAY'S ELIGIBLE FIELD (every hard-gate
    # survivor) — SCORING-NEUTRAL and purely additive. Its own ranking (a
    # strong-delivery near-miss can outrank a volume pick); it never touches
    # selection / composite / rank / sizing / exits or any other section. Built
    # here, after selection, from the survivors already in memory + a single
    # batch delivery read. Degrades to composite order when no delivery on disk.
    try:
        from .delivery import all_advisories
        from .delivery_weighted import build_delivery_analysis
        _advs = all_advisories()
        _cands = [
            {
                "symbol": r.symbol,
                "company": (r.snapshot or {}).get("company") or r.symbol,
                "composite_score": r.composite_score,
                "delivery": _advs.get(r.symbol),
            }
            for r in hard_survivors
        ]
        _picked = {p.get("symbol") for p in visible_picks}
        response["delivery_analysis"] = build_delivery_analysis(_cands, _picked)
    except Exception:
        log.exception("build_delivery_analysis failed")
        response["delivery_analysis"] = []

    path = write_picks_file(response)
    log.info(
        "  [Phase 4/4] Wrote %s  (visible=%d suppressed=%d)",
        path.name, len(visible_picks), len(suppressed_picks),
    )
    log.info("=" * 76)

    # ---- Phase 5: portfolio ledger ----
    # Feed record_picks the FULL reconciled list, not just the visible
    # ones. Suppressed picks are recorded so the fresh signal is tracked
    # as a duplicate suggested row (different entry_date) alongside the
    # user's taken position, without contradicting the exit signal in
    # today's UI.
    run_errors: list[str] = []
    try:
        from .portfolio import record_picks
        record_payload = dict(response)
        record_payload["picks"] = pick_payloads
        added = record_picks(record_payload)
        if added:
            log.info("portfolio.csv: appended %d new picks", added)
    except Exception as e:
        log.exception("portfolio recording failed (non-fatal)")
        run_errors.append(f"record_picks: {type(e).__name__}: {e}")

    # ---- Phase 6: daily diagnostic snapshot ----
    # Overwrites data/daily_diagnostic.md. Self-contained: uploading this
    # single file gives full context (code fingerprints, pipeline results,
    # portfolio state, picks state, reconcile events).
    try:
        from .daily_diagnostic import write_daily_diagnostic
        orchestrator_summary = {
            "universe_count": len(results),
            "ingested_ok": data_health["ingested_ok"],
            "silent_failures": data_health["silent_failures"],
            "survivors_passed_gates": sum(1 for r in results if r.passed_gates),
            "visible_picks": len(visible_picks),
            "suppressed_picks": len(suppressed_picks),
            "regime_passed": regime.passed,
            "regime_summary": regime.summary,
            "top_pick_symbols": [p.get("symbol") for p in visible_picks[:5]],
        }
        write_daily_diagnostic(today_iso, orchestrator_summary, run_errors)
    except Exception:
        log.exception("daily_diagnostic write failed (non-fatal)")

    # ---- Phase 7: per-day selection summary (very-high-level, ~1-2 KB) ----
    # A human-readable story of how the universe narrowed to the picks. Purely
    # additive and READ-ONLY: does not touch selection, scoring, or the picks
    # JSON. Non-fatal — a summary failure must never break a run.
    try:
        from .run_summary import summarize_live
        summary_path = summarize_live(
            today_iso=today_iso,
            generated_at=response.get("generated_at"),
            regime=regime.as_dict(),
            results=results,
            hard_survivors=hard_survivors,
            survivors=survivors,
            visible_picks=visible_picks,
            tau=COMPOSITE_TAU,
            closest=closest_to_firing,
            data_health=data_health,
        )
        log.info("  [Phase 7] Wrote selection summary %s", summary_path.name)
    except Exception:
        log.exception("run_summary write failed (non-fatal)")

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
            for sym in VOLUME_UNIVERSE
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
