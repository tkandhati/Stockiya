"""Reasoning checklist builder — turns a finished pick payload into the
staged, human-facing "steps" the UI renders (frontend ReasoningChecklist).

Why this module exists
----------------------
The gates-based spine stopped emitting the legacy `reasoning` array, so the
ReasoningChecklist component on the front end went dormant: `pick.reasoning`
was never populated, and the checklist silently rendered nothing. In
particular the NSE **delivery-% load status** and the **bulk/block deal
rolling trend** were computed but never surfaced as steps — the user could
not tell, from the picks screen, whether delivery data was even loaded.

This builder is:
  - ADDITIVE — it only reads fields the payload already carries (plus a cheap
    file-only deal aggregate). It never gates selection; the composite already
    did that.
  - DETERMINISTIC — templated strings, no LLM, byte-identical on re-run.
  - NONE-SAFE — every step degrades to an honest "unavailable / not loaded"
    line rather than crashing or being omitted, so the *load status* is always
    visible.

Each point is a dict matching frontend `ReasoningPoint`:
    {label, value, state: bullish|neutral|bearish, why, verify}

Called from the orchestrator's Phase 3, AFTER `payload["delivery"]` is
attached, so the delivery step reflects the same advisory the pill shows.
"""
from __future__ import annotations

from typing import Optional


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _pt(label: str, value: str, state: str, why: str, verify: str) -> dict:
    return {"label": label, "value": value, "state": state, "why": why, "verify": verify}


_GATE_META = {
    "CS": (
        "Consolidation base",
        "A tight, mature base above the 150-day MA means supply has been "
        "absorbed — the launch pad institutions build before a markup.",
        "StockDetail → gate evidence [CS]; ATR/Close ≤ 4%, ≥25 days in a ±10% band.",
    ),
    "VD": (
        "Volume dry-up + OBV divergence",
        "Volume drying up near support while OBV leads price up is the Wyckoff "
        "Phase-C fingerprint of quiet re-accumulation.",
        "StockDetail → gate evidence [VD]; 5d vol < 50% of 50d avg + bullish divergence.",
    ),
    "BR": (
        "Breakout thrust",
        "The objective institutional trigger: close above the 20-day high on "
        "≥1.5× volume, finishing in the upper third of the day's range.",
        "StockDetail → gate evidence [BR]; close vs 20d high, volume ratio, close location.",
    ),
}

# Entry-stage label → (friendly text, state). Mirrors entry_stage_label.py.
_ENTRY_STAGE_META: dict[str, tuple[str, str]] = {
    "DEEP_BASE": ("Deep base — well under the pivot", "neutral"),
    "BUILDING_BASE": ("Building a base under the pivot", "neutral"),
    "COILED_PRE_BREAKOUT": ("Coiled, pre-breakout", "bullish"),
    "AT_PIVOT": ("At the pivot", "bullish"),
    "AT_PIVOT_NO_DEMAND": ("At the pivot but volume is dry (no demand)", "bearish"),
    "BREAKOUT_CONFIRMED_TODAY": ("Breakout confirmed today", "bullish"),
    "POST_BREAKOUT_HEALTHY": ("Post-breakout, still healthy", "bullish"),
    "POST_BREAKOUT_EXTENDED": ("Post-breakout, getting extended", "neutral"),
    "LATE_CHASE": ("Late chase — extended above SMA20", "bearish"),
    "FAILED_BREAKOUT_RETEST": ("Failed breakout, retesting", "bearish"),
    "DATA_UNAVAILABLE": ("Entry stage not classifiable", "neutral"),
}


# --------------------------------------------------------------------------- #
# Per-step builders
# --------------------------------------------------------------------------- #

def _gate_points(payload: dict) -> list[dict]:
    status = payload.get("gate_confirmation_status") or {}
    passed = set(status.get("passed") or [])
    evidence = payload.get("gates_evidence") or {}
    out: list[dict] = []
    for gid in ("CS", "VD", "BR"):
        label, why, verify = _GATE_META[gid]
        lines = evidence.get(gid) or []
        if gid in passed:
            value = lines[0] if lines else "cleared"
            state = "bullish"
        else:
            value = "not confirmed (composite-qualified)"
            state = "neutral"
        out.append(_pt(label, value, state, why, verify))
    return out


def _confirmation_point(payload: dict) -> dict:
    conf = payload.get("confirmation") or {}
    score = conf.get("score")
    bonuses = conf.get("bonuses_fired") or []
    if score is None:
        return _pt(
            "Confirmation strength", "unavailable", "neutral",
            "Confirmation ranks survivors by the sum of weighted gate margins "
            "plus bonuses.",
            "Pick card → confirmation strip.",
        )
    bonus_txt = f" · {len(bonuses)} bonus" + (f" ({', '.join(bonuses[:2])})" if bonuses else "")
    return _pt(
        "Confirmation strength",
        f"{float(score):.2f}{bonus_txt}",
        "bullish",
        "How strongly this pick cleared the composite versus its peers — the "
        "ranking signal that put it in the top N.",
        "Pick card → confirmation strip; StockDetail → confirmation components.",
    )


def _deals_point(symbol: str) -> dict:
    """Bulk + block deal rolling accumulation trend (file-only, None-safe)."""
    try:
        from .block_deals import aggregate_30d
        agg = aggregate_30d(symbol)
    except Exception:  # noqa: BLE001 — deals cache missing/unreadable
        agg = None

    if agg is None or (agg.buy_count + agg.sell_count) == 0:
        return _pt(
            "Bulk / block deals (30d)",
            "no disclosed deals on record",
            "neutral",
            "NSE bulk & block deals are literal records of large trades — "
            "harder to fake than aggregated volume. None are on record for "
            "this ticker in the last 30 days, so this leg is silent.",
            "data/deals/all.csv (refreshed nightly). Run: python -m backend.block_deals",
        )

    net = agg.net_qty
    ratio = agg.net_qty_ratio
    trend = agg.deal_trend
    trend_txt = f", {trend}" if trend else ""
    sign = "+" if net >= 0 else ""
    value = f"net {sign}{net:,} sh · ratio {ratio:+.2f}{trend_txt}"
    if net > 0 and ratio > 0:
        state = "bullish"
    elif net < 0 and ratio < 0:
        state = "bearish"
    else:
        state = "neutral"

    why_bits = [
        "Net buy/sell of disclosed large trades over 30 days; the rolling "
        "trend compares the last 7 days' daily net-buy rate against the 30-day "
        "rate, so accelerating accumulation reads 'rising'."
    ]
    if agg.has_disclosed_large_client:
        why_bits.append(
            f"{agg.institutional_client_count} disclosed institutional "
            "client(s) — participant evidence upgraded from inferred to disclosed."
        )
    return _pt(
        "Bulk / block deals (30d)",
        value,
        state,
        " ".join(why_bits),
        "data/deals/all.csv; ratio = net/(buy+sell); trend = 7d rate vs 30d rate.",
    )


def _delivery_point(payload: dict) -> dict:
    """Delivery-% step — ALWAYS present so load status is visible even when
    no MTO files are on disk (that was the whole complaint)."""
    d = payload.get("delivery")
    if not d or not d.get("available") or d.get("latest_pct") is None:
        return _pt(
            "Delivery % (accumulation vs churn)",
            "no delivery files loaded",
            "neutral",
            "Delivery % (deliverable ÷ traded) separates real accumulation "
            "(shares taken to delivery) from intraday churn. No NSE MTO files "
            "are on disk, so this discriminator is unavailable — the pick rests "
            "on price/volume + deals alone.",
            "Drop NSE MTO files into data/delivery/ or run: python -m backend.delivery "
            "(also shown on the Data Health page).",
        )

    latest = float(d["latest_pct"])
    level = d.get("level") or "moderate"
    trend = d.get("trend")
    days = d.get("days") or 0
    trend_txt = f", {trend}" if trend else ""
    # Accumulation ladder: today, week, 15d, 30d (the windows the user asked for).
    roll = []
    for label, key in (("wk", "avg_5d"), ("15d", "avg_15d"), ("30d", "avg_30d")):
        v = d.get(key)
        if v is not None:
            roll.append(f"{label} {v:.0f}%")
    roll_txt = f" (today; {'; '.join(roll)}; {days}d on record)" if roll else f" ({days}d on record)"
    value = f"{latest:.0f}% {level}{trend_txt}{roll_txt}"
    state = "bullish" if level == "strong" else "bearish" if level == "weak" else "neutral"
    return _pt(
        "Delivery % (accumulation vs churn)",
        value,
        state,
        "High delivery = shares actually taken to delivery and held (strong "
        "hands); low delivery = intraday churn inflating raw volume. The rolling "
        "means over today / week / 15d / 30d show whether real accumulation is "
        "building or fading.",
        "delivery_advisory() reads data/delivery/*.csv; week/15d/30d are rolling "
        "means; trend = week ÷ 20d.",
    )


def _participant_point(payload: dict) -> dict:
    a = payload.get("accumulation_assessment") or {}
    pe = a.get("participant_evidence")
    if not pe:
        return _pt(
            "Participant evidence", "unavailable", "neutral",
            "Whether the accumulation read is backed by disclosed institutional "
            "clients or only inferred from tape.",
            "StockDetail → accumulation assessment envelope.",
        )
    level = a.get("level")
    score = a.get("score_0_100")
    disclosed = "disclosed" in str(pe)
    value = f"{pe}" + (f" · level {level}" if level else "") + (f" · {score}/100" if score is not None else "")
    return _pt(
        "Participant evidence",
        value,
        "bullish" if disclosed else "neutral",
        "'disclosed' means named institutional clients appear in the deal "
        "records; 'inferred' means the accumulation is read from tape only "
        "(the honest default).",
        "StockDetail → accumulation assessment envelope.",
    )


def _early_accumulation_point(payload: dict) -> Optional[dict]:
    """Highlight a genuine early + slowly-accumulating setup.

    Only emitted when the profile matches (tier early/mid) so the checklist
    stays clean; a non-match simply omits the step. Advisory only — the ranker
    already used this to float the pick up. See backend/early_accumulation.py.
    """
    ea = payload.get("early_accumulation")
    if not ea or not ea.get("is_match"):
        return None
    tier = ea.get("tier")
    reasons = ea.get("reasons") or []
    score = ea.get("score")
    if tier == "early":
        value = "genuine early entry · slow+durable accumulation"
        state = "bullish"
    else:  # "mid"
        value = "slow+durable accumulation (early Stage 2)"
        state = "bullish"
    if score is not None:
        value += f" · quality {float(score):.2f}"
    why = (
        "The profile you want to own: still near the launch pad while volume "
        "quietly accumulates over BOTH the last quarter and half-year (OBV-90d "
        "and OBV-180d positive), with steady net buying and a dry-up / "
        "divergence footprint — a slow institutional build, not a fast blow-off. "
        + ("; ".join(reasons) if reasons else "")
    )
    return _pt(
        "Early accumulation",
        value,
        state,
        why.strip(),
        "backend/early_accumulation.py — advisory; floats the pick up in rank.py, "
        "does not gate selection.",
    )


def _entry_stage_point(payload: dict) -> Optional[dict]:
    label = payload.get("entry_stage")
    if not label:
        return None
    friendly, state = _ENTRY_STAGE_META.get(label, (str(label), "neutral"))
    return _pt(
        "Entry stage",
        friendly,
        state,
        "Where the setup is right now — pre-breakout base, at the pivot, "
        "freshly confirmed, or already extended. Tells you enter / wait / "
        "leave alone.",
        "backend/entry_stage_label.py — advisory ladder, does not gate selection.",
    )


def _horizon_point(payload: dict) -> Optional[dict]:
    h = payload.get("holding_horizon")
    if not h:
        return None
    days = h.get("days")
    return _pt(
        "Holding horizon",
        f"{days}d" if days is not None else "unavailable",
        "neutral",
        "Volume-based estimate of how long the setup should need to work — the "
        "outer clock, not a target. T1 tends to hit far sooner on working setups.",
        "backend/horizon.py — bucket basis in holding_horizon.basis.",
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def build_reasoning(payload: dict) -> list[dict]:
    """Assemble the ordered reasoning checklist for one pick payload.

    Never raises: any per-step failure is skipped rather than breaking the
    pick. Returns [] only if literally nothing could be built.
    """
    symbol = payload.get("symbol") or ""
    steps: list[dict] = []
    try:
        steps.extend(_gate_points(payload))
    except Exception:  # noqa: BLE001
        pass
    for builder in (
        lambda: _confirmation_point(payload),
        lambda: _early_accumulation_point(payload),
        lambda: _deals_point(symbol),
        lambda: _delivery_point(payload),
        lambda: _participant_point(payload),
        lambda: _entry_stage_point(payload),
        lambda: _horizon_point(payload),
    ):
        try:
            pt = builder()
        except Exception:  # noqa: BLE001
            pt = None
        if pt:
            steps.append(pt)
    return steps
