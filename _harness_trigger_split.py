"""Split the sample's picks by whether the [BR] breakout TRIGGER actually fired,
then exit-grade each group. Tests the review's 'never buy before a confirmed
breakout' against the app's own early-coil admission. Offline, 5-name sample."""
from __future__ import annotations
import glob, os, re, statistics as st
import pandas as pd

from backend.pipeline import (PipelineContext, PipelineResult, StageResult,
    compute_composite, hard_gates_passed, COMPOSITE_TAU, classify_trigger)
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

def pick_info(sym,sub):
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
    if not res.selected: return None
    br=ctx.stage_results.get("BR")
    return {"br_fired": bool(br and br.passed),
            "regime": classify_trigger(ctx.stage_results)}

MIN_FWD=63
groups={"BR fired (real trigger / 'buy')":[], "no BR (pre-breakout coil / 'watch')":[]}
for path in sorted(glob.glob("test_data/18months/*.csv")):
    sym=sym_of(path); df=parse_nse(path); n=len(df)
    for t in range(200,n+1):
        info=pick_info(sym,df.iloc[:t])
        if info is None: continue
        i=t-1; fwd=df.iloc[i+1:]
        if len(fwd)<MIN_FWD: continue
        fw=forward_walk(fwd,float(fwd["Open"].iloc[0]),hold_days=90)
        key="BR fired (real trigger / 'buy')" if info["br_fired"] else "no BR (pre-breakout coil / 'watch')"
        groups[key].append((sym,str(df.index[i].date()),fw["return_pct"],fw["hit_stop_day"] is not None,info["regime"]))

for key,rows in groups.items():
    print(f"\n=== {key} ===  n={len(rows)}")
    for sym,dt,r,stp,reg in rows:
        print(f"   {sym:<10}{dt:<12}{r:>6.1f}%  stop={stp}  regime={reg}")
    if rows:
        rets=[r for _,_,r,_,_ in rows]; wins=sum(1 for r in rets if r>0); stops=sum(1 for *_,s,_ in [(x) for x in rows] if s)
        stops=sum(1 for _,_,_,s,_ in rows if s)
        print(f"   -> mean={st.mean(rets):+.2f}%  median={st.median(rets):+.2f}%  "
              f"win={wins}/{len(rets)} ({100*wins/len(rets):.0f}%)  stopped={stops}/{len(rets)}")
