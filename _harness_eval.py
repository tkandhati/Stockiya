"""One-off evaluation harness (not part of the pipeline).

Feeds the NSE Quote-Equity CSVs in test_data/ directly into the REAL stage
functions, bypassing [U] Universe and [I] Ingest (which need fetch/universe
membership). Reports per-stage pass/fail/score, the composite S vs tau, the
hard-gate verdict, and a set of sub-200-bar structural diagnostics so we can
characterize each name even when a gate is starved of history.

Pure/offline: reads only the pasted CSVs. No network.
"""
from __future__ import annotations

import glob
import os
import re
import pandas as pd

from backend.pipeline import (
    PipelineContext, compute_composite, hard_gates_passed,
    classify_trigger, COMPOSITE_TAU, COMPOSITE_WEIGHTS, _reweight_for_trigger,
)
from backend.stages import (
    hard_rejects, accum_screen, accumulation, lt_distribution_veto,
    lt_flow, consolidation, volume as volume_stage, breakout,
)
from backend import indicators as ind

SOFT_ORDER = ["ACS", "AC", "LT", "CS", "VD", "BR"]
STAGE_FNS = [
    ("HR", hard_rejects.run),
    ("ACS", accum_screen.run),
    ("AC", accumulation.run),
    ("LTV", lt_distribution_veto.run),
    ("LT", lt_flow.run),
    ("CS", consolidation.run),
    ("VD", volume_stage.run),
    ("BR", breakout.run),
]


def parse_nse(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    raw = raw[raw["SERIES"].str.strip() == "EQ"]

    def num(s):
        return pd.to_numeric(s.str.replace(",", "", regex=False).str.strip(),
                             errors="coerce")

    df = pd.DataFrame({
        "Open": num(raw["OPEN"]),
        "High": num(raw["HIGH"]),
        "Low": num(raw["LOW"]),
        "Close": num(raw["CLOSE"]),
        "Volume": num(raw["VOLUME"]),
    })
    df.index = pd.to_datetime(raw["DATE"].str.strip(), format="%d-%b-%Y")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Volume"].fillna(0) > 0]
    df = df.sort_index()
    df["Volume"] = df["Volume"].astype("int64")
    return df


def symbol_of(path: str) -> str:
    m = re.search(r"Quote-Equity-([A-Z0-9&-]+)-EQ-", os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)


def diagnostics(df: pd.DataFrame) -> dict:
    close = df["Close"]; vol = df["Volume"]
    n = len(df)
    last = float(close.iloc[-1])
    d = {"bars": n, "last": round(last, 2)}
    # 30d return (needs 31 bars)
    if n >= 31:
        p0 = float(close.iloc[-31])
        d["ret_30d_pct"] = round((last / p0 - 1) * 100, 1)
    # extension vs 50d MA
    ma50 = ind.sma(close, 50)
    if ma50:
        d["ext_vs_ma50_pct"] = round((last / ma50 - 1) * 100, 1)
    # full-window return (base to now)
    p_first = float(close.iloc[0])
    d["ret_window_pct"] = round((last / p_first - 1) * 100, 1)
    # ATR%
    ap = ind.atr_pct(df, 14)
    d["atr_pct"] = round(ap, 2) if ap is not None else None
    # 20d breakout posture (today vs prior 20d high)
    res20 = ind.rolling_high(df["High"], 20, exclude_today=True)
    if res20:
        d["vs_20d_high_pct"] = round((last / res20 - 1) * 100, 2)
    # today's volume vs adv50
    adv50 = ind.adv(vol, 50)
    if adv50:
        d["vol_today_x_adv50"] = round(float(vol.iloc[-1]) / adv50, 2)
    # days within +/-12% band (consolidation duration proxy)
    d["days_in_12pct_band"] = ind.days_within_band(close, 0.12)
    # OBV slope over the AVAILABLE window (zero-cross safe), and up/down vol
    obv = ind.obv(close, vol)
    win = min(90, n - 1)
    d["obv_slope_avail_pct"] = (
        round(ind.obv_norm_slope_pct(obv, win), 1) if win >= 3 else None
    )
    d["obv_window_bars"] = win
    ud = ind.up_down_vol_ratio(close, vol, win)
    d["updown_vol_avail"] = round(ud, 2) if ud is not None else None
    # recent tightness: adaptive-window range% (best of triplet) if enough bars
    d["range_20d_pct"] = (
        round(ind.range_pct_window(df, 20) * 100, 1) if n >= 20 else None
    )
    return d


def run_one(path: str):
    sym = symbol_of(path)
    df = parse_nse(path)
    ctx = PipelineContext(symbol=sym, trace_id="eval", today_iso="")
    ctx.ohlcv = df
    results = {}
    for sid, fn in STAGE_FNS:
        try:
            r = fn(ctx)
        except Exception as e:  # pragma: no cover
            from backend.pipeline import StageResult
            r = StageResult(stage_id=sid, passed=False, reason=f"crash: {e}")
        ctx.stage_results[r.stage_id] = r
        results[r.stage_id] = r
    S = compute_composite(ctx.stage_results)
    regime = classify_trigger(ctx.stage_results)
    hard_ok = hard_gates_passed(ctx.stage_results)
    survivor = hard_ok and S >= COMPOSITE_TAU
    return sym, df, results, S, regime, hard_ok, survivor


def fmt_stage(sid, r):
    mark = "PASS" if r.passed else "fail"
    sc = f"{r.score:.3f}" if r.passed and r.score else "  -  "
    reason = (r.reason or "")[:78]
    return f"    [{sid:<3}] {mark}  score={sc}   {reason}"


print("=" * 92)
print(f"tau (composite threshold) = {COMPOSITE_TAU}   weights = {COMPOSITE_WEIGHTS}")
print("Hard gates that run here (no fetch): HR, LTV.  (U/I skipped — data supplied directly.)")
print("=" * 92)

rows = []
for path in sorted(glob.glob("test_data/Quote-Equity-*.csv")):
    sym, df, results, S, regime, hard_ok, survivor = run_one(path)
    diag = diagnostics(df)
    print(f"\n### {sym}   ({diag['bars']} bars, {df.index[0].date()} -> {df.index[-1].date()})")
    print(f"    last=Rs{diag['last']}  window_return={diag.get('ret_window_pct')}%  "
          f"30d={diag.get('ret_30d_pct')}%  ext_vs_50dMA={diag.get('ext_vs_ma50_pct')}%  "
          f"ATR={diag.get('atr_pct')}%")
    print(f"    vs_20d_high={diag.get('vs_20d_high_pct')}%  today_vol={diag.get('vol_today_x_adv50')}x adv50  "
          f"days_in_12%band={diag.get('days_in_12pct_band')}  range20d={diag.get('range_20d_pct')}%")
    print(f"    OBV_slope({diag['obv_window_bars']}b)={diag.get('obv_slope_avail_pct')}%  "
          f"up/down_vol={diag.get('updown_vol_avail')}")
    for sid, _ in STAGE_FNS:
        print(fmt_stage(sid, results[sid]))
    print(f"    --> composite S = {S:.3f}   regime={regime}   hard_gates={'OK' if hard_ok else 'FAIL'}   "
          f"SURVIVOR(S>=tau & hard)={'YES' if survivor else 'no'}")
    rows.append((sym, diag, S, hard_ok, survivor))

print("\n" + "=" * 92)
print("SUMMARY")
print(f"{'Symbol':<12}{'bars':>5}{'win%':>7}{'30d%':>7}{'ext50%':>8}{'ATR%':>6}{'S':>7}  hard  survivor")
for sym, diag, S, hard_ok, survivor in rows:
    print(f"{sym:<12}{diag['bars']:>5}{diag.get('ret_window_pct',0):>7}"
          f"{diag.get('ret_30d_pct',0):>7}{diag.get('ext_vs_ma50_pct',0):>8}"
          f"{diag.get('atr_pct',0):>6}{S:>7.3f}  {'OK ' if hard_ok else 'FAIL'}   "
          f"{'YES' if survivor else 'no'}")
