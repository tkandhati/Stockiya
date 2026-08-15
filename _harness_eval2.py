"""Full-history evaluation harness (scratch, not part of the pipeline).

Runs the REAL per-ticker chain + REAL rank_survivors selection on the
18-month NSE CSVs in test_data/18months/. Bypasses [U]/[I] (fetch/universe)
by building the context directly, then hands PipelineResults to the actual
ranker so confirmation score, bonuses, entry-timing, day-0 exit-watch and the
selection vetoes are computed by production code.

Offline: reads only pasted CSVs. No network.
"""
from __future__ import annotations

import glob, os, re
import pandas as pd

from backend.pipeline import (
    PipelineContext, PipelineResult, StageResult,
    compute_composite, hard_gates_passed, classify_trigger,
    _reweight_for_trigger, COMPOSITE_TAU, COMPOSITE_WEIGHTS,
)
from backend.stages import (
    hard_rejects, accum_screen, accumulation, lt_distribution_veto,
    lt_flow, consolidation, volume as volume_stage, breakout,
)
from backend.stages.rank import rank_survivors, rank_lead_fallback, _selection_veto_reason
from backend.snapshot_calc import build_snapshot_from_ohlcv
from backend import indicators as ind

STAGE_FNS = [
    ("HR", hard_rejects.run), ("ACS", accum_screen.run),
    ("AC", accumulation.run), ("LTV", lt_distribution_veto.run),
    ("LT", lt_flow.run), ("CS", consolidation.run),
    ("VD", volume_stage.run), ("BR", breakout.run),
]


def parse_nse(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    raw.columns = [c.strip().lstrip("﻿") for c in raw.columns]
    raw = raw[raw["SERIES"].str.strip() == "EQ"]

    def num(s):
        return pd.to_numeric(s.str.replace(",", "", regex=False).str.strip(),
                             errors="coerce")

    dates = raw["DATE"].str.strip()
    dt = pd.to_datetime(dates, format="%d-%b-%Y", errors="coerce")
    dt = dt.fillna(pd.to_datetime(dates, format="%d-%b-%y", errors="coerce"))

    df = pd.DataFrame({
        "Open": num(raw["OPEN"]), "High": num(raw["HIGH"]),
        "Low": num(raw["LOW"]), "Close": num(raw["CLOSE"]),
        "Volume": num(raw["VOLUME"]),
    })
    df.index = dt
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Volume"].fillna(0) > 0].sort_index()
    df["Volume"] = df["Volume"].astype("int64")
    return df


def symbol_of(path: str) -> str:
    m = re.search(r"Quote-Equity-([A-Z0-9&-]+)-EQ-", os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)


def build_result(path: str) -> PipelineResult:
    sym = symbol_of(path)
    df = parse_nse(path)
    ctx = PipelineContext(symbol=f"{sym}.NS", trace_id="eval", today_iso="")
    ctx.ohlcv = df
    ctx.snapshot = build_snapshot_from_ohlcv(f"{sym}.NS", df, overrides={"exchange": "NSE"})
    for sid, fn in STAGE_FNS:
        try:
            r = fn(ctx)
        except Exception as e:
            r = StageResult(stage_id=sid, passed=False, reason=f"crash: {e}")
        ctx.stage_results[r.stage_id] = r
    S = compute_composite(ctx.stage_results)
    ctx.composite_score = S
    return PipelineResult(
        symbol=ctx.symbol, trace_id=ctx.trace_id,
        passed_gates=all(r.passed for r in ctx.stage_results.values()),
        composite_score=S, selected=False, rank=None,
        stage_results=ctx.stage_results, pick_payload={},
        snapshot=ctx.snapshot, ohlcv=df,
    )


def stage_line(sid, r):
    mark = "PASS" if r.passed else "fail"
    sc = f"{r.score:.3f}" if (r.passed and r.score) else "  -  "
    return f"    [{sid:<3}] {mark} {sc}  {(r.reason or '')[:74]}"


print("=" * 94)
print(f"tau={COMPOSITE_TAU}   base weights={ {k:v for k,v in COMPOSITE_WEIGHTS.items() if v} }")
print("=" * 94)

results = []
for path in sorted(glob.glob("test_data/18months/*.csv")):
    r = build_result(path)
    results.append(r)

# Per-ticker detail
for r in results:
    df = r.ohlcv
    S = r.composite_score
    regime = classify_trigger(r.stage_results)
    hard_ok = hard_gates_passed(r.stage_results)
    survivor = hard_ok and S >= COMPOSITE_TAU
    last = float(df["Close"].iloc[-1])
    ma50 = ind.sma(df["Close"], 50); ma200 = ind.sma(df["Close"], 200)
    ret30 = (last/float(df["Close"].iloc[-31])-1)*100
    obv = ind.obv(df["Close"], df["Volume"])
    print(f"\n### {r.symbol}  ({len(df)} bars {df.index[0].date()}->{df.index[-1].date()})")
    print(f"    last=Rs{last:.1f}  ret30d={ret30:+.1f}%  ext_vs_50dMA={(last/ma50-1)*100:+.1f}%  "
          f"vs_200dMA={(last/ma200-1)*100:+.1f}%  ATR={ind.atr_pct(df,14):.2f}%")
    print(f"    OBV90d(norm)={ind.obv_norm_slope_pct(obv,90):+.0f}%  OBV180d={ind.obv_norm_slope_pct(obv,180):+.0f}%  "
          f"updown90={ind.up_down_vol_ratio(df['Close'],df['Volume'],90):.2f}  "
          f"days_in_12band={ind.days_within_band(df['Close'],0.12)}")
    for sid, _ in STAGE_FNS:
        print(stage_line(sid, r.stage_results[sid]))
    # Show reweighted weights if pre_breakout
    if regime == "pre_breakout":
        rw = _reweight_for_trigger(COMPOSITE_WEIGHTS, regime)
        print(f"    (pre_breakout reweight -> { {k:round(v,3) for k,v in rw.items() if v} })")
    print(f"    ==> S={S:.3f}  regime={regime}  hard={'OK' if hard_ok else 'FAIL'}  "
          f"survivor(S>=tau)={'YES' if survivor else 'no'}")

# Full selection over the survivor set (production ranker)
hard_survivors = [r for r in results if hard_gates_passed(r.stage_results)]
survivors = [r for r in hard_survivors if r.composite_score >= COMPOSITE_TAU]
print("\n" + "=" * 94)
print(f"SELECTION:  {len(results)} evaluated | {len(hard_survivors)} cleared hard gates | "
      f"{len(survivors)} cleared S>=tau")

selected = rank_survivors(list(survivors), top_n=5)
lead = None
if not selected:
    lead = rank_lead_fallback(list(hard_survivors))
    if lead:
        selected = [lead]

if selected:
    print(f"\nPICKS ({'LEAD-WATCH fallback' if lead else 'confirmed'}):")
    for p in selected:
        c = p.confirmation_components or {}
        print(f"  #{p.rank}  {p.symbol}  confirmation={p.confirmation_score:.3f}  "
              f"tier={c.get('selection_tier')}")
        print(f"       entry_timing={c.get('entry_timing')}  weinstein={c.get('weinstein_stage')}  "
              f"day0_exit_watch={c.get('day0_exit_watch')}")
        print(f"       bonuses={c.get('bonuses_fired')}")
else:
    print("\nNO PICKS (nothing cleared confirmation, no qualifying lead).")

# Why each non-selected survivor was vetoed
print("\nVETO / STATUS per hard-gate survivor:")
for r in hard_survivors:
    # ensure confirmation computed (rank_survivors mutates only its input list)
    reason = _selection_veto_reason(r) if r.confirmation_components else "(not ranked: below tau)"
    tag = "SELECTED" if r.selected else (reason or "eligible-not-topN")
    print(f"  {r.symbol:<16} S={r.composite_score:.3f}  conf={r.confirmation_score:.3f}  -> {tag}")
