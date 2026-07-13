"""[H] Hypothesis + Exit Plan — new gates-based spine.

Builds the user-facing pick payload for one selected ticker. Consumes:
  - the PipelineResult (with CS/VD/BR stage results, confirmation score, ohlcv)
  - the snapshot dict (company / sector / current price)
  - the account value (for position sizing)

Produces a dict that the render stage writes to picks_<date>.json.

Per PRINCIPLES:
  - Entry = breakout close (today's bar)
  - Stop  = -8 %
  - T1    = +8 %  (sell 50 %, raise stop to break-even on the rest)
  - T2    = +16 % (sell remaining 50 %)
  - Day-45: if T1 not hit, tighten stop to entry - 4 %
  - Day-90: if T1 not hit, exit at market
  - Day-180: unconditional final exit

Lives alongside the legacy hypothesis.py until Task #10 cutover swaps them.
"""

from __future__ import annotations

from typing import Optional

from ..indicators import volume_spike_event
from ..pipeline import PipelineResult, classify_trigger
from ..position_sizer import size_position
from ..signal_trajectory import (
    FAILED_BR_VOLUME_MULT,
    FAILED_BR_WINDOW_TRADING_DAYS,
    HEALING_GRACE_TRADING_DAYS,
)


# --------------------------------------------------------------------------- #
# Tunable constants — milestones in trading days
# --------------------------------------------------------------------------- #

DAY_45_TIGHTEN_STOP_PCT: float = 0.04   # tunable — entry - 4 % on day 45 if no T1
DAY_45_MILESTONE: int = 45               # tunable
DAY_90_HARD_EXIT_MILESTONE: int = 90     # tunable
DAY_180_FINAL_EXIT_MILESTONE: int = 180  # tunable


def _build_headline(result: PipelineResult) -> str:
    """One-liner thesis derived from the strongest gate evidence."""
    stages = result.stage_results
    cs = stages.get("CS")
    vd = stages.get("VD")
    br = stages.get("BR")

    days_band = cs.features.get("days_in_band") if cs else None
    atr_pct = cs.features.get("atr_pct") if cs else None
    vol_ratio = br.features.get("vol_ratio_today_50d") if br else None
    break_pct = br.features.get("break_pct") if br else None
    div_form = (vd.features.get("divergence") or {}).get("form") if vd else None

    parts: list[str] = []
    if days_band is not None and atr_pct is not None:
        parts.append(f"{days_band}-day base, ATR {atr_pct:.1f}%")
    if div_form and div_form != "none":
        parts.append(f"{div_form.replace('-', ' ')} OBV divergence")
    if vol_ratio is not None and break_pct is not None:
        # Bug fix: a *negative* break_pct means today closed BELOW the 20d high —
        # the trigger bar did not confirm. Templating "broke -6.4% above ..."
        # produced a self-contradictory sentence. Branch on sign.
        if break_pct >= 0:
            parts.append(
                f"broke {break_pct:+.1f}% above 20d high on {vol_ratio:.1f}x vol"
            )
        else:
            parts.append(
                f"closed {abs(break_pct):.1f}% below 20d high on {vol_ratio:.1f}x vol "
                f"— no confirmed breakout yet"
            )
    return "; ".join(parts) or "All four gates cleared"


def _gate_evidence(result: PipelineResult) -> dict:
    """Pull per-gate evidence lists for the UI."""
    out: dict = {}
    for gid in ("CS", "VD", "BR"):
        sr = result.stage_results.get(gid)
        out[gid] = list(sr.evidence) if sr and sr.evidence else []
    return out


def _gate_confirmation_status(result: PipelineResult) -> dict:
    """Report whether every listed soft gate literally passed on its own terms.

    Bug fix: the UI previously hardcoded "Why all four gates passed" whenever a
    ticker was surfaced. Under the v3 soft-gate composite spine a pick can
    clear the composite `S ≥ τ` while an individual soft leg (e.g. BR) failed
    its own bool check. Reporting "all four passed" in that case is a lie.

    Returns:
        {
          "status":  "hard_confirmed" | "composite_qualified",
          "passed":  ["CS", "VD"],
          "failed":  ["BR"],
          "counts":  {"passed": 2, "total": 3},
        }

    UI should branch its heading text on `status`:
      - hard_confirmed      →  "Why all N gates passed"
      - composite_qualified →  "Composite-qualified — {p}/{t} legs confirmed"
    """
    gate_ids = ("CS", "VD", "BR")
    passed: list[str] = []
    failed: list[str] = []
    for gid in gate_ids:
        sr = result.stage_results.get(gid)
        if sr is None:
            continue
        (passed if sr.passed else failed).append(gid)
    total = len(passed) + len(failed)
    return {
        "status": "hard_confirmed" if not failed and total > 0 else "composite_qualified",
        "passed": passed,
        "failed": failed,
        "counts": {"passed": len(passed), "total": total},
    }


def build_pick_payload(
    result: PipelineResult,
    snapshot: dict,
    *,
    account_value: float = 100000.0,
    today_iso: Optional[str] = None,
) -> dict:
    """Assemble the pick payload for one selected ticker.

    Returns a dict ready to be written by the render stage.
    Includes both new-shape fields (price_plan, exit_schedule, gates_evidence)
    and legacy aliases (best_buy_at, sell_target, stop_loss) so existing UI
    code can render without modification during the spine cutover.
    """
    br = result.stage_results.get("BR")
    if br is None or not br.passed:
        # Defensive: ranker should never select a non-breakout. Use snapshot's
        # current price as fallback so we don't crash.
        entry = float(snapshot.get("current") or 0.0)
    else:
        entry = float(br.features.get("close") or snapshot.get("current") or 0.0)

    plan = size_position(account_value=account_value, entry=entry)

    headline = _build_headline(result)
    vol_event = volume_spike_event(result.ohlcv).as_dict() if result.ohlcv is not None else None

    upside_pct = (plan.t2 / entry - 1) * 100 if entry > 0 else 0.0
    downside_pct = (plan.stop / entry - 1) * 100 if entry > 0 else 0.0

    exit_schedule = {
        "day_45": {
            "milestone_days": DAY_45_MILESTONE,
            "action": "tighten_stop",
            "trigger": "T1 not hit",
            "new_stop": round(entry * (1 - DAY_45_TIGHTEN_STOP_PCT), 2),
            "note": (
                f"If T1 not hit by day {DAY_45_MILESTONE}, raise stop to "
                f"entry - {int(DAY_45_TIGHTEN_STOP_PCT*100)}%."
            ),
        },
        "day_90": {
            "milestone_days": DAY_90_HARD_EXIT_MILESTONE,
            "action": "exit_market",
            "trigger": "T1 not hit",
            "note": (
                f"If T1 not hit by day {DAY_90_HARD_EXIT_MILESTONE}, exit at "
                "market. Capital frozen in a non-moving trade is opportunity cost."
            ),
        },
        "day_180": {
            "milestone_days": DAY_180_FINAL_EXIT_MILESTONE,
            "action": "exit_market",
            "trigger": "any leg still open",
            "note": (
                f"Unconditional final exit on day {DAY_180_FINAL_EXIT_MILESTONE}."
            ),
        },
    }

    # Trigger-aware exit language. Divergent (pre-breakout) entries can't use
    # the standard "OBV-30d turns down" rule because their 30d OBV was already
    # weak at entry; instead the runtime signal_trajectory watches the 10d/30d
    # inflection for a "healing -> hemorrhaging" flip inside a bounded grace.
    # SOS-breakout entries additionally arm the failed-breakout micro-stop for
    # the first FAILED_BR_WINDOW_TRADING_DAYS sessions.
    regime = classify_trigger(result.stage_results)
    vd = result.stage_results.get("VD")
    entry_inflection = (
        (vd.features or {}).get("obv_flow_inflection") if vd else None
    )
    br = result.stage_results.get("BR")
    resistance_20d = (br.features or {}).get("resistance_20d") if br else None

    if entry_inflection == "healing":
        distribution_flip_note = (
            "Divergent entry (10d OBV up, 30d weak — 'healing'). Standard "
            "OBV-30d exit rule is suspended for the first "
            f"{HEALING_GRACE_TRADING_DAYS} sessions. Exit at next open only if "
            "the 10d OBV rolls negative (inflection -> hemorrhaging) inside "
            "that grace, or the 150d MA slope turns down."
        )
    else:
        distribution_flip_note = (
            "Exit immediately if any of the volume signals invert: OBV-90d "
            "rolls into a downslope, up/down vol 90d falls below 1.0x, or "
            "close < 150d MA on two consecutive sessions."
        )

    if regime == "sos_breakout" and resistance_20d:
        exit_schedule["day_5_failed_breakout"] = {
            "milestone_days": FAILED_BR_WINDOW_TRADING_DAYS,
            "action": "exit_market",
            "trigger": (
                f"close < 20d high ({resistance_20d:.2f}) "
                f"AND volume >= {FAILED_BR_VOLUME_MULT:.2f}x ADV50"
            ),
            "resistance_20d": resistance_20d,
            "note": (
                f"Micro-stop (B1.5) armed for the first "
                f"{FAILED_BR_WINDOW_TRADING_DAYS} sessions. A breakout that "
                "falls back below its own resistance on heavy volume is "
                "institutional distribution — exit before the -8% B2 stop."
            ),
        }

    return {
        # ---- New shape (primary) ----
        "symbol": result.symbol,
        "rank": result.rank,
        "trace_id": result.trace_id,
        "company": snapshot.get("company"),
        "sector": snapshot.get("sector"),
        "industry": snapshot.get("industry"),
        "current_price": entry,
        "headline": headline,
        "confirmation": {
            "score": result.confirmation_score,
            **(result.confirmation_components or {}),
        },
        "price_plan": plan.as_dict(),
        "exit_schedule": exit_schedule,
        "distribution_flip_exit": distribution_flip_note,
        "gates_evidence": _gate_evidence(result),
        "gate_confirmation_status": _gate_confirmation_status(result),
        "volume_event": vol_event,

        # ---- Legacy aliases (so existing frontend keeps rendering) ----
        "best_buy_at": round(entry, 2),
        "sell_target": round(plan.t2, 2),
        "stop_loss": round(plan.stop, 2),
        "upside_pct": round(upside_pct, 2),
        "downside_pct": round(downside_pct, 2),
        "shares_to_buy": plan.shares_total,
    }
