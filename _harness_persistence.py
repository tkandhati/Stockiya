"""Test two anti-churn proposals against the 18-month data (scratch, offline):
  Q1 "scan longer than today"  -> require a name to be a PICK for 2 consecutive
     sessions before entering (Rule B), vs today-only rising-edge entry (Rule A).
  Q2 "avoid next-day flip"     -> measure the actual pick->non-pick next-day
     flip rate, and whether flip-prone entries are the losers.
Grades each entry with the app's own exit ladder (backtest.forward_walk)."""
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
    rank_survivors([res],top_n=5); return res.selected

MIN_FWD=63
def grade(df,i):
    fwd=df.iloc[i+1:]
    if len(fwd)<MIN_FWD: return None
    fw=forward_walk(fwd,float(fwd["Open"].iloc[0]),hold_days=90)
    return fw["return_pct"], fw["exit_reason"], fw["hit_stop_day"] is not None

def stats(entries):
    g=[grade(df,i) for _,df,i in entries]
    g=[x for x in g if x is not None]
    if not g: return "(no gradeable entries)"
    rets=[r for r,_,_ in g]; wins=sum(1 for r in rets if r>0); stops=sum(1 for _,_,s in g if s)
    import statistics as st
    return (f"n={len(g)}  mean={st.mean(rets):+.1f}%  win={wins}/{len(g)} ({100*wins/len(g):.0f}%)  "
            f"stopped={stops}/{len(g)} ({100*stops/len(g):.0f}%)")

# Build pick[] series per stock
frames={}
picks_by_stock={}
for path in sorted(glob.glob("test_data/18months/*.csv")):
    sym=sym_of(path); df=parse_nse(path); frames[sym]=df; n=len(df)
    series=[]  # (t, i, picked)
    for t in range(200,n+1):
        series.append((t, t-1, is_pick(sym, df.iloc[:t])))
    picks_by_stock[sym]=series

# Q2: next-day flip rate (pick -> non-pick next session)
print("Q2  NEXT-DAY FLIP (a name that is a PICK today, not a pick tomorrow)")
print("-"*70)
tot_pickdays=0; tot_flips=0
for sym,series in picks_by_stock.items():
    picked=[p for _,_,p in series]
    pd_days=sum(picked)
    flips=sum(1 for k in range(len(picked)-1) if picked[k] and not picked[k+1])
    tot_pickdays+=pd_days; tot_flips+=flips
    print(f"  {sym:<12} pick-days={pd_days:>3}  next-day flips={flips:>3}  "
          f"flip-rate={100*flips/pd_days:.0f}%" if pd_days else f"  {sym:<12} no pick-days")
print(f"  {'ALL':<12} pick-days={tot_pickdays}  flips={tot_flips}  "
      f"flip-rate={100*tot_flips/tot_pickdays:.0f}%")

# Rising edges -> Rule A (today-only) entries; length-2+ runs -> Rule B (persisted) entries
ruleA=[]; ruleB=[]; run_len_hist={}
for sym,series in picks_by_stock.items():
    df=frames[sym]
    k=0; N=len(series)
    while k<N:
        if series[k][2]:
            j=k
            while j+1<N and series[j+1][2]: j+=1
            run=j-k+1
            run_len_hist[run]=run_len_hist.get(run,0)+1
            ruleA.append((sym,df,series[k][1]))                 # first day of run
            if run>=2: ruleB.append((sym,df,series[k+1][1]))    # second day (persisted)
            k=j+1
        else:
            k+=1

print("\nQ1  PERSISTENCE — 'scan longer than today'")
print("-"*70)
print(f"  pick-run length histogram (how many distinct pick episodes of each length): {dict(sorted(run_len_hist.items()))}")
print(f"  Rule A (enter on day-1 of a pick run, today-only): {stats(ruleA)}")
print(f"  Rule B (enter only if pick persists >=2 sessions):  {stats(ruleB)}")
print(f"  (Rule B discards the {run_len_hist.get(1,0)} one-day-only pick episodes.)")
