"""Exit-applied grading (scratch). For each confirmed-pick date, run the app's
OWN exit ladder (backend.backtest.forward_walk: stop / T1+BE / T2 / day-45
tighten / day-90 hard-exit) on the forward bars ALREADY in the 18-month file.
Fill = next-day open, exactly as the backtest does. Compares raw buy&hold-63d
vs the exit-managed return. Offline."""
from __future__ import annotations
import glob, os, re
import pandas as pd

from backend.pipeline import (PipelineContext, PipelineResult, StageResult,
    compute_composite, hard_gates_passed, COMPOSITE_TAU)
from backend.stages import (hard_rejects, accum_screen, accumulation,
    lt_distribution_veto, lt_flow, consolidation, volume as volume_stage, breakout)
from backend.stages.rank import rank_survivors
from backend.snapshot_calc import build_snapshot_from_ohlcv
from backend.backtest import forward_walk

STAGE_FNS=[("HR",hard_rejects.run),("ACS",accum_screen.run),("AC",accumulation.run),
    ("LTV",lt_distribution_veto.run),("LT",lt_flow.run),("CS",consolidation.run),
    ("VD",volume_stage.run),("BR",breakout.run)]

def parse_nse(path):
    raw=pd.read_csv(path,dtype=str,encoding="utf-8-sig")
    raw.columns=[c.strip().lstrip("﻿") for c in raw.columns]
    raw=raw[raw["SERIES"].str.strip()=="EQ"]
    num=lambda s: pd.to_numeric(s.str.replace(",","",regex=False).str.strip(),errors="coerce")
    d=raw["DATE"].str.strip()
    dt=pd.to_datetime(d,format="%d-%b-%Y",errors="coerce").fillna(pd.to_datetime(d,format="%d-%b-%y",errors="coerce"))
    df=pd.DataFrame({"Open":num(raw["OPEN"]),"High":num(raw["HIGH"]),"Low":num(raw["LOW"]),
                     "Close":num(raw["CLOSE"]),"Volume":num(raw["VOLUME"])})
    df.index=dt; df=df.dropna(subset=["Open","High","Low","Close"])
    df=df[df["Volume"].fillna(0)>0].sort_index(); df["Volume"]=df["Volume"].astype("int64")
    return df

def sym_of(p):
    m=re.search(r"Quote-Equity-([A-Z0-9&-]+)-EQ-",os.path.basename(p)); return m.group(1) if m else p

def is_pick(sym,sub):
    ctx=PipelineContext(symbol=f"{sym}.NS",trace_id="e",today_iso=""); ctx.ohlcv=sub
    ctx.snapshot=build_snapshot_from_ohlcv(f"{sym}.NS",sub,overrides={"exchange":"NSE"})
    for sid,fn in STAGE_FNS:
        try: r=fn(ctx)
        except Exception as e: r=StageResult(stage_id=sid,passed=False,reason=str(e))
        ctx.stage_results[r.stage_id]=r
    S=compute_composite(ctx.stage_results)
    if not (hard_gates_passed(ctx.stage_results) and S>=COMPOSITE_TAU): return False
    res=PipelineResult(symbol=f"{sym}.NS",trace_id="e",passed_gates=True,composite_score=S,
        selected=False,rank=None,stage_results=ctx.stage_results,pick_payload={},
        snapshot=ctx.snapshot,ohlcv=sub)
    rank_survivors([res],top_n=5)
    return res.selected

print("Exit-applied grade — app's own ladder (fill=next open, stop/T1+BE/T2/d45/d90), hold<=90d")
print("="*98)
rets=[]; t1_hits=0; wins=0; graded=0; reasons={}
for path in sorted(glob.glob("test_data/18months/*.csv")):
    sym=sym_of(path); df=parse_nse(path); n=len(df)
    print(f"\n### {sym}")
    print(f"    {'pick date':<12}{'fwd bars':>9}{'exit reason':>22}{'exit day':>9}{'return':>9}{'T1 day':>8}")
    for t in range(200,n+1):
        if not is_pick(sym, df.iloc[:t]): continue
        i=t-1
        fwd_bars=df.iloc[i+1:]
        nb=len(fwd_bars)
        if nb==0:
            print(f"    {str(df.index[i].date()):<12}{nb:>9}{'(no forward bars)':>22}"); continue
        entry_px=float(fwd_bars["Open"].iloc[0])
        fw=forward_walk(fwd_bars, entry_px, hold_days=90)
        r=fw["return_pct"]; reason=fw["exit_reason"]
        incomplete = nb < 90 and reason in ("expiry_flat","t1_then_time")
        flag=" *" if incomplete else ""
        print(f"    {str(df.index[i].date()):<12}{nb:>9}{reason:>22}{fw['exit_day']:>9}"
              f"{r:>8.1f}%{str(fw['hit_t1_day'] or '-'):>8}{flag}")
        if not incomplete:
            rets.append(r); graded+=1
            reasons[reason]=reasons.get(reason,0)+1
            if fw['hit_t1_day']: t1_hits+=1
            if r>0: wins+=1

print("\n"+"="*98)
print("* = forward window shorter than 90d and no ladder exit yet (outcome not final; excluded from stats)")
if graded:
    import statistics as st
    print(f"\nGRADED (complete outcomes): {graded}")
    print(f"  mean return {st.mean(rets):+.2f}%   median {st.median(rets):+.2f}%   "
          f"win-rate {wins}/{graded} ({100*wins/graded:.0f}%)   T1-hit {t1_hits}/{graded} ({100*t1_hits/graded:.0f}%)")
    print(f"  best {max(rets):+.1f}%   worst {min(rets):+.1f}%")
    print(f"  exit reasons: {reasons}")
else:
    print("No complete outcomes.")
