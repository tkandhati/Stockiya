"""LLM-tuning log — one self-describing record per decision + its matured outcome.

WHY THIS EXISTS
---------------
PRINCIPLES §9 says thresholds must be evolved on OUTCOMES, never hand-tuned. But
the champion metric in config/stage_weights.json is still null — the tuner has
never run, because a clean, outcome-paired training record was never emitted in a
form something (an RL bandit, or an LLM) could consume. This module produces that
record: every field the decision used (as-of, no lookahead) paired with the
graded forward outcome (the label), plus a one-line natural-language `summary` so
an LLM can read a record without a schema in hand.

DESIGN CONTRACT
---------------
* Pure + deterministic. `build_record(...)` reads only what it is handed; it
  recomputes nothing and never raises on a missing field (writes null instead).
* No lookahead. Everything under `as_of` is decision-time state (data <= pick
  date). Everything under `outcome` is the future label. The two never mix.
* Faithful. Features are pulled from the STAGE RESULTS the pipeline actually
  decided on (not a re-derivation), so the log = the real decision inputs.
* Reusable live + offline. `scripts/gen_tuning_log.py` calls this over the
  backtest files today; the live orchestrator can call the SAME builder when an
  outcome matures (see MATURED-OUTCOME HOOK at the bottom).

Format: JSON Lines (one record per line). Schema documented in
data/tuning/README_tuning_log.md. Bump TUNING_LOG_SCHEMA_VERSION on any
breaking field change so an LLM/consumer can branch on it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

TUNING_LOG_SCHEMA = "stockiya-tuning-log"
TUNING_LOG_SCHEMA_VERSION = 1

# Horizons (trading days) at which we also record raw buy&hold return, for label
# maturity. The app's own exit-ladder return is the primary label; these are the
# unmanaged references an LLM can use to separate "detection was right, exit was
# wrong" from "detection was wrong".
BUY_HOLD_HORIZONS: tuple[int, ...] = (21, 63, 90)


def _num(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    import math
    return f if math.isfinite(f) else None


def _sr(stage_results: dict, sid: str):
    return (stage_results or {}).get(sid)


def _feat(stage_results: dict, sid: str) -> dict:
    sr = _sr(stage_results, sid)
    return (getattr(sr, "features", None) or {}) if sr is not None else {}


def _passed(stage_results: dict, sid: str) -> Optional[bool]:
    sr = _sr(stage_results, sid)
    return bool(sr.passed) if sr is not None else None


def _score(stage_results: dict, sid: str) -> Optional[float]:
    sr = _sr(stage_results, sid)
    return _num(getattr(sr, "score", None)) if sr is not None else None


def extract_features(stage_results: dict) -> dict:
    """Flatten the decision-time signals into named, grouped feature blocks.

    Every value is read from a stage's own `features` dict — the exact numbers
    that stage scored on. Grouped by the question each block answers so an LLM
    (or a human) can reason about which axis failed.
    """
    br = _feat(stage_results, "BR")
    vd = _feat(stage_results, "VD")
    ltv = _feat(stage_results, "LTV")
    dv = _feat(stage_results, "DV")
    ac = _feat(stage_results, "AC")

    sp = (vd.get("signed_pressure_ewm") or {})
    div = (vd.get("divergence") or {})

    return {
        # --- Did today's bar fire the breakout trigger? (the BUY/WATCH pivot) ---
        "trigger": {
            "br_passed": _passed(stage_results, "BR"),
            "breakout_vol_x_adv50": _num(br.get("vol_ratio_today_50d")),
            "breakout_break_pct": _num(br.get("break_pct")),
            "upper_third_ratio": _num(br.get("upper_third_ratio")),
            "vol_robust_z_50d": _num(br.get("vol_robust_z_50d")),
        },
        # --- Multi-day accumulation footprint (spike-proof reads) ---
        "accumulation": {
            "ac_passed": _passed(stage_results, "AC"),
            "ac_score": _score(stage_results, "AC"),
            "dry_up_streak_days_p25": br.get("dry_up_streak_days_p25"),
            "anomaly_cluster_count_15d": br.get("anomaly_cluster_count_15d"),
            "vol_ratio_5_50": _num(vd.get("vol_ratio_5_50")),
        },
        # --- Signed flow / "does the accumulated drift agree with entry?" ---
        "flow": {
            "signed_pressure_ewm_hl3": _num(sp.get("hl3")),
            "signed_pressure_ewm_hl10": _num(sp.get("hl10")),
            "signed_pressure_ewm_hl30": _num(sp.get("hl30")),
            "obv_flow_inflection": vd.get("obv_flow_inflection"),
            "obv_slope_short_pct": _num(vd.get("obv_slope_short_pct")),
            "obv_slope_long_pct": _num(vd.get("obv_slope_long_pct")),
            "obv_bullish_divergence": div.get("is_bullish"),
            "obv_divergence_form": div.get("form"),
        },
        # --- Long-term flow (the [LTV] veto axis) ---
        "long_flow": {
            "obv_90d_norm_slope_pct": _num(ltv.get("obv_90d_norm_slope_pct")),
            "obv_90d_slope_pct_ratio": _num(ltv.get("obv_90d_slope_pct_ratio")),
            "ltv_prebreakout_exempt": ltv.get("prebreakout_exempt"),
        },
        # --- Distribution footprints (the trap the owner fears) ---
        "distribution": {
            "dv_would_veto": dv.get("would_veto"),
            "dv_reasons": dv.get("veto_reasons"),
            "dist_day_count_15": dv.get("dist_day_count_15"),
        },
    }


def money_flow_block(signals: Any) -> dict:
    """Close-weighted money-flow + structure from volume_signals.compute(...).

    `signals` is an AccumulationSignals (or None). Read defensively.
    """
    g = lambda a: getattr(signals, a, None) if signals is not None else None
    return {
        "verdict": g("verdict"),
        "entry_timing": g("entry_timing"),
        "weinstein_stage": g("weinstein_stage"),
        "cmf_21d": _num(g("cmf_21d")),
        "ad_line_slope_pct_30d": _num(g("ad_line_slope_pct")),
        "obv_slope_pct": _num(g("obv_slope_pct")),
    }


def gate_map(stage_results: dict) -> dict:
    """Per-stage {passed, score} — the raw gate ledger for this decision."""
    out = {}
    for sid, sr in (stage_results or {}).items():
        out[sid] = {"passed": bool(getattr(sr, "passed", False)),
                    "score": _num(getattr(sr, "score", None))}
    return out


def _summary(rec: dict) -> str:
    """Compact NL handle so an LLM can read a record without the schema."""
    d = rec["as_of"]["decision"]
    t = rec["as_of"]["features"]["trigger"]
    mf = rec["as_of"]["money_flow"]
    o = rec["outcome"]
    trig = "BR-fired" if t.get("br_passed") else f"no-BR({t.get('breakout_vol_x_adv50')}x)"
    br = d.get("buy_readiness") or {}
    verdict = f"{br.get('state','?')}/{d.get('trigger_regime','?')}"
    if o.get("graded"):
        res = "WIN" if o.get("win") else "LOSS"
        tail = f"{o.get('exit_ladder_return_pct'):+.1f}% {o.get('exit_reason')} d{o.get('exit_day')} ({res})"
    else:
        tail = "outcome pending"
    return (f"{rec['symbol']} {rec['pick_date']} | {verdict} {trig}, "
            f"{mf.get('weinstein_stage')}/{mf.get('entry_timing')}, "
            f"sp_hl30 {t and rec['as_of']['features']['flow'].get('signed_pressure_ewm_hl30')} | {tail}")


def build_record(
    *,
    symbol: str,
    pick_date_iso: str,
    stage_results: dict,
    composite_score: float,
    composite_tau: float,
    trigger_regime: str,
    rank: Optional[int],
    candidate_class: str,          # "selected" | "qualified_not_selected" | "sub_threshold"
    buy_readiness: Optional[dict],  # from backend.buy_readiness.assess_buy_readiness
    signals: Any = None,            # AccumulationSignals from volume_signals.compute
    outcome: Optional[dict] = None,
) -> dict:
    """Assemble one tuning-log record. Pure; never raises on missing fields."""
    rec = {
        "schema": TUNING_LOG_SCHEMA,
        "schema_version": TUNING_LOG_SCHEMA_VERSION,
        "record_type": "pick_outcome",
        "symbol": symbol,
        "pick_date": pick_date_iso,
        "as_of": {
            "decision": {
                "candidate_class": candidate_class,
                "selected": candidate_class == "selected",
                "rank": rank,
                "composite_score": _num(composite_score),
                "composite_tau": _num(composite_tau),
                "cleared_tau": (_num(composite_score) is not None
                                and _num(composite_tau) is not None
                                and _num(composite_score) >= _num(composite_tau)),
                "trigger_regime": trigger_regime,
                "buy_readiness": (
                    {"state": buy_readiness.get("state"),
                     "category": buy_readiness.get("category")}
                    if isinstance(buy_readiness, dict) else None
                ),
                # The app would actually tell you to buy this today = it was
                # selected AND the readiness verdict is buy. (A non-selected
                # BR-fired day can still read buy_readiness=buy, but it is not
                # surfaced — so it is not actionable.)
                "actionable_buy": (
                    candidate_class == "selected"
                    and isinstance(buy_readiness, dict)
                    and buy_readiness.get("state") == "buy"
                ),
            },
            "gates": gate_map(stage_results),
            "features": extract_features(stage_results),
            "money_flow": money_flow_block(signals),
        },
        "outcome": outcome or {"graded": False, "reason": "not matured"},
    }
    rec["summary"] = _summary(rec)
    return rec


def build_outcome(
    *,
    exit_ladder_return_pct: Optional[float],
    exit_reason: Optional[str],
    exit_day: Optional[int],
    hit_stop: Optional[bool],
    hit_t1_day: Optional[int],
    forward_window_bars: int,
    buy_hold_by_horizon: Optional[dict] = None,
    entry_fill_price: Optional[float] = None,
) -> dict:
    """Normalize a forward_walk result + horizon buy&holds into the outcome block."""
    r = _num(exit_ladder_return_pct)
    # "mature" = the forward window is at least the longest horizon OR the ladder
    # already produced a terminal exit (stop/T2/day-90). Anything else is partial.
    mature = (forward_window_bars >= max(BUY_HOLD_HORIZONS)) or (
        exit_reason in ("stop", "t2", "hard_exit_90", "t1_then_be_stop")
    )
    return {
        "graded": r is not None,
        "outcome_mature": bool(mature),
        "fill": "next_open",
        "entry_fill_price": _num(entry_fill_price),
        "exit_ladder_return_pct": r,
        "exit_reason": exit_reason,
        "exit_day": exit_day,
        "hit_stop": bool(hit_stop) if hit_stop is not None else None,
        "hit_t1_day": hit_t1_day,
        "win": (r is not None and r > 0),
        "forward_window_bars": forward_window_bars,
        "buy_hold_return_pct": buy_hold_by_horizon or {},
    }


def write_jsonl(path: str | Path, records: list[dict]) -> int:
    """Overwrite `path` with one JSON object per line. Returns count written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    return len(records)


def append_jsonl(path: str | Path, record: dict) -> None:
    """Append a single record — the live MATURED-OUTCOME HOOK path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# --------------------------------------------------------------------------- #
# MATURED-OUTCOME HOOK (for the live app, wired later)
# --------------------------------------------------------------------------- #
# When a pick's T+90 outcome matures, the outcome stage (backend/stages/outcome.py)
# can call:
#
#     from backend.tuning_log import build_record, build_outcome, append_jsonl
#     rec = build_record(symbol=..., pick_date_iso=..., stage_results=<from trace>,
#                        composite_score=..., composite_tau=..., trigger_regime=...,
#                        rank=..., candidate_class="selected",
#                        buy_readiness=<persisted verdict>, signals=<recomputed as-of>,
#                        outcome=build_outcome(...))
#     append_jsonl("data/tuning/tuning_log.jsonl", rec)
#
# The as-of stage_results are already in the pick's trace JSONL, so live emission
# needs no re-run of the pipeline — just a read of the stored trace + the outcome.
