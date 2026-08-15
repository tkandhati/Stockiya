"""Survivor-date audit (scratch). For each 18-month CSV, find every session
where the name cleared hard gates AND S>=tau, then run the REAL rank_survivors
selection as-of that day to see whether it becomes a CONFIRMED PICK or is
vetoed (and why). Offline."""
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
from backend.stages.rank import rank_survivors, _selection_veto_reason
from backend.snapshot_calc import build_snapshot_from_ohlcv
from backend import indicators as ind

STAGE_FNS = [("HR",hard_rejects.run),("ACS",accum_screen.run),("AC",accumulation.run),
    ("LTV",lt_distribution_veto.run),("LT",lt_flow.run),("CS",consolidation.run),
    ("VD",volume_stage.run),("BR",breakout.run)]

def parse_nse(path):
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    raw.columns = [c.strip().lstrip("﻿") for c in raw.columns]
    raw = raw[raw["SERIES"].str.strip()=="EQ"]
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

def build_result(sym, df):
    ctx=PipelineContext(symbol=f"{sym}.NS",trace_id="e",today_iso="")
    ctx.ohlcv=df
    ctx.snapshot=build_snapshot_from_ohlcv(f"{sym}.NS",df,overrides={"exchange":"NSE"})
    for sid,fn in STAGE_FNS:
        try: r=fn(ctx)
        except Exception as e: r=StageResult(stage_id=sid,passed=False,reason=str(e))
        ctx.stage_results[r.stage_id]=r
    S=compute_composite(ctx.stage_results)
    return PipelineResult(symbol=ctx.symbol,trace_id="e",passed_gates=True,composite_score=S,
        selected=False,rank=None,stage_results=ctx.stage_results,pick_payload={},
        snapshot=ctx.snapshot,ohlcv=df), S, hard_gates_passed(ctx.stage_results)

print(f"tau={COMPOSITE_TAU}\n"+"="*94)
for path in sorted(glob.glob("test_data/18months/*.csv")):
    sym=sym_of(path); df=parse_nse(path); n=len(df)
    print(f"\n### {sym}")
    picked=0; vetoed=0
    for t in range(200,n+1):
        sub=df.iloc[:t]
        r,S,hard=build_result(sym,sub)
        if not (hard and S>=COMPOSITE_TAU): continue
        sel=rank_survivors([r],top_n=5)
        date=sub.index[-1].date()
        atr=ind.atr_pct(sub,14)
        c=r.confirmation_components or {}
        if r.selected:
            picked+=1
            print(f"  {date}  S={S:.2f} ATR={atr:.1f}%  -> PICK  "
                  f"timing={c.get('entry_timing')} stage={c.get('weinstein_stage')} "
                  f"tier={c.get('selection_tier')}")
        else:
            vetoed+=1
            reason=_selection_veto_reason(r)
            print(f"  {date}  S={S:.2f} ATR={atr:.1f}%  -> veto [{reason}]")
    print(f"    => {picked} confirmed picks, {vetoed} vetoed (of the survivor days)")
