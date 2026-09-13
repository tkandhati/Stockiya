"""Per-pick discriminator diagnostic (scratch, offline).

For every pick the live pipeline would make on the 18-month sample, record the
graded forward outcome AND a battery of ALREADY-EXISTING signals, so we can see
which signal (if any) separates the winners from the -8% losers WITHOUT hand-
tuning. This is the §9 "emit shadow, observe, then let the tuner ratify" step —
here done offline on the sample to pick which shadow is worth promoting.
"""
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
from backend import indicators as ind
from backend import volume_signals as vs

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
    if not (hard_gates_passed(ctx.stage_results) and S>=COMPOSITE_TAU): return None
    res=PipelineResult(symbol=f"{sym}.NS",trace_id="e",passed_gates=True,composite_score=S,
        selected=False,rank=None,stage_results=ctx.stage_results,pick_payload={},
        snapshot=ctx.snapshot,ohlcv=sub)
    rank_survivors([res],top_n=5)
    return ctx.stage_results if res.selected else None

def safe(fn, default=None):
    try:
        v=fn()
        return v
    except Exception:
        return default

def discriminators(sub, stage_results):
    d={}
    # long-term flow (the LTV read, but as a magnitude we can rank on)
    obv=safe(lambda: ind.obv(sub))
    if obv is not None:
        d["obv90"]=safe(lambda: ind.obv_norm_slope_pct(obv,90))
        d["obv180"]=safe(lambda: ind.obv_norm_slope_pct(obv,180))
    # accumulated signed pressure (the "drift")
    p=safe(lambda: ind.signed_volume_pressure(sub))
    if p is not None:
        d["sp_hl30"]=safe(lambda: ind.ewm_signed_pressure(p,30))
        d["sp_hl10"]=safe(lambda: ind.ewm_signed_pressure(p,10))
    infl=safe(lambda: ind.obv_flow_inflection(sub["Close"],sub["Volume"]))
    d["inflect"]=infl[0] if isinstance(infl,tuple) else infl
    d["volz"]=safe(lambda: ind.volume_robust_zscore(sub["Volume"],50))
    d["evr_ok"]=safe(lambda: ind.effort_vs_result_ok(sub))
    # volume_signals verdict / stage / money-flow
    sig=safe(lambda: vs.compute(sub, "X.NS"))
    if sig is not None:
        d["verdict"]=getattr(sig,"verdict",None)
        d["timing"]=getattr(sig,"entry_timing",None)
        d["stage"]=getattr(sig,"weinstein_stage",None)
        d["cmf21"]=getattr(sig,"cmf_21d",None)
        d["ad30"]=getattr(sig,"ad_line_slope_pct",None)
    # BR trigger spike ratio from the stage trace
    br=stage_results.get("BR")
    if br is not None:
        d["br_volx"]=(br.features or {}).get("vol_ratio_today_50d")
        d["br_brk%"]=(br.features or {}).get("break_pct")
    return d

MIN_FWD=63
rows=[]
for path in sorted(glob.glob("test_data/18months/*.csv")):
    sym=sym_of(path); df=parse_nse(path); n=len(df)
    for t in range(200,n+1):
        sub=df.iloc[:t]
        sr=is_pick(sym,sub)
        if sr is None: continue
        i=t-1
        fwd=df.iloc[i+1:]
        if len(fwd)<MIN_FWD: continue
        fw=forward_walk(fwd,float(fwd["Open"].iloc[0]),hold_days=90)
        d=discriminators(sub,sr)
        d["sym"]=sym; d["date"]=str(df.index[i].date())
        d["ret"]=fw["return_pct"]; d["win"]=fw["return_pct"]>0; d["stop"]=fw["hit_stop_day"] is not None
        rows.append(d)

def g(x,f="{:>7.2f}"):
    return f.format(x) if isinstance(x,(int,float)) else f"{'-':>7}" if x is None else f"{str(x)[:7]:>7}"

cols=["obv90","obv180","sp_hl30","sp_hl10","inflect","volz","evr_ok","verdict","timing","stage","cmf21","ad30","br_volx"]
print(f"{'sym':<10}{'date':<12}{'ret':>7}  " + "".join(f"{c:>8}" for c in cols))
print("-"*160)
for r in sorted(rows,key=lambda x:(-x['win'],x['ret'])):
    print(f"{r['sym']:<10}{r['date']:<12}{r['ret']:>6.1f}%  " + "".join(f"{g(r.get(c)):>8}" for c in cols))

# Simple separation report: mean of each numeric discriminator, winners vs losers
print("\nSEPARATION (mean winners vs losers; want a col where the two differ a lot)")
wins=[r for r in rows if r['win']]; loss=[r for r in rows if not r['win']]
print(f"  n_win={len(wins)}  n_loss={len(loss)}")
import statistics as st
for c in ["obv90","obv180","sp_hl30","sp_hl10","volz","cmf21","ad30","br_volx"]:
    wv=[r[c] for r in wins if isinstance(r.get(c),(int,float))]
    lv=[r[c] for r in loss if isinstance(r.get(c),(int,float))]
    if wv and lv:
        print(f"  {c:<10} win={st.mean(wv):+8.2f}   loss={st.mean(lv):+8.2f}   delta={st.mean(wv)-st.mean(lv):+8.2f}")
for c in ["inflect","evr_ok","verdict","timing","stage"]:
    wv=[str(r.get(c)) for r in wins]; lv=[str(r.get(c)) for r in loss]
    from collections import Counter
    print(f"  {c:<10} win={dict(Counter(wv))}   loss={dict(Counter(lv))}")
