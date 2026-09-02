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
    MAX_PICK_ATR_PCT        : final volatility ceiling (default 3.0%)

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
from ..pipeline import COMPOSITE_TAU, COMPOSITE_WEIGHTS, PipelineResult
from ..universe import VOLUME_UNIVERSE_SET
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

# Drop entry_timing == "late" from the top-N. Flipped ON 2026-09-02 at the
# owner's request ("save me from distribution traps"). The prior comment called
# "late" purely "extended-but-not-distribution" — that was inaccurate: the
# "late" bucket in volume_signals._classify_entry_timing ALSO covers
#   (a) distribution-into-strength — Stage-2 price structure but OBV-90d falling
#       ("institutions are distributing into the rally, not accumulating"), and
#   (b) Stage-3 top forming ("distribution is starting to form").
# Excluding "late" therefore keeps BOTH already-run names AND those two
# distribution setups out of the buy list. Selection-only veto — it never widens
# picks; a resulting zero-pick day falls through to the calm accumulation lead /
# monitoring content exactly as before. Set back to False to restore pre-2026-09-02.
EXCLUDE_LATE_ENTRY: bool = True         # tunable

# Final quality boundary. [CS] remains a soft stage so near-misses stay visible,
# but the actual buy list should not contain a stock moving more than ~3% ATR
# per day. This is deliberately below CS's broad 5.5% analysis ceiling.
EXCLUDE_HIGH_VOLATILITY: bool = True    # tunable
MAX_PICK_ATR_PCT: float = 3.0           # tunable

# The composite is intentionally tolerant of one weak soft leg, but a volume-
# accumulation strategy must still show accumulation somewhere. Accept either
# direct [AC] confirmation or the durable-slow multi-horizon profile (which
# itself requires a quiet VD/AC footprint). This closes the path where unrelated
# breakout bonuses could carry a no-accumulation candidate into the top-N.
REQUIRE_ACCUMULATION_EVIDENCE: bool = True  # tunable

# Guaranteed daily pre-breakout LEAD (2026-08-13). When the strict guards leave
# zero confirmed picks, surface the single best PRE-BREAKOUT candidate from the
# hard-gate pool even when it is just below the confirmation threshold τ. Only τ
# is relaxed here: the volatility ceiling (MAX_PICK_ATR_PCT), accumulation
# evidence, day-0 exit-coherence and the missed-entry veto ALL still apply — so
# the lead is always a calm, coiling, accumulation-confirmed name that will not
# be a whipsaw/volatility problem the next day, never a distribution setup. It is
# badged `selection_tier="lead_watch"` so the UI shows it as watch-grade, not a
# confirmed buy. Set LEAD_FALLBACK_ENABLED=False to restore honest zero-pick days;
# raise LEAD_FALLBACK_MIN_COMPOSITE to refuse a lead that is too far under τ.
LEAD_FALLBACK_ENABLED: bool = True        # tunable
LEAD_FALLBACK_MIN_COMPOSITE: float = 0.0  # tunable — floor on S (0 = always surface best qualifying)


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

    # Defence in depth: the [U] gate and live orchestrator already use the
    # fixed volume universe, but the ranker is also called by backtests and
    # programmatic callers.  No caller may turn an out-of-scope result into a
    # volume-strategy pick by supplying a wider/custom candidate list.
    survivors, outside_volume_universe = _partition_volume_universe(survivors)
    for r in outside_volume_universe:
        r.selected = False
        r.rank = None
    if outside_volume_universe:
        log.warning(
            "rank: rejected %d non-volume-universe candidate(s): %s",
            len(outside_volume_universe),
            ", ".join(r.symbol for r in outside_volume_universe),
        )
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
        if isinstance(r.confirmation_components, dict):
            # Default tier for a name that cleared every strict guard. The
            # fallback below overrides the winner it promotes to "lead_watch".
            r.confirmation_components.setdefault("selection_tier", "confirmed")

    return selected


def rank_lead_fallback(
    hard_survivors: list[PipelineResult],
    *,
    min_composite: Optional[float] = None,
) -> Optional[PipelineResult]:
    """Best watch-grade pre-breakout lead from the hard-gate pool (τ relaxed).

    Runs the SAME confirmation ranking and selection vetoes as `rank_survivors`
    — volatility ceiling, accumulation evidence, day-0 exit-coherence and the
    missed-entry veto all still enforced — over the wider hard-gate pool, which
    (unlike the strict `survivors` list) includes below-τ near-misses. Returns
    the single top qualifying candidate, tagged `selection_tier="lead_watch"`,
    or None when nothing qualifies or its composite is under the floor.

    Call this only when `rank_survivors` returned no confirmed picks; it exists
    so a day is never silently empty when a genuine, calm, accumulating
    pre-breakout base is coiling just under the confirmation line.
    """
    if not LEAD_FALLBACK_ENABLED or not hard_survivors:
        return None
    floor = LEAD_FALLBACK_MIN_COMPOSITE if min_composite is None else min_composite

    ranked = rank_survivors(list(hard_survivors), top_n=1)
    if not ranked:
        return None
    lead = ranked[0]

    composite = float(lead.composite_score or 0.0)
    if composite < floor:
        # Even the best coherent, accumulation-confirmed candidate is too far
        # under τ — honest empty beats surfacing a weak name as "the lead".
        lead.selected = False
        lead.rank = None
        return None

    comps = lead.confirmation_components if isinstance(lead.confirmation_components, dict) else {}
    comps["selection_tier"] = "lead_watch"
    comps["lead_note"] = (
        f"Approaching confirmation — S={composite:.3f} vs τ={COMPOSITE_TAU:.2f}. "
        f"Accumulation-confirmed and inside the {MAX_PICK_ATR_PCT:.1f}% volatility "
        "ceiling; watch-grade lead, wait for the trigger / size cautiously."
    )
    lead.confirmation_components = comps
    return lead


def _partition_volume_universe(
    survivors: list[PipelineResult],
) -> tuple[list[PipelineResult], list[PipelineResult]]:
    """Split candidates at the volume strategy's Nifty Total Market boundary."""
    eligible: list[PipelineResult] = []
    excluded: list[PipelineResult] = []
    for result in survivors:
        (eligible if result.symbol in VOLUME_UNIVERSE_SET else excluded).append(result)
    return eligible, excluded


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

    stages = getattr(r, "stage_results", None) or {}
    cs = stages.get("CS")
    cs_features = getattr(cs, "features", None) or {}
    try:
        atr_pct = float(cs_features.get("atr_pct"))
    except (TypeError, ValueError):
        atr_pct = None
    if (
        EXCLUDE_HIGH_VOLATILITY
        and atr_pct is not None
        and atr_pct > MAX_PICK_ATR_PCT
    ):
        return f"high volatility: ATR/price {atr_pct:.2f}% > {MAX_PICK_ATR_PCT:.1f}%"

    # Fail open for legacy/minimal callers that did not run [AC]. The live and
    # backtest chains always include it, so their complete context is held to
    # the accumulation-evidence requirement.
    ac = stages.get("AC")
    early = comps.get("early_accumulation") or {}
    durable_slow = bool((early.get("features") or {}).get("durable_slow"))
    direct_ac = bool(ac is not None and getattr(ac, "passed", False))
    if REQUIRE_ACCUMULATION_EVIDENCE and ac is not None and not (direct_ac or durable_slow):
        return "no confirmed accumulation (AC failed and durable flow absent)"
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
