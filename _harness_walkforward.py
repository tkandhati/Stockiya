"""Walk-forward replay (scratch). For each 18-month CSV, slice the tape to
each historical session t (>=200 bars) and run the REAL chain as-of that day.
Answers empirically: on how many days would each name have been a survivor
(S>=tau) / a near-miss / a breakout-trigger day, and WHICH stage was binding.
Offline; reads only pasted CSVs.
"""
from __future__ import annotations
import glob, os, re
import pandas as pd

from backend.pipeline import (
    PipelineContext, StageResult, compute_composite, hard_gates_passed,
    classify_trigger, COMPOSITE_TAU,
)
from backend.stages import (
    hard_rejects, accum_screen, accumulation, lt_distribution_veto,
    lt_flow, consolidation, volume as volume_stage, breakout,
)
from backend import indicators as ind

STAGE_FNS = [
    ("HR", hard_rejects.run), ("ACS", accum_screen.run),
    ("AC", accumulation.run), ("LTV", lt_distribution_veto.run),
    ("LT", lt_flow.run), ("CS", consolidation.run),
    ("VD", volume_stage.run), ("BR", breakout.run),
]

def parse_nse(path):
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    raw.columns = [c.strip().lstrip("﻿") for c in raw.columns]
    raw = raw[raw["SERIES"].str.strip() == "EQ"]
    num = lambda s: pd.to_numeric(s.str.replace(",", "", regex=False).str.strip(), errors="coerce")
    d = raw["DATE"].str.strip()
    dt = pd.to_datetime(d, format="%d-%b-%Y", errors="coerce").fillna(
         pd.to_datetime(d, format="%d-%b-%y", errors="coerce"))
    df = pd.DataFrame({"Open": num(raw["OPEN"]), "High": num(raw["HIGH"]),
                       "Low": num(raw["LOW"]), "Close": num(raw["CLOSE"]),
                       "Volume": num(raw["VOLUME"])})
    df.index = dt
    df = df.dropna(subset=["Open","High","Low","Close"])
    df = df[df["Volume"].fillna(0) > 0].sort_index()
    df["Volume"] = df["Volume"].astype("int64")
    return df

def sym_of(p):
    m = re.search(r"Quote-Equity-([A-Z0-9&-]+)-EQ-", os.path.basename(p))
    return m.group(1) if m else os.path.basename(p)

def eval_asof(df):
    ctx = PipelineContext(symbol="x", trace_id="e", today_iso="")
    ctx.ohlcv = df
    for sid, fn in STAGE_FNS:
        try: r = fn(ctx)
        except Exception as e: r = StageResult(stage_id=sid, passed=False, reason=str(e))
        ctx.stage_results[r.stage_id] = r
    S = compute_composite(ctx.stage_results)
    hard = hard_gates_passed(ctx.stage_results)
    sr = ctx.stage_results
    return {
        "S": S, "hard": hard,
        "AC": sr["AC"].passed, "VD": sr["VD"].passed, "BR": sr["BR"].passed,
        "CS": sr["CS"].passed, "LT": sr["LT"].passed, "LTV": sr["LTV"].passed,
        "ac_score": sr["AC"].score if sr["AC"].passed else 0.0,
        "atr": ind.atr_pct(df, 14),
        "regime": classify_trigger(sr),
    }

print(f"tau={COMPOSITE_TAU}   (survivor = hard_gates AND S>=tau)\n" + "="*94)
for path in sorted(glob.glob("test_data/18months/*.csv")):
    sym = sym_of(path); df = parse_nse(path)
    n = len(df)
    recs = []
    for t in range(200, n + 1):          # need >=200 bars
        sub = df.iloc[:t]
        e = eval_asof(sub)
        e["date"] = sub.index[-1].date()
        recs.append(e)
    R = pd.DataFrame(recs)
    surv = R[R.hard & (R.S >= COMPOSITE_TAU)]
    near = R[R.hard & (R.S >= 0.20) & (R.S < COMPOSITE_TAU)]
    br_days = R[R.BR]
    ac_days = R[R.AC]
    imax = R.S.idxmax()
    print(f"\n### {sym}   ({n} bars; replayed {len(R)} sessions "
          f"{R.date.iloc[0]} -> {R.date.iloc[-1]})")
    print(f"    survivor days (S>=tau):   {len(surv):>3}   "
          f"near-miss days (0.20-0.28): {len(near):>3}")
    print(f"    breakout[BR] trigger days:{len(br_days):>3}   "
          f"tight-coil[AC] days:        {len(ac_days):>3}   "
          f"hard-gate-blocked days: {int((~R.hard).sum())}")
    print(f"    max S = {R.S.max():.3f} on {R.date.loc[imax]}  "
          f"(AC={R.AC.loc[imax]} VD={R.VD.loc[imax]} BR={R.BR.loc[imax]} "
          f"ATR={R.atr.loc[imax]:.2f}%)")
    # stage pass-rate over the whole replay
    print(f"    stage pass-rate:  LT={R.LT.mean()*100:.0f}%  CS={R.CS.mean()*100:.0f}%  "
          f"AC={R.AC.mean()*100:.0f}%  VD={R.VD.mean()*100:.0f}%  BR={R.BR.mean()*100:.0f}%  "
          f"LTV={R.LTV.mean()*100:.0f}%")
    if len(surv):
        print(f"    >>> SURVIVOR dates: " +
              ", ".join(f"{d}(S={s:.2f})" for d, s in zip(surv.date, surv.S)))
    # If BR fired, show what S looked like on those days (tests proposal-3 premise)
    if len(br_days):
        print(f"    BR-day S values: " +
              ", ".join(f"{d}:{s:.2f}" for d, s in zip(br_days.date, br_days.S)))
