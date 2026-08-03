"""Pre-breakout TAG eligibility — the coherence guard on the pre-breakout label.

PRESENTATION / LABELING ONLY. Nothing here changes selection, scoring, sizing,
or exits — the composite/BR gates and the ranker still own which stocks are
picked (PRINCIPLES.md; feedback: additive-over-redesign). It answers ONE
question about an ALREADY-SELECTED pick payload:

    "May this pick be presented / tagged as PRE-BREAKOUT?"

A pick that fails either rule below simply loses the pre-breakout badge (it drops
out of any "Pre-Breakout Pick List" view); it is NOT removed from the picks and
its price/volume basis is unchanged. Pure function of the payload — no I/O,
never raises, deterministic. Compute it in the orchestrator's Phase-3 I/O layer
AFTER every advisory annotation (delivery divergence, flow interest) is attached,
so the self-veto sees the complete contradiction list.

The two rules were requested by the user (2026-08-03):

INSTRUCTION 1 — Self-Veto Override. If ANY of our own advisory modules flags the
pick as late / extended / distribution / exit-zone, it is disqualified from the
pre-breakout tag — our own bearish read wins over a tight-looking base. We only
READ flags computed elsewhere:
  - entry_stage in LATE_STAGES {POST_BREAKOUT_EXTENDED, LATE_CHASE,
      FAILED_BREAKOUT_RETEST} — the "extended / missed / failed" states of
      entry_stage_label.
  - accumulation_assessment.contradictions — distribution footprints from [DV]
      plus the OBV-vs-delivery "distribution-into-strength" flag.
  - accumulation_assessment.would_veto_shadow — a [DV] distribution footprint
      even in shadow mode. We do NOT wait for [DV] to be promoted to a blocking
      SELECTION gate before we stop calling the setup pre-breakout; the label is
      free to be stricter than the (reversible, config-gated) selection veto.
  - volume_event.direction == "bearish" (or a distribution/climax kind) — the
      single-session volume interpreter reading the trigger bar as distribution.

  "Stage 4" / long-term distribution needs no code here: [LTV] already HARD-gates
  OBV-90d slope < 0 upstream, so a Stage-4 name never reaches a pick.

INSTRUCTION 2 — Unanimous Accumulation. The volume timeframes must tell ONE
coherent accumulation story. We read 90d / 180d OBV, 90d up/down volume, and the
10d-vs-30d inflection. Disqualify on:
  - IMPOSSIBLE / MISSING METRIC (None/NaN) — cannot verify a timeframe, so cannot
    claim unanimity.
  - HEMORRHAGING (10d AND 30d OBV both negative) — the bad kind of disagreement:
    continued distribution, not a base turning up.
  - Any negative LONG-term read (90d/180d OBV < 0, or up/down-90d < 1.0), UNLESS
    the HEALING carve-out applies.

  THE HEALING CARVE-OUT (default ON — the one place we deliberately do NOT take
  Instruction 2 literally). A genuine EARLY base off a prior decline has a
  90d/180d window straddling the tail of that decline, so its long OBV is still
  negative while the last ~2 weeks (10d) have turned up ('healing'). That
  negative-long / positive-short disagreement is the EARLIEST, most valuable
  pre-breakout footprint — the thing the strategy exists to catch (mirrors
  lt_distribution_veto.py's pre-breakout carve-out and PRINCIPLES.md §2.5).
  Enforcing literal "every timeframe positive" would amputate exactly these. So a
  negative long-term read is EXEMPT when the recent tape is genuinely turning up.
  Set HEALING_CARVE_OUT_ENABLED = False for literal strict unanimity.

INSTRUCTION 3 — Stealth Demand (Stealth Accumulation Burst). Rules 1-2 prove the
sellers have LEFT; this one proves the buyers are actively HERE. Before tagging a
stock pre-breakout we require a right-edge Up/Down volume ratio at/above
STEALTH_DEMAND_MIN_RATIO over the final `window` bars (indicators.stealth_demand_
ratio). This resolves the ambiguity of a VPA volume dry-up: a quiet base with a
HIGH right-edge ratio is institutions absorbing supply ('stealth accumulation');
the SAME dry-up with a LOW ratio is apathy ('no one cares'), which must NOT wear
the pre-breakout badge. A missing/uncomputable ratio disqualifies (can't prove
demand). Reported via `stealth_demand` (the sub-metric) and `demand_conflicts`.

Fix points (all at the top of this file):
    HEALING_CARVE_OUT_ENABLED : master switch for the Instruction-2 carve-out.
    UP_DOWN_90D_MIN           : up/down-vol-90d floor for "positive net flow".
    STEALTH_DEMAND_MIN_RATIO  : right-edge up/down floor for Instruction 3.
    LATE_STAGES / PRE_BREAKOUT_STAGES : the entry_stage buckets.
"""

from __future__ import annotations

import math
from typing import Optional

from .entry_stage_label import (
    AT_PIVOT,
    AT_PIVOT_NO_DEMAND,
    BUILDING_BASE,
    COILED_PRE_BREAKOUT,
    DEEP_BASE,
    FAILED_BREAKOUT_RETEST,
    LATE_CHASE,
    POST_BREAKOUT_EXTENDED,
)

# --------------------------------------------------------------------------- #
# Fix points
# --------------------------------------------------------------------------- #

# entry_stage values that ARE a pre-breakout setup (the label can apply).
PRE_BREAKOUT_STAGES = frozenset({
    DEEP_BASE, BUILDING_BASE, COILED_PRE_BREAKOUT, AT_PIVOT, AT_PIVOT_NO_DEMAND,
})
# entry_stage values that are an explicit "too late / extended / failed" read —
# a hard self-veto (Instruction 1) even if another module said "early".
LATE_STAGES = frozenset({
    POST_BREAKOUT_EXTENDED, LATE_CHASE, FAILED_BREAKOUT_RETEST,
})

# Instruction-2 carve-out (see module docstring). True = allow a negative
# long-term read when the recent tape is healing; False = literal strict unanimity.
HEALING_CARVE_OUT_ENABLED: bool = True

# up/down-vol ratio over 90d at/above which net flow counts as positive.
UP_DOWN_90D_MIN: float = 1.0

# Instruction 3 — right-edge up/down volume ratio at/above which the base shows
# genuine active buying ("stealth demand"), not mere seller exhaustion. Up-day
# volume must dominate down-day volume by this factor over the final window.
STEALTH_DEMAND_MIN_RATIO: float = 1.5


def _is_num(x) -> bool:
    """True for a real, finite number (not None, not NaN)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def assess_pre_breakout_tag(
    payload: dict,
    *,
    healing_carveout: Optional[bool] = None,
) -> dict:
    """Pre-breakout tag eligibility for one ALREADY-SELECTED pick payload.

    Returns:
        {
          "is_pre_breakout_setup": bool,  # the setup COULD be tagged pre-breakout
          "eligible":             bool,   # setup AND passes both rules
          "self_veto":            [str],  # Instruction 1 — bearish flags that fired
          "flow_conflicts":       [str],  # Instruction 2 — timeframe/metric problems
          "demand_conflicts":     [str],  # Instruction 3 — stealth-demand problems
          "healing_exemption":    bool,   # Instruction 2 carve-out was applied
          "stealth_demand":       dict|None,  # Instruction 3 sub-metric (echoed)
          "stage":                str|None,   # entry_stage, echoed for the UI
          "reason":               str,    # human-readable summary
        }

    Never raises. When the pick is not a pre-breakout setup at all, `eligible` is
    False and both lists are empty — it is simply not a pre-breakout pick (e.g. a
    fresh BREAKOUT_CONFIRMED_TODAY), not a vetoed one.
    """
    if healing_carveout is None:
        healing_carveout = HEALING_CARVE_OUT_ENABLED
    payload = payload or {}

    entry_stage = payload.get("entry_stage")
    ea = payload.get("early_accumulation") or {}
    tier = ea.get("tier")

    is_setup = (entry_stage in PRE_BREAKOUT_STAGES) or (tier == "early")

    # ---- Instruction 1: self-veto -------------------------------------- #
    self_veto: list[str] = []
    if entry_stage in LATE_STAGES:
        self_veto.append(f"entry_stage={entry_stage} (extended/late/failed)")

    assess = payload.get("accumulation_assessment") or {}
    for c in (assess.get("contradictions") or []):
        self_veto.append(str(c))
    if assess.get("would_veto_shadow"):
        self_veto.append("distribution-veto (shadow) fired")

    ve = payload.get("volume_event") or {}
    ve_dir = str(ve.get("direction") or "").lower()
    ve_kind = str(ve.get("kind") or "").lower()
    if ve_dir == "bearish" or "distribution" in ve_kind or "climax" in ve_kind:
        self_veto.append(f"volume_event={ve.get('kind')} (bearish)")

    self_veto = list(dict.fromkeys(self_veto))  # dedupe, preserve order

    # ---- Instruction 2: multi-timeframe flow coherence ----------------- #
    flow_conflicts: list[str] = []
    healing_exemption = False

    if is_setup:
        ft = payload.get("flow_timeframes") or {}
        obv90 = ft.get("obv_90d_norm_slope_pct")
        obv180 = ft.get("obv_180d_norm_slope_pct")
        ud90 = ft.get("up_down_vol_ratio_90d")
        infl = ft.get("obv_flow_inflection")
        s_short = ft.get("obv_10d_norm_slope_pct")
        s_long = ft.get("obv_30d_norm_slope_pct")

        # (a) impossible / missing metric — cannot claim unanimity.
        for name, val in (
            ("OBV-90d", obv90), ("OBV-180d", obv180), ("up/down-90d", ud90),
        ):
            if not _is_num(val):
                flow_conflicts.append(f"impossible/missing metric: {name}")
        if infl not in ("healing", "hemorrhaging", "neutral"):
            flow_conflicts.append("impossible/missing metric: 10d/30d inflection")

        # Recent tape genuinely turning up = the healing turn.
        healing = _is_num(s_short) and s_short > 0
        hemorrhaging = (infl == "hemorrhaging") or (
            _is_num(s_short) and _is_num(s_long) and s_short < 0 and s_long < 0
        )

        # (b) hemorrhaging — both short and long negative = still distributing.
        if hemorrhaging:
            flow_conflicts.append("flow hemorrhaging (10d & 30d OBV both negative)")

        # (c) long-term negatives — allowed only under the healing carve-out.
        long_negs: list[str] = []
        if _is_num(obv90) and obv90 < 0:
            long_negs.append(f"OBV-90d {obv90:+.0f}%")
        if _is_num(obv180) and obv180 < 0:
            long_negs.append(f"OBV-180d {obv180:+.0f}%")
        if _is_num(ud90) and ud90 < UP_DOWN_90D_MIN:
            long_negs.append(f"up/down-90d {ud90:.2f}x")
        if long_negs:
            if healing_carveout and healing and not hemorrhaging:
                healing_exemption = True
            else:
                tail = (
                    ") with no healing turn (10d OBV not up)"
                    if not (healing and not hemorrhaging)
                    else ") [healing carve-out OFF]"
                )
                flow_conflicts.append("long-term flow negative (" + ", ".join(long_negs) + tail)

        flow_conflicts = list(dict.fromkeys(flow_conflicts))

    # ---- Instruction 3: stealth demand at the right edge --------------- #
    demand_conflicts: list[str] = []
    stealth_demand = payload.get("stealth_demand") if is_setup else None
    if is_setup:
        sd = stealth_demand or {}
        sd_ratio = sd.get("ratio")
        if not _is_num(sd_ratio):
            demand_conflicts.append("impossible/missing metric: stealth-demand ratio")
        elif sd_ratio < STEALTH_DEMAND_MIN_RATIO:
            ctx = " in dry-up — apathy, not absorption" if sd.get("in_dryup") else ""
            demand_conflicts.append(
                f"no stealth demand at right edge (up/down {sd_ratio:.2f}x < "
                f"{STEALTH_DEMAND_MIN_RATIO:.2f}x{ctx})"
            )

    eligible = bool(
        is_setup and not self_veto and not flow_conflicts and not demand_conflicts
    )

    # ---- Reason string ------------------------------------------------- #
    if not is_setup:
        reason = f"not a pre-breakout setup (entry_stage={entry_stage}, tier={tier})"
    elif eligible:
        sd_txt = ""
        if isinstance(stealth_demand, dict) and _is_num(stealth_demand.get("ratio")):
            sd_txt = f", stealth demand {stealth_demand['ratio']:.2f}x"
            if stealth_demand.get("in_dryup"):
                sd_txt += " in dry-up"
        reason = "pre-breakout confirmed — no self-veto, flow coherent" + sd_txt + (
            " (healing carve-out applied)" if healing_exemption else ""
        )
    else:
        bits: list[str] = []
        if self_veto:
            bits.append("self-veto[" + "; ".join(self_veto) + "]")
        if flow_conflicts:
            bits.append("flow-conflict[" + "; ".join(flow_conflicts) + "]")
        if demand_conflicts:
            bits.append("demand-conflict[" + "; ".join(demand_conflicts) + "]")
        reason = "disqualified from pre-breakout tag: " + "; ".join(bits)

    return {
        "is_pre_breakout_setup": is_setup,
        "eligible": eligible,
        "self_veto": self_veto,
        "flow_conflicts": flow_conflicts,
        "demand_conflicts": demand_conflicts,
        "healing_exemption": healing_exemption,
        "stealth_demand": stealth_demand,
        "stage": entry_stage,
        "reason": reason,
    }
