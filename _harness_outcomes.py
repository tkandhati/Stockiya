"""Outcome grading (scratch). For each 18-month CSV, find every CONFIRMED-PICK
date via the real ranker, then grade it against forward prices ALREADY IN THE
FILE: return at +21/+42/+63 trading bars, plus max favorable / adverse excursion
over 63 bars. Picks too close to the file end (insufficient forward bars) are
flagged, not graded. Offline; reads only pasted CSVs."""
from __future__ import annotations
import glob, os, re
import pandas as pd

from backend.pipeline import (
    PipelineContext, PipelineResult, StageResult, compute_composite,
    hard_gates_passed, COMPOSITE_TAU,
)
from backend.stages import (
    hard_rejects, accum_screen, accumulation, lt_distribution_veto,
    lt_flow, consolidation, volume as volume_stage, breakout,
)
from backend.stages.rank import rank_survivors
from backend.snapshot_calc import build_snapshot_from_ohlcv

STAGE_FNS = [("HR",hard_rejects.run),("ACS",accum_screen.run),("AC",accumulation.run),
    ("LTV",lt_distribution_veto.run),("LT",lt_flow.run),("CS",consolidation.run),
    ("VD",volume_stage.run),("BR",breakout.run)]

def parse_nse(path):
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
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

def is_pick(sym, sub):
    ctx=PipelineContext(symbol=f"{sym}.NS",trace_id="e",today_iso=""); ctx.ohlcv=sub
    ctx.snapshot=build_snapshot_from_ohlcv(f"{sym}.NS",sub,overrides={"exchange":"NSE"})
    for sid,fn in STAGE_FNS:
        try: r=fn(ctx)
        except Exception as e: r=StageResult(stage_id=sid,passed=False,reason=str(e))
        ctx.stage_results[r.stage_id]=r
    S=compute_composite(ctx.stage_results)
    if not (hard_gates_passed(ctx.stage_results) and S>=COMPOSITE_TAU): return None
    res=PipelineResult(symbol=f"{sym}.NS",trace_id="e",passed_gates=True,composite_score=S,
        selected=False,rank=None,stage_results=ctx.stage_results,pick_payload={},
        snapshot=ctx.snapshot,ohlcv=sub)
    rank_survivors([res],top_n=5)
    return res if res.selected else None

def fwd(df, i, h):
    if i+h >= len(df): return None
    e=float(df["Close"].iloc[i]); p=float(df["Close"].iloc[i+h])
    return (p/e-1)*100

def excursion(df, i, h=63):
    j=min(i+h, len(df)-1)
    if j<=i: return None,None
    e=float(df["Close"].iloc[i])
    seg=df.iloc[i+1:j+1]
    mfe=(float(seg["High"].max())/e-1)*100
    mae=(float(seg["Low"].min())/e-1)*100
    return mfe, mae

print("Forward grading (close-to-close), horizons in trading bars ~ 1/2/3 months.")
print("MFE/MAE = best/worst intrabar move within 63 bars.  '--' = insufficient forward bars.")
print("="*94)
all_rows=[]
for path in sorted(glob.glob("test_data/18months/*.csv")):
    sym=sym_of(path); df=parse_nse(path); n=len(df)
    print(f"\n### {sym}")
    print(f"    {'pick date':<12}{'entry':>9}{'+21d':>8}{'+42d':>8}{'+63d':>8}{'MFE':>8}{'MAE':>8}")
    picks=[]
    prev=False
    for t in range(200,n+1):
        res=is_pick(sym, df.iloc[:t])
        if res is not None:
            i=t-1  # index of pick day in full df
            picks.append(i)
    for i in picks:
        date=df.index[i].date(); e=float(df["Close"].iloc[i])
        r21,r42,r63=fwd(df,i,21),fwd(df,i,42),fwd(df,i,63)
        mfe,mae=excursion(df,i,63)
        f=lambda x: f"{x:+6.1f}%" if x is not None else "   -- "
        print(f"    {str(date):<12}{e:>9.1f}{f(r21):>8}{f(r42):>8}{f(r63):>8}{f(mfe):>8}{f(mae):>8}")
        if r63 is not None:
            all_rows.append((sym,date,r21,r63,mfe,mae))

# Aggregate over gradeable picks
print("\n"+"="*94)
if all_rows:
    import statistics as st
    r21s=[r for _,_,r,_,_,_ in all_rows]
    r63s=[r for _,_,_,r,_,_ in all_rows]
    win63=sum(1 for r in r63s if r>0)
    print(f"GRADEABLE PICKS (>=63 fwd bars): {len(all_rows)}")
    print(f"  +21d mean {st.mean(r21s):+.1f}%   +63d mean {st.mean(r63s):+.1f}%   "
          f"+63d win-rate {win63}/{len(r63s)} ({100*win63/len(r63s):.0f}%)")
    print(f"  +63d best {max(r63s):+.1f}%  worst {min(r63s):+.1f}%")
else:
    print("No gradeable picks with full forward runway.")
