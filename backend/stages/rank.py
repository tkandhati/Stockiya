"""[RK] Confirmation-strength ranker.

Operates on all per-ticker PipelineResults that cleared every gate.
Computes a confirmation score for each survivor:

    confirmation = sum(gate_margins) + BONUS_WEIGHT * bonus_signal_count

    gate_margins   = CS.score + VD.score + BR.score   (each in [0, 1])
    bonus signals  (each +1):
      - 50d MA > 150d MA > 200d MA aligned
      - OBV-90d slope >= +5 %
      - Pocket-pivot fires today (up day, vol > prior-10 max down-day vol,
        AND VSA effort-vs-result: bar spread > trailing avg, upper-half close)
      - Top RS rank vs other survivors  (proxy; full-universe RS later)
      - Volume ignition / early-accumulation regime shift
      - Slow+durable accumulation (OBV positive over BOTH 90d and 180d, steady
        net buying, quiet dry-up/divergence footprint)   [early or mid tier]
      - Genuine early entry — not extended (near launch pad, 180d return < 30%,
        within +12% of the 50d MA, mature base)           [early tier only]
      - Stealth accumulation burst (right-edge up/down volume >= threshold while
        the base is in a volume dry-up — quiet absorption, not abandonment)

The pick with the highest confirmation score is rank #1. Top N selected
(default 3). Less-likely-false setups bubble to the top.

Fix points:
    BONUS_OBV_90D_MIN       : OBV-90d slope threshold (default 5.0)
    BONUS_RS_RANK_TOP_PCT   : top fraction of survivors for RS bonus
    BONUS_WEIGHT            : weight on bonus signals (default 0.5)
    TOP_N                   : how many picks per day (default 3)

Note: bulk/block deals were removed from confirmation scoring on 2026-07-27 —
they are optional/flaky data and now feed only the scoring-neutral presentation
layer (backend/flow_interest.py). Do NOT re-add a deal term to the score.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from ..early_accumulation import assess_early_accumulation
from ..indicators import (
    avwap_breakdown,
    distribution_day_count,
    effort_vs_result_ok,
    ma_stack_aligned,
    obv,
    obv_slope_pct,
    stealth_demand_ratio,
    volume_spike_event,
)
from ..pipeline import COMPOSITE_WEIGHTS, PipelineResult
# Reuse the EXACT exit-watch thresholds so a stock that WOULD exit-flip on day 0
# is kept out of the picks — entry and exit stay in lockstep by construction.
from ..signal_trajectory import (
    AVWAP_ANCHOR_LOOKBACK,
    AVWAP_CONFIRM_CLOSES,
    DIST_DAY_EXIT_COUNT,
)
from ..volume_signals import compute as compute_volume_signals

log = logging.getLogger("rank")


# --------------------------------------------------------------------------- #
# Tunable constants
# --------------------------------------------------------------------------- #

BONUS_OBV_90D_MIN: float = 5.0          # tunable
BONUS_RS_RANK_TOP_PCT: float = 0.30     # tunable
BONUS_WEIGHT: float = 0.5               # tunable
TOP_N: int = 3                           # tunable
STEALTH_DEMAND_BONUS_MIN: float = 1.5   # tunable — right-edge up/down floor (in dry-up)

# Self-Veto Override (2026-08-03): keep setups our own volume-signature lens
# classifies as entry_timing == "missed" (Stage 4 / distribution — "exit zone,
# not entry", backend/volume_signals.py) OUT of the top-N picks. They may clear
# the composite, but we refuse to present distribution as a buy. Reversible.
EXCLUDE_MISSED_ENTRY: bool = True       # tunable

# Day-0 coherence gate (2026-08-03): keep setups that would IMMEDIATELY trip the
# exit monitor out of the picks, so the picks page and the positions page tell
# the same story on day 0 (no "BUY here / EXIT here" on the same stock). Uses the
# SAME distribution-day + AVWAP-breakdown rules and thresholds as the exit layer
# (imported from signal_trajectory). Reversible.
EXCLUDE_DAY0_EXIT_WATCH: bool = True    # tunable

# Also drop entry_timing == "late" (price already run) from the top-N. Off by
# default — "late" is extended-but-not-distribution, so excluding it is a
# stricter policy the user can opt into; the reported bug does not require it.
EXCLUDE_LATE_ENTRY: bool = False        # tunable


# --------------------------------------------------------------------------- #
# Bonus-signal helpers
# --------------------------------------------------------------------------- #

def _check_pocket_pivot_today(df: Optional[pd.DataFrame]) -> bool:
    """Today is an up day AND today's volume > max(down-day volumes in prior 10)
    AND the bar passes the VSA effort-vs-result check (wider-than-average spread,
    upper-half close) so a big-volume / no-movement micro-trap doesn't qualify."""
    if df is None or len(df) < 12:
        return False
    closes = df["Close"]
    vols = df["Volume"]
    if closes.iloc[-1] <= closes.iloc[-2]:
        return False
    prev = df.iloc[-11:-1]
    prev_deltas = prev["Close"].diff().fillna(0)
    prev_down_vols = prev["Volume"][prev_deltas < 0]
    if prev_down_vols.empty:
        return False
    if float(vols.iloc[-1]) <= float(prev_down_vols.max()):
        return False
    return effort_vs_result_ok(df)


def _ret_90d(df: Optional[pd.DataFrame]) -> Optional[float]:
    """90-bar return as a fraction."""
    if df is None or len(df) < 91:
        return None
    p0 = float(df["Close"].iloc[-91])
    p1 = float(df["Close"].iloc[-1])
    if p0 <= 0:
        return None
    return (p1 / p0) - 1


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def rank_survivors(
    survivors: list[PipelineResult],
    *,
    top_n: int = TOP_N,
) -> list[PipelineResult]:
    """Compute confirmation scores and select the top N.

    Mutates each survivor in place: sets `confirmation_score`,
    `confirmation_components`, `selected`, `rank`.

    Returns the selected list, sorted #1 -> #N.
    """
    if not survivors:
        return []

    # ---- Pre-compute RS rank (90-day return within the survivor set) ----
    rets: list[Optional[float]] = []
    for r in survivors:
        rets.append(_ret_90d(r.ohlcv))

    indexed = [(i, v) for i, v in enumerate(rets) if v is not None]
    indexed.sort(key=lambda x: x[1], reverse=True)
    cutoff = max(1, int(len(indexed) * BONUS_RS_RANK_TOP_PCT))
    top_rs_indices = {i for i, _ in indexed[:cutoff]}

    # ---- Per-survivor confirmation ----
    for idx, r in enumerate(survivors):
        stages = r.stage_results
        # Weighted sum of ALL soft-gate margins (composite S) — matches
        # pipeline.compute_composite so ranker and gate use the same detector.
        # Read weights from the live config so the tuner's monthly ratchet
        # takes effect on the next run without touching this file.
        margin = 0.0
        for gid, w in COMPOSITE_WEIGHTS.items():
            if w == 0.0:
                continue
            sr = stages.get(gid)
            if sr is None or not sr.passed:
                continue
            margin += w * float(sr.score or 0.0)

        bonuses_fired: list[str] = []
        df = r.ohlcv

        # 1. MA stack
        if df is not None and ma_stack_aligned(df["Close"]):
            bonuses_fired.append("MA stack 50>150>200")

        # 2. OBV-90d slope
        if df is not None:
            obv_series = obv(df["Close"], df["Volume"])
            slope = obv_slope_pct(obv_series, 90)
            if slope is not None and slope >= BONUS_OBV_90D_MIN:
                bonuses_fired.append(f"OBV-90d {slope:+.1f}% >= {BONUS_OBV_90D_MIN}%")

        # 3. Pocket-pivot today (effort-vs-result confirmed)
        if _check_pocket_pivot_today(df):
            bonuses_fired.append("Pocket-pivot today (effort-vs-result)")

        # 4. Top RS rank (proxy: vs other survivors)
        if idx in top_rs_indices:
            bonuses_fired.append("Top RS-rank vs survivors")

        # 5. Contextual volume ignition / early accumulation.
        # Survivors already cleared the breakout gate; this bonus separates
        # ordinary breakouts from the sudden volume-regime shifts the user
        # wants surfaced earlier and more visibly.
        if df is not None:
            event = volume_spike_event(df)
            if event.kind in ("bullish_ignition", "early_accumulation"):
                bonuses_fired.append(f"{event.label} ({event.vol_ratio_50:.2f}x ADV50)")

        # 6. Early + slowly-accumulating preference (2026-07-28).
        # The profile the user wants to own: still near the launch pad AND
        # quietly, durably accumulating (OBV positive over BOTH 90d and 180d,
        # steady net buying, dry-up/divergence footprint — a slow build, not a
        # fast blow-off). Two additive bonuses so a genuine early accumulation
        # floats above an already-extended or spike-driven breakout. Nothing is
        # excluded; this only reorders. See backend/early_accumulation.py.
        early_accum = assess_early_accumulation(df, stages)
        if early_accum["tier"] in ("early", "mid"):
            bonuses_fired.append("Slow+durable accumulation (OBV 90d & 180d positive)")
        if early_accum["tier"] == "early":
            bonuses_fired.append("Genuine early entry — not extended")

        # 7. Stealth accumulation burst (2026-08-03). Complements #6 (which reads
        # 90d/180d flow) by reading the LAST ~10 bars' up/down volume: fires only
        # when the right edge is BOTH quiet (dry-up) AND demand-dominated — the
        # "supply exhausted, institutions quietly absorbing" footprint, as opposed
        # to a base that is merely being abandoned. Pure price/volume, so it
        # belongs in the ranker (not the scoring-neutral deals/delivery layer). It
        # only reorders — nothing is excluded. See indicators.stealth_demand_ratio.
        if df is not None:
            sd = stealth_demand_ratio(df["Close"], df["Volume"])
            if (
                sd is not None
                and sd.get("in_dryup")
                and sd.get("ratio") is not None
                and sd["ratio"] >= STEALTH_DEMAND_BONUS_MIN
            ):
                bonuses_fired.append(
                    f"Stealth accumulation burst (right-edge up/down "
                    f"{sd['ratio']:.1f}x in dry-up)"
                )

        bonus_count = len(bonuses_fired)
        confirmation = margin + BONUS_WEIGHT * bonus_count

        # Self-Veto Override input: our own Weinstein/Wyckoff volume lens rates
        # the entry timing. "missed" == Stage 4 / distribution ("exit zone, not
        # entry"). Recorded here (once) so selection can drop it and the trace /
        # card can show why. Fail-open to "unknown" — never over-exclude on an
        # analysis error or short history.
        entry_timing = "unknown"
        weinstein_stage = ""
        try:
            _vs = compute_volume_signals(df, r.symbol)
            entry_timing = getattr(_vs, "entry_timing", "unknown") or "unknown"
            weinstein_stage = getattr(_vs, "weinstein_stage", "") or ""
        except Exception:
            log.exception("rank: volume-signature timing failed for %s", r.symbol)

        # Day-0 coherence: would this setup IMMEDIATELY trip the exit monitor?
        # Same rules/thresholds the positions page uses (distribution-day cluster,
        # AVWAP breakdown). If so, we must not present it as a buy today.
        day0_exit_watch: Optional[str] = None
        if df is not None:
            try:
                dd = distribution_day_count(df["Close"], df["Volume"], lookback=15)
                if dd >= DIST_DAY_EXIT_COUNT:
                    day0_exit_watch = f"{dd} distribution days (>= {DIST_DAY_EXIT_COUNT})"
                else:
                    avb = avwap_breakdown(
                        df, anchor_lookback=AVWAP_ANCHOR_LOOKBACK,
                        confirm=AVWAP_CONFIRM_CLOSES,
                    )
                    if avb and avb.get("broke"):
                        day0_exit_watch = "AVWAP breakdown from base low"
            except Exception:
                log.exception("rank: day-0 exit-watch failed for %s", r.symbol)

        r.confirmation_score = round(confirmation, 4)
        r.confirmation_components = {
            "gate_margin_sum": round(margin, 4),
            "bonus_count": bonus_count,
            "bonus_weight": BONUS_WEIGHT,
            "bonuses_fired": bonuses_fired,
            "early_accumulation": early_accum,
            "entry_timing": entry_timing,
            "weinstein_stage": weinstein_stage,
            "day0_exit_watch": day0_exit_watch,
        }

    # ---- Sort + select ----
    survivors.sort(key=lambda x: x.confirmation_score, reverse=True)

    # Keep unsuitable-to-BUY setups out of the top-N: Stage-4/distribution
    # ("missed"), optionally already-run ("late"), and anything that would trip
    # the exit monitor on day 0. If this leaves fewer than top_n we present FEWER
    # (honest) rather than backfill with a name we'd immediately sell.
    actionable, excluded = _partition_selectable(survivors)
    if excluded:
        log.info(
            "rank: excluded %d setup(s) from top-%d: %s", len(excluded), top_n,
            "; ".join(f"{r.symbol} [{reason}]" for r, reason in excluded),
        )

    selected = actionable[:top_n]
    for rank, r in enumerate(selected, start=1):
        r.selected = True
        r.rank = rank

    return selected


def _selection_veto_reason(r: PipelineResult) -> Optional[str]:
    """Why this survivor must NOT be surfaced as a buy today, or None if it may.

    Pure — reads only confirmation_components (entry_timing / day0_exit_watch),
    so it is unit-testable without OHLCV and fail-open on missing data. Honors
    the EXCLUDE_* switches.
    """
    comps = getattr(r, "confirmation_components", None) or {}
    et = comps.get("entry_timing")
    if EXCLUDE_MISSED_ENTRY and et == "missed":
        return "missed (Stage 4 / distribution)"
    if EXCLUDE_LATE_ENTRY and et == "late":
        return "late (price already run)"
    if EXCLUDE_DAY0_EXIT_WATCH and comps.get("day0_exit_watch"):
        return f"day-0 exit-watch: {comps['day0_exit_watch']}"
    return None


def _partition_selectable(
    survivors: list[PipelineResult],
) -> tuple[list[PipelineResult], list[tuple[PipelineResult, str]]]:
    """Split survivors into (actionable, excluded[(result, reason)]) for top-N
    selection. Pure; see _selection_veto_reason for the rules."""
    actionable: list[PipelineResult] = []
    excluded: list[tuple[PipelineResult, str]] = []
    for r in survivors:
        reason = _selection_veto_reason(r)
        if reason:
            excluded.append((r, reason))
        else:
            actionable.append(r)
    return actionable, excluded
