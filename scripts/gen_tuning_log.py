"""Generate the LLM-tuning log from the offline backtest files.

Runs the LIVE pipeline (same gates, weights, tau, DV-block) on every session of
every stock in test_data/18months, and for each day that clears the gate boundary
emits ONE backend.tuning_log record: decision inputs (as-of, no lookahead) paired
with the graded forward outcome (app's own exit ladder + raw buy&hold horizons)
and the real buy_readiness verdict.

Output:
    data/tuning/tuning_log.jsonl   — one record per line (feed this to the LLM)
    data/tuning/tuning_digest.md   — human/LLM-readable summary + field legend

Offline, deterministic, no network. Run:
    PYTHONPATH=. python scripts/gen_tuning_log.py
"""
from __future__ import annotations

import glob
import os
import re
import statistics as st
from collections import Counter

import pandas as pd

from backend.pipeline import (PipelineContext, PipelineResult, StageResult,
    compute_composite, hard_gates_passed, COMPOSITE_TAU, classify_trigger)
from backend.stages import (hard_rejects, accum_screen, accumulation,
    lt_distribution_veto, lt_flow, consolidation, volume as volume_stage, breakout,
    distribution_veto)
from backend.stages.rank import rank_survivors
from backend.snapshot_calc import build_snapshot_from_ohlcv
from backend.backtest import forward_walk
from backend import volume_signals as vs
from backend.buy_readiness import assess_buy_readiness
from backend import tuning_log as tl

# DV runs AFTER BR (it needs the full tape); it is a hard gate in block mode, so
# including it makes the offline log faithful to live distribution-veto selection.
STAGE_FNS = [("HR", hard_rejects.run), ("ACS", accum_screen.run), ("AC", accumulation.run),
             ("LTV", lt_distribution_veto.run), ("LT", lt_flow.run), ("CS", consolidation.run),
             ("VD", volume_stage.run), ("BR", breakout.run), ("DV", distribution_veto.run)]

DATA_GLOB = "test_data/18months/*.csv"
OUT_JSONL = "data/tuning/tuning_log.jsonl"
OUT_DIGEST = "data/tuning/tuning_digest.md"
BOUNDARY_BAND = 0.10   # also log days within tau-0.10 (the decision boundary)
MIN_FWD_TO_GRADE = 5   # need at least a few forward bars to say anything


def parse_nse(path):
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    raw.columns = [c.strip().lstrip("﻿") for c in raw.columns]
    raw = raw[raw["SERIES"].str.strip() == "EQ"]
    num = lambda s: pd.to_numeric(s.str.replace(",", "", regex=False).str.strip(), errors="coerce")
    d = raw["DATE"].str.strip()
    dt = pd.to_datetime(d, format="%d-%b-%Y", errors="coerce").fillna(
        pd.to_datetime(d, format="%d-%b-%y", errors="coerce"))
    df = pd.DataFrame({"Open": num(raw["OPEN"]), "High": num(raw["HIGH"]), "Low": num(raw["LOW"]),
                       "Close": num(raw["CLOSE"]), "Volume": num(raw["VOLUME"])})
    df.index = dt
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Volume"].fillna(0) > 0].sort_index()
    df["Volume"] = df["Volume"].astype("int64")
    return df


def sym_of(p):
    m = re.search(r"Quote-Equity-([A-Z0-9&-]+)-EQ-", os.path.basename(p))
    return m.group(1) if m else p


def run_gates(sym, sub):
    ctx = PipelineContext(symbol=f"{sym}.NS", trace_id="e", today_iso="")
    ctx.ohlcv = sub
    ctx.snapshot = build_snapshot_from_ohlcv(f"{sym}.NS", sub, overrides={"exchange": "NSE"})
    for sid, fn in STAGE_FNS:
        try:
            r = fn(ctx)
        except Exception as e:
            r = StageResult(stage_id=sid, passed=False, reason=str(e))
        ctx.stage_results[r.stage_id] = r
    return ctx


def minimal_payload(ctx, signals, selected):
    """Build the fields backend.buy_readiness.assess_buy_readiness reads, from the
    stage results + volume_signals. Offline simplification (documented in digest):
    upstream contradictions / durable_slow / tier demotions are not reconstructed,
    so buy_readiness money-flow relies on CMF/A-D directly (which is present)."""
    sr = ctx.stage_results
    passed = [sid for sid, r in sr.items() if r.passed]
    failed = [sid for sid, r in sr.items() if not r.passed]
    ltv = (sr.get("LTV").features if sr.get("LTV") else {}) or {}
    br_passed = bool(sr.get("BR") and sr["BR"].passed)
    close = float(ctx.ohlcv["Close"].iloc[-1])
    g = lambda a: getattr(signals, a, None) if signals is not None else None
    return {
        "confirmation": {
            "money_flow": {"cmf_21d": g("cmf_21d"), "cmf_60d": None,
                           "ad_line_slope_pct": g("ad_line_slope_pct")},
            "entry_timing": g("entry_timing"),
            "weinstein_stage": g("weinstein_stage"),
        },
        "flow_timeframes": {"obv_90d_norm_slope_pct": ltv.get("obv_90d_norm_slope_pct"),
                            "obv_180d_norm_slope_pct": None,
                            "up_down_vol_ratio_90d": None},
        "entry_stage": "",
        "early_accumulation": {"features": {"durable_slow": False}},
        "accumulation_assessment": {"contradictions": []},
        "gate_confirmation_status": {"passed": passed, "failed": failed},
        "entry_stage_features": {"br_passed_today": br_passed},
        "selection_tier": "confirmed" if selected else None,
        "price_plan": {"entry": close}, "current_price": close,
    }


def grade(df, i):
    fwd = df.iloc[i + 1:]
    nb = len(fwd)
    if nb < MIN_FWD_TO_GRADE:
        return None
    entry = float(fwd["Open"].iloc[0])
    fw = forward_walk(fwd, entry, hold_days=90)
    bh = {}
    for h in tl.BUY_HOLD_HORIZONS:
        if nb > h:
            bh[f"t{h}"] = round((float(fwd["Close"].iloc[h]) / entry - 1.0) * 100, 2)
    return tl.build_outcome(
        exit_ladder_return_pct=fw["return_pct"], exit_reason=fw["exit_reason"],
        exit_day=fw["exit_day"], hit_stop=fw["hit_stop_day"] is not None,
        hit_t1_day=fw["hit_t1_day"], forward_window_bars=nb,
        buy_hold_by_horizon=bh, entry_fill_price=entry)


def main():
    records = []
    for path in sorted(glob.glob(DATA_GLOB)):
        sym = sym_of(path)
        df = parse_nse(path)
        n = len(df)
        for t in range(200, n + 1):
            sub = df.iloc[:t]
            ctx = run_gates(sym, sub)
            if not hard_gates_passed(ctx.stage_results):
                continue
            S = compute_composite(ctx.stage_results)
            if S < COMPOSITE_TAU - BOUNDARY_BAND:
                continue  # far below the boundary — not a tuning-relevant candidate
            regime = classify_trigger(ctx.stage_results)
            selected = False
            if S >= COMPOSITE_TAU:
                res = PipelineResult(symbol=f"{sym}.NS", trace_id="e", passed_gates=True,
                    composite_score=S, selected=False, rank=None,
                    stage_results=ctx.stage_results, pick_payload={},
                    snapshot=ctx.snapshot, ohlcv=sub)
                rank_survivors([res], top_n=5)
                selected = res.selected
            cls = ("selected" if selected
                   else "qualified_not_selected" if S >= COMPOSITE_TAU
                   else "sub_threshold")
            try:
                signals = vs.compute(sub, f"{sym}.NS")
            except Exception:
                signals = None
            payload = minimal_payload(ctx, signals, selected)
            readiness = assess_buy_readiness(payload)
            outcome = grade(df, t - 1) or {"graded": False, "reason": "insufficient forward bars"}
            rec = tl.build_record(
                symbol=f"{sym}.NS", pick_date_iso=str(df.index[t - 1].date()),
                stage_results=ctx.stage_results, composite_score=S,
                composite_tau=COMPOSITE_TAU, trigger_regime=regime,
                rank=1 if selected else None, candidate_class=cls,
                buy_readiness=readiness, signals=signals, outcome=outcome)
            records.append(rec)

    tl.write_jsonl(OUT_JSONL, records)
    write_digest(records)
    print(f"wrote {len(records)} records -> {OUT_JSONL}")
    print(f"digest -> {OUT_DIGEST}")


def write_digest(records):
    graded = [r for r in records if r["outcome"].get("graded")]
    def by(keyfn):
        buckets = {}
        for r in graded:
            k = keyfn(r)
            buckets.setdefault(k, []).append(r["outcome"]["exit_ladder_return_pct"])
        return {k: (len(v), round(st.mean(v), 2), sum(1 for x in v if x > 0))
                for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))}

    lines = []
    lines.append("# Tuning log — digest\n")
    lines.append(f"Records: **{len(records)}** | graded: **{len(graded)}** "
                 f"| classes: {dict(Counter(r['as_of']['decision']['candidate_class'] for r in records))}\n")
    lines.append("## Mean exit-ladder return by group (n, mean%, wins)\n")
    lines.append(f"- by buy_readiness.state: `{by(lambda r: (r['as_of']['decision'].get('buy_readiness') or {}).get('state'))}`")
    lines.append(f"- by BR fired: `{by(lambda r: r['as_of']['features']['trigger']['br_passed'])}`")
    lines.append(f"- by trigger_regime: `{by(lambda r: r['as_of']['decision']['trigger_regime'])}`")
    lines.append(f"- by weinstein_stage: `{by(lambda r: r['as_of']['money_flow']['weinstein_stage'])}`")
    lines.append(f"- by actionable_buy: `{by(lambda r: r['as_of']['decision']['actionable_buy'])}`\n")
    lines.append("## Record shape (one JSON object per line)\n")
    lines.append("```json")
    if records:
        import json
        lines.append(json.dumps(records[0], ensure_ascii=False, indent=2, default=str))
    lines.append("```\n")
    lines.append("## Field legend\n")
    lines.append(
        "- `as_of` = decision-time state, data <= `pick_date` (NO lookahead).\n"
        "- `outcome` = future label. `exit_ladder_return_pct` uses the app's own "
        "stop/T1+BE/T2/day-45/day-90 ladder, fill = next open. "
        "`buy_hold_return_pct.tN` = raw unmanaged return N trading days out.\n"
        "- `outcome_mature` = window >= 90d OR a terminal ladder exit fired. "
        "Filter on this before trusting a record for tuning.\n"
        "- `features.trigger.br_passed` = did today's bar fire the breakout (the "
        "buy/watch pivot). `features.flow.signed_pressure_ewm_hl30` = accumulated "
        "signed flow. `features.long_flow.obv_90d_norm_slope_pct` = the [LTV] axis. "
        "`features.distribution.dv_would_veto` = distribution footprint present.\n"
        "- `decision.candidate_class`: `selected` (would be surfaced) | "
        "`qualified_not_selected` (cleared tau, not top-N) | `sub_threshold` "
        "(within tau-0.10 boundary band, below tau).\n"
        "- `decision.buy_readiness.state`: `buy` | `watch` | `avoid` (the live label).\n")
    lines.append("## Known scope limits (no silent caps)\n")
    lines.append(
        "- Offline 5-name sample: cross-sectional ranking is degenerate (one stock "
        "per frame), so `rank` is 1 for any selected day.\n"
        "- buy_readiness is fed a MINIMAL payload here: upstream distribution "
        "contradictions, `durable_slow`, and tier demotions are NOT reconstructed "
        "offline, so its money-flow layer leans on CMF/A-D directly. Live emission "
        "via the MATURED-OUTCOME HOOK carries the full persisted payload.\n"
        "- Distribution-VETOED days do not appear (they fail hard gates before a "
        "candidate forms). Add a `veto` record_type in v2 to tune the veto itself.\n")
    from pathlib import Path
    p = Path(OUT_DIGEST)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
