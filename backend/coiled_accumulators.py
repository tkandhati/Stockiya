"""Coiled Accumulators — the "loaded spring" watch cohort.

PRESENTATION / MONITORING ONLY. Nothing here changes selection, scoring, sizing,
or exits — the composite/BR gates and the ranker still own which stocks are
picked (PRINCIPLES.md; feedback: additive-over-redesign). It answers ONE
question about ALREADY-BUILT pick payloads:

    "Has this base absorbed volume for a while, is it STILL absorbing, and has it
     not broken out yet?" — i.e. a coiling spring that hasn't fired.

This is the section the user asked for (2026-08-24): stocks where the volume
accumulation "passed ~15 trading days ago", volume is STILL being accumulated
within the range, but price has not spiked — it is still sideways. The value is
the DISCRIMINATOR the user intuited: a flat, recurring, not-yet-broken-out base
is a *dead* base (churn) when flow is flat/falling, but a *loaded spring* when
flow is still rising. The existing entry-readiness router lumps these together
(`_stale_base_reason` bins the flat/recurring ones as "goes nowhere"; the
`entry_timing` classifier can even tag a strong coil "late"). This cohort is the
positive counterpart: it re-surfaces the ones that are STILL accumulating.

WHY THIS IS NOT REDUNDANT with the pre-breakout badge (pre_breakout_tag.py):
  - pre-breakout is a per-day BADGE on one selected card; this is a dedicated,
    orderable SECTION you can monitor.
  - this adds the TIME / persistence axis the user emphasised — `coil_age_days`
    (how long it has coiled) and `prior` (how many of OUR runs have flagged it).
  - it spans BOTH the main buy list AND the `not_actionable` awareness bin, so a
    strong coil that `entry_timing` routed to "late" is still surfaced here.
  - being a persisted cohort with age + history, "coiled -> breakout" conversion
    can be measured offline over time (observation-first; owner choice 2026-08-24).

Qualification (ALL must hold), read only from persisted payload fields:
  1. Base/coil stage      — entry_stage in PRE_BREAKOUT_STAGES (reused, same set
                            the pre-breakout tag uses; not extended/late/failed).
  2. Not broken out yet   — "BR" in gate_confirmation_status.failed (still sideways).
  3. Coiled long enough   — volume_event.base_days >= COIL_MIN_BASE_DAYS.
  4. Accumulation BANKED  — obv_90d_norm_slope_pct > COIL_OBV90_MIN_PCT AND
                            (obv_180d > 0 OR up/down-90d >= COIL_UD90_MIN)
                            ("the accumulation passed 15 days ago" part).
  5. STILL absorbing NOW  — stealth_demand.ratio >= COIL_STEALTH_MIN_RATIO, the
                            right-edge up/down volume ("still getting accumulated"
                            part). THIS is the discriminator vs a dead base.
  6. Not distributing     — no distribution self-veto (mirrors the pre-breakout
                            self-veto: contradictions / shadow-veto / bearish
                            volume event).

Pure functions of the payload — no I/O, never raise, deterministic, so the same
logic runs live and in an offline replay, and the tuner can import it. Reversible
via env STOCKYA_COILED_WATCH=0 (then the section is empty / absent).

Fix points (top of file):
    COIL_MIN_BASE_DAYS       : how many sessions the base must have coiled (~15).
    COIL_OBV90_MIN_PCT       : 90d OBV-norm-slope floor for "accumulation banked".
    COIL_UD90_MIN            : up/down-vol-90d floor (durable net buying).
    COIL_STEALTH_MIN_RATIO   : right-edge up/down floor for "still absorbing".
"""
from __future__ import annotations

import math
import os
from typing import Optional

from .pre_breakout_tag import PRE_BREAKOUT_STAGES

# --------------------------------------------------------------------------- #
# Fix points
# --------------------------------------------------------------------------- #

# Minimum coiled-base length (sessions) before a name is watch-worthy. The user's
# "the accumulation passed ~15 trading days ago" maps directly here.
COIL_MIN_BASE_DAYS: int = 15

# 90d OBV normalised slope must be above this for "accumulation already banked".
COIL_OBV90_MIN_PCT: float = 0.0

# up/down-vol-90d floor: durable net buying across the quarter (secondary
# confirmation alongside a positive 90d/180d OBV).
COIL_UD90_MIN: float = 1.0

# Right-edge up/down volume ratio at/above which the base is STILL absorbing now
# (the discriminator vs a dead base). Deliberately looser than the pre-breakout
# tag's 1.5 — this is a WATCH net, not the buy-badge, so it may cast wider.
COIL_STEALTH_MIN_RATIO: float = 1.3


def _is_num(x) -> bool:
    """True for a real, finite number (not None, not NaN, not bool)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _enabled() -> bool:
    """On by default; STOCKYA_COILED_WATCH=0 disables the section entirely."""
    return os.environ.get("STOCKYA_COILED_WATCH", "1") != "0"


def _distribution_veto(payload: dict) -> list[str]:
    """Distribution flags that disqualify a coil from the watch cohort.

    Mirrors the pre-breakout tag's Instruction-1 self-veto: we only READ flags
    computed elsewhere (advisory contradictions, the shadow distribution veto,
    and a bearish/distribution/climax volume event). A coil that is actually
    being distributed into is not a loaded spring.
    """
    blocks: list[str] = []
    assess = payload.get("accumulation_assessment") or {}
    for c in (assess.get("contradictions") or []):
        blocks.append(str(c))
    if assess.get("would_veto_shadow"):
        blocks.append("distribution-veto (shadow) fired")
    ve = payload.get("volume_event") or {}
    ve_dir = str(ve.get("direction") or "").lower()
    ve_kind = str(ve.get("kind") or "").lower()
    if ve_dir == "bearish" or "distribution" in ve_kind or "climax" in ve_kind:
        blocks.append(f"volume_event={ve.get('kind')} (bearish)")
    return list(dict.fromkeys(blocks))  # dedupe, preserve order


def assess_coiled_accumulator(payload: dict) -> dict:
    """Coiled-accumulator assessment for ONE already-built pick payload.

    Returns a dict ALWAYS (never None, never raises) so it is fully testable:
        {
          "qualifies":          bool,       # a loaded spring right now
          "coil_age_days":      int|None,   # sessions coiling (volume_event.base_days)
          "not_broken_out":     bool,       # BR gate still failing
          "flow":               dict,       # the flow evidence (echoed)
          "flow_strengthening": bool,       # 10d OBV up / healing (echo, not gating)
          "stage":              str|None,   # entry_stage, echoed
          "support_point":      float|None, # coil floor to enter on/before (base low)
          "support_basis":      str|None,   # "25-bar base low" | "protective stop"
          "entry_reference":    float|None, # top of the buy zone (don't chase above)
          "volume_gate":        str,        # the still-absorbing condition that keeps it live
          "blocks":             [str],      # why it does NOT qualify (empty = clean)
          "reason":             str,        # human-readable summary
        }
    """
    payload = payload or {}
    blocks: list[str] = []

    entry_stage = payload.get("entry_stage")
    ft = payload.get("flow_timeframes") or {}
    sd = payload.get("stealth_demand") or {}
    ve = payload.get("volume_event") or {}
    esf = payload.get("entry_stage_features") or {}
    plan = payload.get("price_plan") or {}

    obv90 = ft.get("obv_90d_norm_slope_pct")
    obv180 = ft.get("obv_180d_norm_slope_pct")
    ud90 = ft.get("up_down_vol_ratio_90d")
    s_short = ft.get("obv_10d_norm_slope_pct")
    infl = ft.get("obv_flow_inflection")
    sd_ratio = sd.get("ratio")
    base_days = ve.get("base_days")

    # 1. Must be in a base / coil stage (not extended / late / failed).
    if entry_stage not in PRE_BREAKOUT_STAGES:
        blocks.append(f"not a base/coil stage (entry_stage={entry_stage})")

    # 2. Must NOT have broken out yet — still sideways.
    gate = payload.get("gate_confirmation_status") or {}
    not_broken_out = "BR" in (gate.get("failed") or [])
    if not not_broken_out:
        blocks.append("already broke out (BR gate passed) — no longer coiling")

    # 3. Must have coiled long enough.
    if not _is_num(base_days):
        blocks.append("coil age unknown (volume_event.base_days missing)")
    elif base_days < COIL_MIN_BASE_DAYS:
        blocks.append(f"base only {int(base_days)}d < {COIL_MIN_BASE_DAYS}d minimum")

    # 4. Accumulation already banked over the quarter/half-year.
    if not (_is_num(obv90) and obv90 > COIL_OBV90_MIN_PCT):
        blocks.append("90d OBV not positive (no accumulation banked)")
    else:
        durable = (_is_num(obv180) and obv180 > 0) or (_is_num(ud90) and ud90 >= COIL_UD90_MIN)
        if not durable:
            blocks.append("accumulation not durable (180d OBV & up/down-90d both weak)")

    # 5. STILL absorbing at the right edge — the discriminator vs a dead base.
    if not _is_num(sd_ratio):
        blocks.append("right-edge demand unknown (stealth-demand ratio missing)")
    elif sd_ratio < COIL_STEALTH_MIN_RATIO:
        ctx = " (dry-up = apathy, not absorption)" if sd.get("in_dryup") else ""
        blocks.append(
            f"not absorbing now (right-edge up/down {sd_ratio:.2f}x < "
            f"{COIL_STEALTH_MIN_RATIO:.2f}x){ctx}"
        )

    # 6. Not distributing.
    blocks.extend(_distribution_veto(payload))

    blocks = list(dict.fromkeys(blocks))
    qualifies = not blocks

    flow_strengthening = (infl == "healing") or (_is_num(s_short) and s_short > 0)

    flow = {
        "obv_90d_norm_slope_pct": obv90,
        "obv_180d_norm_slope_pct": obv180,
        "up_down_vol_ratio_90d": ud90,
        "obv_10d_norm_slope_pct": s_short,
        "inflection": infl,
        "stealth_ratio": sd_ratio,
        "in_dryup": bool(sd.get("in_dryup")),
    }

    # Support point to "enter on or before" (owner ask, 2026-08-24). This is the
    # coil FLOOR the pullback should hold — the 25-bar base low persisted in
    # entry_stage_features. Fall back to the pick's protective stop when the base
    # low is missing (a stop sits just under the same support). entry_reference is
    # the top of the buy zone: don't chase a still-coiling name above its planned
    # entry. Presentation-only — no level here changes selection or sizing.
    base_low = esf.get("base_low_25")
    stop = plan.get("stop")
    if _is_num(base_low):
        support_point = round(float(base_low), 2)
        support_basis = "25-bar base low"
    elif _is_num(stop):
        support_point = round(float(stop), 2)
        support_basis = "protective stop"
    else:
        support_point = None
        support_basis = None
    entry_ref = plan.get("entry")
    if not _is_num(entry_ref):
        entry_ref = payload.get("current_price")
    entry_reference = round(float(entry_ref), 2) if _is_num(entry_ref) else None

    # The support is only LIVE while volume keeps confirming — the whole point of
    # this cohort. Say so explicitly so the level is never read in isolation.
    volume_gate = (
        f"Support holds only while the right-edge up/down volume stays "
        f">= {COIL_STEALTH_MIN_RATIO:.2f}x (still absorbing). If volume rolls "
        f"over, the coil is voided — do not enter."
    )

    if qualifies:
        age = int(base_days) if _is_num(base_days) else None
        reason = (
            f"coiling {age}d, no breakout yet; accumulation banked "
            f"(OBV-90d {obv90:+.0f}%"
            + (f", up/down {ud90:.2f}x" if _is_num(ud90) else "")
            + ") and still absorbing "
            f"(right-edge up/down {sd_ratio:.2f}x"
            + (" in dry-up" if sd.get("in_dryup") else "")
            + ")."
            + (
                f" Enter on or before ~{support_point:.2f} ({support_basis}) "
                "while volume still confirms; do not chase above "
                + (f"{entry_reference:.2f}." if entry_reference is not None else "the planned entry.")
                if support_point is not None else ""
            )
        )
    else:
        reason = "not a coiled accumulator: " + "; ".join(blocks)

    return {
        "qualifies": qualifies,
        "coil_age_days": int(base_days) if _is_num(base_days) else None,
        "not_broken_out": not_broken_out,
        "flow": flow,
        "flow_strengthening": bool(flow_strengthening),
        "stage": entry_stage,
        "support_point": support_point,
        "support_basis": support_basis,
        "entry_reference": entry_reference,
        "volume_gate": volume_gate,
        "blocks": blocks,
        "reason": reason,
    }


def _prior_link(payload: dict) -> dict:
    """Link this coil to OUR previous picks of the same symbol.

    Reuses the `pick_history` trail already attached to the payload
    (picks_diff.attach_pick_history) — a newest-first list of prior days this
    symbol was picked. Answers "how long have WE been watching this coil?".
    """
    trail = payload.get("pick_history") or []
    prior = len(trail)
    first_seen = trail[-1].get("date") if trail else None  # oldest is last
    return {
        "prior_appearances": prior,
        "appearances_incl_today": prior + 1,
        "first_seen": first_seen,
    }


def build_coiled_accumulators(picks: list[dict]) -> list[dict]:
    """Build the Coiled Accumulators watch cohort from already-built payloads.

    `picks` should be the union of the main buy list and the `not_actionable`
    awareness payloads, so a strong coil routed to awareness is still surfaced.
    Env-gated: returns [] when STOCKYA_COILED_WATCH=0. Never raises — a single
    bad payload is skipped, the rest still build.

    Rows are ordered strongest-ongoing-first: (flow_strengthening, right-edge
    demand, coil age) descending. This is a MONITOR section with its own honest
    ordering (like delivery_analysis); it never touches `rank`/selection.
    """
    if not _enabled():
        return []

    rows: list[dict] = []
    for p in picks or []:
        try:
            if not isinstance(p, dict):
                continue
            a = assess_coiled_accumulator(p)
            if not a["qualifies"]:
                continue

            na = p.get("not_actionable") or {}
            also_flagged = na.get("category") if isinstance(na, dict) else None
            source_section = "awareness" if also_flagged else "main"

            pbe = p.get("pre_breakout_eligibility") or {}
            also_pre_breakout = bool(pbe.get("eligible")) if isinstance(pbe, dict) else False

            why = a["reason"]
            if also_flagged in ("stale_base", "late_entry", "extended_breakout", "timing_unclear"):
                why += (
                    f" Currently binned '{also_flagged}' — but flow says it is "
                    "still accumulating, not finished. Worth watching for the trigger."
                )

            rows.append({
                "symbol": p.get("symbol"),
                "company": p.get("company"),
                "rank": p.get("rank"),
                "coil_age_days": a["coil_age_days"],
                "flow": a["flow"],
                "flow_strengthening": a["flow_strengthening"],
                # Support to enter on or before (coil floor), the top of the buy
                # zone, and the volume condition that keeps the level live.
                "support_point": a["support_point"],
                "support_basis": a["support_basis"],
                "entry_reference": a["entry_reference"],
                "volume_gate": a["volume_gate"],
                "also_pre_breakout": also_pre_breakout,
                "source_section": source_section,
                "also_flagged": also_flagged,
                "prior": _prior_link(p),
                "why": why,
            })
        except Exception:  # never let one bad payload break the section
            continue

    def _sort_key(r: dict):
        flow = r.get("flow") or {}
        sr = flow.get("stealth_ratio")
        return (
            1 if r.get("flow_strengthening") else 0,
            sr if _is_num(sr) else -1.0,
            r.get("coil_age_days") or 0,
        )

    rows.sort(key=_sort_key, reverse=True)
    return rows
