"""[H] Hypothesis + Exit Plan — template-based, zero LLM.

Every selected ticker gets:
  - a "why we're buying" paragraph built from the feature values, and
  - a 4-exit plan (target / volume-distribution / hard-stop / time-stop)

All copy is filled deterministically from features. Re-run on the same
features always produces the same text. Replace the template strings to
change voice; replace the feature-to-text rules to change emphasis.
"""

from __future__ import annotations

from ..pipeline import PipelineContext, PipelineResult
from ..signals import suggest_target_window

stage_id = "H"


def build_pick_payload(result: PipelineResult, ctx_snapshot: dict) -> dict:
    """Build the JSON the UI renders. Called after [S] has marked `selected`.

    Returns the same shape `middleware/schemas.py:Pick` expects.
    """
    a = result.stage_results  # for evidence
    current = ctx_snapshot.get("current") or 0.0

    # Pull the AccumulationSignals that was stashed into ingest features —
    # but we don't actually need it here; stage features are enough.
    lt = result.stage_results.get("LT")
    mt = result.stage_results.get("MT")
    tt = result.stage_results.get("TT")
    dd = result.stage_results.get("DD")
    br = result.stage_results.get("BR")

    # ---- Entry / target / stop ----
    # Entry zone: 2% pullback from current is the default "best buy at".
    best_buy_at = round(current * 0.98, 2)
    # Target: +20% by default (Nifty 100 large-cap realistic 3-6m).
    sell_target = round(best_buy_at * 1.20, 2)
    # Stop: -10% from entry (PRINCIPLES §4 B2).
    stop_loss = round(best_buy_at * 0.90, 2)

    upside_pct = round((sell_target / best_buy_at - 1) * 100, 2)
    downside_pct = round((stop_loss / best_buy_at - 1) * 100, 2)

    # ---- Headline (one-line thesis) ----
    weinstein = (lt.features.get("weinstein_stage") if lt else "") or "undefined"
    obv90 = lt.features.get("obv_90d_pct") if lt else None
    cmf60 = lt.features.get("cmf_60d") if lt else None

    if obv90 is not None and obv90 >= 5:
        lead = f"OBV {obv90:+.0f}% over 90 days"
    elif cmf60 is not None and cmf60 >= 0.05:
        lead = f"CMF {cmf60:+.2f} over 60 days"
    elif tt and tt.features.get("minervini_template"):
        lead = "Trend-template aligned (50d > 150d > 200d, rising)"
    else:
        lead = f"composite {result.composite_score:.0f}/100"

    stage_tag = {
        "stage_1_to_2": "Stage 1→2",
        "stage_2_advance": "Stage 2",
        "stage_1_base": "Stage 1 base",
    }.get(weinstein, "")
    headline = f"{lead}{' — ' + stage_tag if stage_tag else ''}."
    if len(headline) > 120:
        headline = headline[:117] + "..."

    # ---- Rationale (bull case) ----
    rationale_bits: list[str] = []
    if lt and lt.evidence:
        rationale_bits.append("Long-term volume: " + ", ".join(lt.evidence) + ".")
    if mt and mt.evidence:
        rationale_bits.append("This-month confirmation: " + ", ".join(mt.evidence) + ".")
    if dd and dd.evidence:
        rationale_bits.append("Direct deals (NSE): " + "; ".join(dd.evidence) + ".")
    if br and br.evidence and br.score > 0:
        rationale_bits.append("Breakout triggers: " + ", ".join(br.evidence) + ".")
    rationale = " ".join(rationale_bits) or "Composite signal cleared all gates."

    # ---- Risks (bear case) — fixed template per PRINCIPLES §4 ----
    risks = (
        "PRIMARY EXIT TRIGGER (volume): exit if OBV-30d rolls over, CMF-21d "
        "turns negative, or down-day volume starts dominating up-day volume — "
        "that is the institutional footprint leaving. "
        "BACKSTOPS: -10% hard stop from best_buy_at, or two daily closes below "
        "the 200-day moving average, or 6-month time stop without target. "
        "General market risk: 10–15% drawdowns on a single name are normal "
        "even with the long-term thesis intact."
    )
    risk_headline = (
        "Exit if volume inverts (OBV-30d down, CMF-21d<0, or down-day vol "
        "dominates)."
    )

    # ---- Target window — derive from the long-term picture ----
    # We need an AccumulationSignals object for suggest_target_window. The
    # orchestrator stashes it on ctx_snapshot as `_signals`.
    a_obj = ctx_snapshot.get("_signals")
    if a_obj is not None:
        tw = suggest_target_window(a_obj)
        target_window = {
            "center_months": tw.center_months,
            "tolerance_months": tw.tolerance_months,
            "label": tw.label,
            "rationale": tw.rationale,
        }
    else:
        target_window = {
            "center_months": 4.5, "tolerance_months": 1.5,
            "label": "4-6 months",
            "rationale": "Default investing window.",
        }

    # ---- Reasoning checklist — one auditable line per scoring stage ----
    reasoning: list[dict] = []
    for sid, label, why in [
        ("LT", "Long-term volume (50%)",
         "Institutional accumulation over months — Weinstein stage, OBV-90/180d, CMF-60d, U/D-90d, base length, QoQ vol."),
        ("TT", "Trend Template (15%)",
         "Minervini: 50d > 150d > 200d, all rising, price above 50d MA."),
        ("MT", "Mid-term volume (20%)",
         "Today's confirmation — Wyckoff phase, OBV-30d, CMF-21d, U/D-30d, MFI-14, price vs VWAP."),
        ("DD", "Direct deals (10%)",
         "NSE block + bulk deals over 30d — literal institutional trade records."),
        ("BR", "Breakout triggers (5%)",
         "Pocket Pivot, Volume Dry-Up, CAN SLIM — timing signals."),
    ]:
        sr = result.stage_results.get(sid)
        if not sr:
            continue
        state = (
            "bullish" if sr.score >= 0.6
            else "bearish" if sr.score < 0.3
            else "neutral"
        )
        reasoning.append({
            "label": label,
            "value": f"score {sr.score:.2f} → " + (", ".join(sr.evidence[:3]) if sr.evidence else "n/a"),
            "state": state,
            "why": why,
            "verify": f"See stage trace for {sid} in data/traces/run_<date>_{result.symbol}.jsonl",
        })

    return {
        "symbol": result.symbol,
        "company": ctx_snapshot.get("company") or result.symbol,
        "current": round(current, 2),
        "best_buy_at": best_buy_at,
        "sell_target": sell_target,
        "stop_loss": stop_loss,
        "headline": headline,
        "rationale": rationale,
        "risk_headline": risk_headline,
        "risks": risks,
        "confidence": "high" if result.composite_score >= 80 else "medium" if result.composite_score >= 60 else "low",
        "upside_pct": upside_pct,
        "downside_pct": downside_pct,
        "entry_timing": (a_obj.entry_timing if a_obj else "unknown"),
        "wyckoff_phase": (a_obj.wyckoff_phase if a_obj else "indeterminate"),
        "weinstein_stage": weinstein if weinstein else "undefined",
        "target_window": target_window,
        "reasoning": reasoning,
        "composite_score": result.composite_score,
        "rank": result.rank,
    }
