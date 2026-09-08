"""Sequential BUY-readiness verdict — the "5-filter fortress" (owner ask, 2026-09-08).

THE BUG THIS FIXES
------------------
The engine scored its gates in PARALLEL and combined them into one composite S,
so a strong STRUCTURE leg (tight base, rising long-OBV) could outvote a bearish
TIMING/FLOW leg (no breakout yet, CMF/A-D distributing). That let premature
"Best Buy" cards through on bases that were quietly distributing (the Minda /
CG Power incidents). A weighted average is the wrong detector when the legs are
*preconditions*, not interchangeable evidence.

THE FIX — a strict SEQUENCE, not an average
--------------------------------------------
A stock is only an actionable BUY when THREE layers are green **in order**, plus
two hygiene gates. This module does NOT recompute any indicator — it reads the
fields the pipeline already persisted on the pick payload and assembles them into
one honest verdict. Reversible via env STOCKYA_BUY_READINESS=0.

    Layer 0  DATA INTEGRITY   indicator values are physically sane (CMF in
                              [-1,1], no NaN/inf, OBV slope not absurd, price>0).
                              CG Power's OBV +3123% is a data error, not a signal.
    Layer 1  STRUCTURE        Weinstein Stage-2 (or durable early accumulation)
                              AND not already extended / late / distribution.
    Layer 2  MONEY FLOW       no active distribution: CMF-21d not strongly
                              negative, A/D-line not falling hard, and no
                              distribution contradiction attached upstream.
                              (Long-OBV rising is already an [LTV] hard gate, so
                              any surfaced pick has it — echoed as a note.)
    Layer 3  ENTRY TRIGGER    the breakout actually fired today ([BR] passed:
                              close > 20d high, volume confirm, upper-third close).

VERDICT (safety-first — BUY requires POSITIVE evidence on every layer; the
absence of evidence is "watch", never "buy"):

    avoid   data-integrity fail, OR money-flow distributing, OR structure is
            late / extended / Stage-4. Do not enter — this is the trap.
    watch   structure + flow are clean but the trigger has NOT fired (a coil to
            accumulate/monitor and buy ON the trigger), OR the pick is only a
            watch-grade lead (selection_tier == lead_watch). Never badged BUY.
    buy     all layers green AND the pick cleared confirmation (tier confirmed).

This reconciles the review's "never buy before a confirmed breakout" with the
owner's early-accumulation thesis: a clean coil is still SURFACED and tabled — it
is simply labelled "watch / wait for the trigger", not "Best Buy".

Pure and deterministic: reads only persisted payload fields, never raises, runs
identically live and in the offline replay. Fix points at the top.
"""
from __future__ import annotations

import math
import os
from typing import Optional

# Reuse the ONE source of truth for money-flow distribution thresholds so this
# verdict and the tier guard / contradiction can never drift apart.
from .smart_money import AD_SLOPE_DISTRIBUTION_MAX, CMF_DISTRIBUTION_MAX

# --------------------------------------------------------------------------- #
# Fix points
# --------------------------------------------------------------------------- #

# entry_timing values (backend/volume_signals.py) that mean the timely entry has
# passed or the tape is distributing — a hard structure fail (avoid, not watch).
_EXTENDED_TIMINGS = frozenset({"late", "missed"})

# entry_stage ladder values that are an explicit "extended / late / failed" read.
_LATE_STAGES = frozenset({
    "POST_BREAKOUT_EXTENDED", "LATE_CHASE", "FAILED_BREAKOUT_RETEST",
})

# up/down-volume ratio (90d) at/above which recent net flow counts as healthy.
# Advisory NOTE only (does not block) — kept soft so the owner's stealth-demand
# right-edge logic remains the primary demand test. Mirrors the review's 1.3-1.5.
UP_DOWN_90D_HEALTHY: float = 1.3

# Data-integrity plausibility bounds (pure-technical sanity — NO fundamentals,
# per PRINCIPLES §8 "don't override the volume signal with fundamentals").
CMF_ABS_MAX: float = 1.05                 # CMF is bounded [-1, 1] by construction
OBV_NORM_SLOPE_ABS_MAX: float = 1000.0    # a normalized OBV slope beyond ±1000%/win is a data error
AD_SLOPE_ABS_MAX: float = 100000.0        # A/D 30d slope beyond ±100000% is a data error


def _enabled() -> bool:
    """On by default; STOCKYA_BUY_READINESS=0 restores pure-composite behaviour."""
    return os.environ.get("STOCKYA_BUY_READINESS", "1") != "0"


def _num(x) -> Optional[float]:
    """Coerce to a finite float, else None."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _finite_or_absurd(x, abs_max: float) -> Optional[str]:
    """Return an integrity complaint string if x is present-but-insane, else None.

    A *missing* value (None) is not an integrity failure here — it is simply
    unverifiable, handled by the layer that needs it. Only a present value that is
    non-finite or beyond `abs_max` is flagged as a data error.
    """
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return f"non-numeric ({x!r})"
    if not math.isfinite(f):
        return f"non-finite ({x!r})"
    if abs(f) > abs_max:
        return f"{f:+.0f} beyond ±{abs_max:.0f} (implausible)"
    return None


# --------------------------------------------------------------------------- #
# Layer assessors — each returns (passed, notes[])
# --------------------------------------------------------------------------- #

def _mf(payload: dict) -> dict:
    return ((payload.get("confirmation") or {}).get("money_flow")) or {}


def _assess_data_integrity(payload: dict) -> tuple[bool, list[str]]:
    notes: list[str] = []
    mf = _mf(payload)
    ft = payload.get("flow_timeframes") or {}

    for label, val, bound in (
        ("CMF-21d", mf.get("cmf_21d"), CMF_ABS_MAX),
        ("CMF-60d", mf.get("cmf_60d"), CMF_ABS_MAX),
        ("A/D-line slope", mf.get("ad_line_slope_pct"), AD_SLOPE_ABS_MAX),
        ("OBV-90d norm slope", ft.get("obv_90d_norm_slope_pct"), OBV_NORM_SLOPE_ABS_MAX),
        ("OBV-180d norm slope", ft.get("obv_180d_norm_slope_pct"), OBV_NORM_SLOPE_ABS_MAX),
    ):
        complaint = _finite_or_absurd(val, bound)
        if complaint:
            notes.append(f"{label} {complaint}")

    price = _num((payload.get("price_plan") or {}).get("entry")) or _num(payload.get("current_price"))
    if price is not None and price <= 0:
        notes.append(f"entry price {price} <= 0")

    return (len(notes) == 0), notes


def _assess_structure(payload: dict) -> tuple[bool, list[str]]:
    conf = payload.get("confirmation") or {}
    timing = str(conf.get("entry_timing") or "unknown")
    stage = str(conf.get("weinstein_stage") or "")
    entry_stage = str(payload.get("entry_stage") or "")
    early = payload.get("early_accumulation") or {}
    durable_slow = bool((early.get("features") or {}).get("durable_slow"))

    notes: list[str] = []
    if timing in _EXTENDED_TIMINGS or entry_stage in _LATE_STAGES:
        notes.append(
            f"extended/late (entry_timing={timing}"
            + (f", stage={entry_stage}" if entry_stage in _LATE_STAGES else "")
            + ")"
        )
        return False, notes

    stage2 = "2" in stage  # "stage_2_advance", "Stage 2", etc.
    if not (stage2 or durable_slow):
        notes.append(
            f"no Stage-2 / durable-accumulation base confirmed (stage={stage or '—'})"
        )
        return False, notes

    notes.append(
        ("Weinstein Stage-2" if stage2 else "durable slow accumulation")
        + (" + durable flow" if stage2 and durable_slow else "")
    )
    return True, notes


def _assess_money_flow(payload: dict) -> tuple[bool, list[str]]:
    mf = _mf(payload)
    ft = payload.get("flow_timeframes") or {}
    assess = payload.get("accumulation_assessment") or {}

    notes: list[str] = []
    fail = False

    # Any distribution contradiction already attached upstream (money-flow, [DV],
    # OBV-vs-delivery) — the single strongest tell that the tape is being sold.
    dist_contras = [
        str(c) for c in (assess.get("contradictions") or [])
        if "distribution" in str(c).lower()
    ]
    if dist_contras:
        fail = True
        notes.append("distribution contradiction: " + dist_contras[0])

    cmf = _num(mf.get("cmf_21d"))
    if cmf is not None and cmf <= CMF_DISTRIBUTION_MAX:
        fail = True
        notes.append(f"CMF {cmf:+.2f} <= {CMF_DISTRIBUTION_MAX} (selling at the close)")

    ad = _num(mf.get("ad_line_slope_pct"))
    if ad is not None and ad <= AD_SLOPE_DISTRIBUTION_MAX:
        fail = True
        notes.append(f"A/D-line {ad:+.0f}%/30d <= {AD_SLOPE_DISTRIBUTION_MAX} (falling)")

    if not fail:
        ud = _num(ft.get("up_down_vol_ratio_90d"))
        if ud is not None:
            notes.append(
                f"up/down-vol {ud:.2f}x"
                + (" (healthy)" if ud >= UP_DOWN_90D_HEALTHY else " (soft, <1.3x)")
            )
        notes.append("no active distribution")
    return (not fail), notes


def _assess_trigger(payload: dict) -> tuple[bool, list[str]]:
    gate = payload.get("gate_confirmation_status") or {}
    failed = gate.get("failed") or []
    passed = gate.get("passed") or []
    esf = payload.get("entry_stage_features") or {}
    br_today = bool(esf.get("br_passed_today"))

    br_fired = br_today or ("BR" in passed and "BR" not in failed)
    if br_fired:
        return True, ["breakout fired today (close > 20d high on volume, upper-third close)"]
    return False, ["no confirmed breakout yet (BR gate not passed)"]


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #

def assess_buy_readiness(payload: dict) -> Optional[dict]:
    """Sequential BUY-readiness verdict for one pick payload.

    Returns None when disabled (STOCKYA_BUY_READINESS=0) so callers fall back to
    the pure-composite behaviour. Otherwise:

        {
          "state":    "buy" | "watch" | "avoid",
          "category": str,   # routes to the entry-readiness awareness bin
          "why":      str,   # one-liner
          "layers": {        # per-layer {"pass": bool, "notes": [...]}
            "data_integrity": {...}, "structure": {...},
            "money_flow": {...}, "trigger": {...},
          },
        }
    """
    if not _enabled() or not isinstance(payload, dict):
        return None

    di_pass, di_notes = _assess_data_integrity(payload)
    st_pass, st_notes = _assess_structure(payload)
    mf_pass, mf_notes = _assess_money_flow(payload)
    tr_pass, tr_notes = _assess_trigger(payload)

    tier = payload.get("selection_tier") or (payload.get("confirmation") or {}).get("selection_tier")

    layers = {
        "data_integrity": {"pass": di_pass, "notes": di_notes},
        "structure": {"pass": st_pass, "notes": st_notes},
        "money_flow": {"pass": mf_pass, "notes": mf_notes},
        "trigger": {"pass": tr_pass, "notes": tr_notes},
    }

    # ---- Sequence — first failing hygiene/precondition decides the state ---- #
    if not di_pass:
        state, category = "avoid", "data_integrity"
        why = "Data-integrity check failed — " + "; ".join(di_notes) + " (not trustworthy)."
    elif not mf_pass:
        state, category = "avoid", "distribution"
        why = "Active distribution — " + "; ".join(mf_notes) + ". Rallies are being sold; not a buy."
    elif not st_pass:
        # Late/extended/Stage-4 = the timely entry has passed → avoid.
        state, category = "avoid", "late_entry"
        why = "Structure fail — " + "; ".join(st_notes) + "."
    elif not tr_pass:
        # Structure + flow clean but the breakout has NOT fired: a coil to watch
        # and buy ON the trigger — never an at-market buy today.
        state, category = "watch", "setup_unconfirmed"
        why = (
            "Setup forming — structure and money flow are clean but the breakout "
            "has not fired. Watch for a close above the pivot on >=1.5x volume; "
            "do not buy at market yet."
        )
    elif tier == "lead_watch":
        state, category = "watch", "lead_watch"
        why = (
            payload.get("lead_note")
            or "Watch-grade lead — coiling just under confirmation or demoted by "
               "distribution risk. Wait for the trigger; do not buy at market."
        )
    else:
        state, category = "buy", "enterable"
        why = "All layers green — Stage-2 structure, healthy money flow, confirmed breakout."

    return {"state": state, "category": category, "why": why, "layers": layers}
