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

from ..pipeline import PipelineResult
from ..position_sizer import size_position


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
        parts.append(
            f"broke {break_pct:+.1f}% above 20d high on {vol_ratio:.1f}x vol"
        )
    return "; ".join(parts) or "All four gates cleared"


def _gate_evidence(result: PipelineResult) -> dict:
    """Pull per-gate evidence lists for the UI."""
    out: dict = {}
    for gid in ("CS", "VD", "BR"):
        sr = result.stage_results.get(gid)
        out[gid] = list(sr.evidence) if sr and sr.evidence else []
    return out


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

    distribution_flip_note = (
        "Exit immediately if any of the volume signals invert: OBV-30d turns "
        "down, 5d/50d volume ratio re-expands above 1.0x, or close < 150d MA "
        "on two consecutive sessions."
    )

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

        # ---- Legacy aliases (so existing frontend keeps rendering) ----
        "best_buy_at": round(entry, 2),
        "sell_target": round(plan.t2, 2),
        "stop_loss": round(plan.stop, 2),
        "upside_pct": round(upside_pct, 2),
        "downside_pct": round(downside_pct, 2),
        "shares_to_buy": plan.shares_total,
    }
