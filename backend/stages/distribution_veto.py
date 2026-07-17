"""[DV] Distribution Veto — pre-composite hygiene check.

Runs after [BR] so it sees the full tape. Fires when today's or the recent
15-session tape shows classical distribution footprints that invalidate an
otherwise-attractive setup. This is the "don't get trapped by institutional
tricks" layer — engineered spikes, gap-up bull traps, and stealth distribution
disguised as consolidation.

Three vetoes (deterministic, per-bar rules — no thresholds picked to fit any
one incident):

    weak_close_spike     :  today volume z ≥ 2.0 (MAD-normalized on 50 bars)
                            AND close in bottom third of the day's range
                            — "big volume, sellers won"
    gap_up_weak_close    :  today.Open ≥ 2% above yesterday.Close
                            AND close in bottom half of the day's range
                            — "gap-up sold into" (classic bull trap)
    dist_day_cluster     :  ≥3 sessions in last 15 with down-close AND
                            volume > ADV20
                            — repeated institutional exit

Two modes controlled by config/stage_weights.json → "distribution_veto_mode":

    "shadow" (DEFAULT): always passed=True; only writes `would_veto` to the
                        trace. Zero live impact. This is the anti-tightening
                        guardrail — the veto has to prove itself against
                        outcomes before it can block a pick.
    "block":            passed=False when any veto fires. The mode setter in
                        pipeline._load_weight_config auto-adds "DV" to
                        HARD_GATE_IDS so the pipeline short-circuits.

Fix points:
    Z_SPIKE_THRESHOLD        (default 2.0)   — MAD-normalized volume-z spike
    BOTTOM_THIRD_MAX_CLV     (default -0.33) — bottom-third close in signed CLV
    BOTTOM_HALF_MAX_CLV      (default  0.0)  — mid-range or below
    GAP_UP_MIN_PCT           (default 0.02)  — 2% overnight gap threshold
    DIST_CLUSTER_MIN_DAYS    (default 3)     — how many dist days in window
    DIST_CLUSTER_LOOKBACK    (default 15)    — trailing sessions inspected
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..indicators import (
    adv,
    close_location_value,
    volume_robust_zscore,
)
from ..pipeline import PipelineContext, StageResult

stage_id = "DV"

Z_SPIKE_THRESHOLD: float = 2.0
BOTTOM_THIRD_MAX_CLV: float = -0.33
BOTTOM_HALF_MAX_CLV: float = 0.0
GAP_UP_MIN_PCT: float = 0.02
DIST_CLUSTER_MIN_DAYS: int = 3
DIST_CLUSTER_LOOKBACK: int = 15
_ADV_WINDOW_FOR_CLUSTER: int = 20

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "stage_weights.json"


def _load_mode() -> str:
    """Read veto mode from config; default 'shadow' so unwired = harmless."""
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return str(raw.get("distribution_veto_mode", "shadow")).lower()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "shadow"


def _weak_close_spike(df: pd.DataFrame) -> bool:
    """Big-volume day with a close in the bottom third of the bar's range."""
    z = volume_robust_zscore(df["Volume"], n=50)
    if z is None or z < Z_SPIKE_THRESHOLD:
        return False
    last = df.iloc[-1]
    clv = close_location_value(
        float(last["Open"]),
        float(last["High"]),
        float(last["Low"]),
        float(last["Close"]),
    )
    return clv is not None and clv <= BOTTOM_THIRD_MAX_CLV


def _gap_up_weak_close(df: pd.DataFrame) -> bool:
    """Gap-up open followed by a mid-or-lower close — classic bull trap.

    Institutions filling short-term liquidity by baiting retail on the open,
    then selling into the follow-through. Any trip in this footprint should
    disqualify the setup regardless of how bullish the base looked yesterday.
    """
    if len(df) < 2:
        return False
    prev_close = float(df["Close"].iloc[-2])
    today_open = float(df["Open"].iloc[-1])
    if prev_close <= 0:
        return False
    if (today_open / prev_close - 1.0) < GAP_UP_MIN_PCT:
        return False
    last = df.iloc[-1]
    clv = close_location_value(
        float(last["Open"]),
        float(last["High"]),
        float(last["Low"]),
        float(last["Close"]),
    )
    return clv is not None and clv <= BOTTOM_HALF_MAX_CLV


def _dist_day_cluster(df: pd.DataFrame) -> tuple[bool, int]:
    """Count trailing sessions with (down-close) AND (volume > ADV20).

    ADV is computed on the prefix BEFORE the lookback window so a distribution
    day doesn't reduce its own baseline. Returns (is_veto, count).
    """
    needed = DIST_CLUSTER_LOOKBACK + _ADV_WINDOW_FOR_CLUSTER + 1
    if len(df) < needed:
        return False, 0
    prefix_end = -DIST_CLUSTER_LOOKBACK
    adv20 = adv(df["Volume"].iloc[:prefix_end], _ADV_WINDOW_FOR_CLUSTER)
    if adv20 is None or adv20 <= 0:
        return False, 0

    tail = df.iloc[-DIST_CLUSTER_LOOKBACK:]
    prev_close = df["Close"].shift(1)
    count = 0
    for ts, row in tail.iterrows():
        pc = prev_close.loc[ts] if ts in prev_close.index else None
        if pc is None or pd.isna(pc):
            continue
        if float(row["Close"]) < float(pc) and float(row["Volume"]) > adv20:
            count += 1
    return count >= DIST_CLUSTER_MIN_DAYS, count


def run(ctx: PipelineContext) -> StageResult:
    if ctx.ohlcv is None or ctx.ohlcv.empty:
        # No tape = nothing to veto on. Do NOT fail the ticker for missing
        # data — [I] Ingest already gates on that as a hard reject.
        return StageResult(
            stage_id=stage_id,
            passed=True,
            features={"skipped": True, "reason": "no ohlcv"},
            fix_point="backend/stages/distribution_veto.py",
        )

    df = ctx.ohlcv
    reasons: list[str] = []

    if _weak_close_spike(df):
        reasons.append("weak_close_spike")
    if _gap_up_weak_close(df):
        reasons.append("gap_up_weak_close")
    is_cluster, dist_count = _dist_day_cluster(df)
    if is_cluster:
        reasons.append(f"dist_day_cluster:{dist_count}")

    would_veto = len(reasons) > 0
    mode = _load_mode()

    features = {
        "mode": mode,
        "would_veto": would_veto,
        "veto_reasons": reasons,
        "dist_day_count_15": dist_count,
    }

    if mode == "block" and would_veto:
        return StageResult(
            stage_id=stage_id,
            passed=False,
            features=features,
            evidence=[f"distribution veto (block): {', '.join(reasons)}"],
            fix_point="backend/stages/distribution_veto.py:thresholds",
            reason=f"distribution footprint: {', '.join(reasons)}",
        )

    evidence = (
        [f"shadow-mode veto candidate: {', '.join(reasons)}"] if would_veto
        else ["no distribution footprint"]
    )
    return StageResult(
        stage_id=stage_id,
        passed=True,
        features=features,
        evidence=evidence,
        fix_point="backend/stages/distribution_veto.py:thresholds",
    )
