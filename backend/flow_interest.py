"""Presentation-layer institutional-flow interest — SCORING-NEUTRAL.

Bulk/block deals and NSE delivery % are deliberately kept OUT of the
deterministic price/volume scoring flow (composite S and the confirmation
ranker). Per the design decision on 2026-07-27 they serve two non-scoring roles:

  1. INDICATOR  — flag which picks carry an institutional-flow tailwind worth a
     closer look (the `analyze` priority + `label`), and corroborate the
     hypothesis the price/volume flow already formed.
  2. PRESENTATION RANK — order the ALREADY-SELECTED picks for display, WITHOUT
     touching selection, composite_score, confirmation_score, or the canonical
     confirmation `rank`.

Design guarantees:
  - Nothing here is imported by a scoring stage. It is called only from the
    orchestrator's I/O layer (Phase 3), after picks are chosen.
  - It reads the SAME file-only rolling averages the reasoning checklist already
    shows: block_deals.aggregate_30d (7d-vs-30d net-buy trend) and
    delivery.delivery_advisory (5d/20d delivery-% means). No network, no LLM,
    deterministic, never raises.
  - When no deals/delivery data is on disk, interest is "none" and the
    presentation rank FALLS BACK to the confirmation rank — so a firewalled /
    offline run presents picks exactly as the price/volume flow chose them.

Fix points (all tunable, display-only — they can never change which stocks are
picked):
    DEAL_SUBWEIGHT / DELIV_SUBWEIGHT : deal-vs-delivery blend (renormalized)
    DEAL_TREND_SCORE                 : rising/flat/falling → sub-score
    DEAL_DISCLOSED_BOOST             : + when a disclosed institution is on record
    DELIV_TREND_ADJ                  : ± on the delivery band for rising/falling
    STRONG_INTEREST / MODERATE_INTEREST : 0-100 band cutoffs for the level label
    MIN_DEAL_COUNT                   : minimum deal events before the deal leg counts
"""
from __future__ import annotations

from typing import Optional

# --------------------------------------------------------------------------- #
# Tunables (display-only — see module docstring)
# --------------------------------------------------------------------------- #

DEAL_SUBWEIGHT: float = 0.5
DELIV_SUBWEIGHT: float = 0.5
DEAL_TREND_SCORE: dict[str, float] = {"rising": 1.0, "flat": 0.6, "falling": 0.2}
DEAL_DISCLOSED_BOOST: float = 0.2
DELIV_TREND_ADJ: float = 0.15
STRONG_INTEREST: int = 66     # score >= -> "strong"
MODERATE_INTEREST: int = 33   # score >= -> "moderate"
MIN_DEAL_COUNT: int = 2
WATCHLIST_TOP_N: int = 12     # max rows in the accumulation watchlist (Use 1)


# --------------------------------------------------------------------------- #
# Per-leg components (file-only, None-safe)
# --------------------------------------------------------------------------- #

def _deal_component(symbol: str) -> Optional[dict]:
    """Rolling bulk/block-deal interest in [0, 1], or None when it should be silent.

    None unless there are >= MIN_DEAL_COUNT deals, NET BUYING (ratio > 0), and a
    computed rolling trend. Positive-only: distribution never produces a
    component (it neither inflates nor deflates presentation interest).
    """
    try:
        from .block_deals import aggregate_30d
        agg = aggregate_30d(symbol)
    except Exception:  # noqa: BLE001 — deals cache missing/unreadable
        return None
    if (agg.buy_count + agg.sell_count) < MIN_DEAL_COUNT:
        return None
    if agg.net_qty_ratio <= 0 or agg.deal_trend is None:
        return None
    sub = DEAL_TREND_SCORE.get(agg.deal_trend, 0.6)
    if agg.has_disclosed_large_client:
        sub = min(1.0, sub + DEAL_DISCLOSED_BOOST)
    reason = f"bulk/block net-buy {agg.deal_trend}"
    if agg.has_disclosed_large_client:
        reason += f", {agg.institutional_client_count} disclosed institution(s)"
    return {
        "sub": round(max(0.0, min(1.0, sub)), 4),
        "trend": agg.deal_trend,
        "disclosed": agg.has_disclosed_large_client,
        "net_qty_ratio": agg.net_qty_ratio,
        "reason": reason,
    }


def _deliv_component_from_advisory(d: Optional[dict]) -> Optional[dict]:
    """Delivery interest in [0, 1] from a PRE-FETCHED advisory dict — pure, no I/O.

    Uses the 20-day rolling delivery mean mapped through the NSE weak/strong
    bands, nudged ± by the 5d-vs-20d trend. Returns None when the advisory is
    absent/unavailable. Used directly by the batch watchlist so it never reads
    a file per symbol.
    """
    if not d or not d.get("available") or d.get("avg_20d") is None:
        return None
    from .delivery import STRONG_DELIV_PCT, WEAK_DELIV_PCT
    span = max(1e-9, STRONG_DELIV_PCT - WEAK_DELIV_PCT)
    level = (float(d["avg_20d"]) - WEAK_DELIV_PCT) / span
    trend = d.get("trend")
    adj = (DELIV_TREND_ADJ if trend == "rising"
           else -DELIV_TREND_ADJ if trend == "falling" else 0.0)
    sub = max(0.0, min(1.0, level + adj))
    band = d.get("level") or "moderate"
    reason = (f"delivery {float(d['avg_20d']):.0f}% ({band}"
              + (f", {trend}" if trend else "") + ")")
    return {
        "sub": round(sub, 4),
        "avg_20d": round(float(d["avg_20d"]), 2),
        "latest_pct": (round(float(d["latest_pct"]), 2)
                       if d.get("latest_pct") is not None else None),
        "trend": trend,
        "level_band": band,
        "reason": reason,
    }


def _deliv_component(symbol: str, advisory: Optional[dict] = None) -> Optional[dict]:
    """Delivery interest for a symbol — fetches the advisory if not supplied.

    Accepts a pre-fetched advisory (the one the orchestrator already attached to
    the payload) to avoid a second disk read; otherwise reads it itself.
    """
    d = advisory
    if d is None:
        try:
            from .delivery import delivery_advisory
            d = delivery_advisory(symbol)
        except Exception:  # noqa: BLE001 — delivery corpus missing/unreadable
            return None
    return _deliv_component_from_advisory(d)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _percentile(value: Optional[float], dist: list[float]) -> Optional[float]:
    """Percentile rank of `value` within `dist` (0-100). None if inputs empty."""
    if value is None or not dist:
        return None
    below = sum(1 for x in dist if x <= value)
    return round(100.0 * below / len(dist), 1)


def _assemble(
    deal: Optional[dict],
    deliv: Optional[dict],
    market_pcts: Optional[list[float]] = None,
) -> dict:
    """Blend the two components into the display block. Pure — no I/O.

    Returns {available, score 0-100, level, label, analyze, suppressed, reasons,
    vs_normal, components}. When `market_pcts` (today's cross-section of delivery
    %) is supplied, `vs_normal.delivery_percentile` says where this name sits
    against the market — the 'strength against the normal' the user asked for.
    """
    parts: list[tuple[float, float]] = []
    if deal is not None:
        parts.append((DEAL_SUBWEIGHT, deal["sub"]))
    if deliv is not None:
        parts.append((DELIV_SUBWEIGHT, deliv["sub"]))

    if not parts:
        # Completely suppressed: no deals + no delivery (or contrary /
        # insufficient). The pick, if any, rests entirely on price/volume.
        return {
            "available": False, "score": 0, "level": "none",
            "label": "no institutional-flow data",
            "analyze": False, "suppressed": True, "reasons": [],
            "vs_normal": None,
            "components": {"deal": None, "delivery": None},
        }

    wsum = sum(w for w, _ in parts) or 1.0
    blended = sum(w * s for w, s in parts) / wsum
    score = int(round(100 * blended))
    level = ("strong" if score >= STRONG_INTEREST
             else "moderate" if score >= MODERATE_INTEREST else "low")
    reasons = [c["reason"] for c in (deal, deliv) if c is not None]

    vs_normal: Optional[dict] = None
    if deliv is not None and market_pcts:
        ref = deliv.get("latest_pct")
        if ref is None:
            ref = deliv.get("avg_20d")
        pct = _percentile(ref, market_pcts)
        if pct is not None:
            vs_normal = {
                "delivery_percentile": pct,     # e.g. 96.0 -> top 4% of the market
                "delivery_band": deliv.get("level_band"),
                "cohort_n": len(market_pcts),
            }

    return {
        "available": True,
        "score": score,
        "level": level,
        "label": "; ".join(reasons),
        "analyze": level in ("strong", "moderate"),
        # "suppressed" = present but too weak to back the pick (sorts to the
        # bottom of the presentation alongside the no-data picks).
        "suppressed": level == "low",
        "reasons": reasons,
        "vs_normal": vs_normal,
        "components": {"deal": deal, "delivery": deliv},
    }


def flow_interest(
    symbol: str,
    delivery: Optional[dict] = None,
    market_pcts: Optional[list[float]] = None,
) -> dict:
    """Scoring-neutral institutional-flow interest for one symbol. Never raises."""
    return _assemble(
        _deal_component(symbol),
        _deliv_component(symbol, delivery),
        market_pcts,
    )


def build_watchlist(top_n: int = WATCHLIST_TOP_N) -> list[dict]:
    """Flow-ranked institutional-accumulation WATCHLIST (Use 1 — guidance).

    Scoring-neutral: pure guidance for which stocks to analyze/pick. It does NOT
    enter the scan or change any gate. Candidates are only names that actually
    carry flow data (deal symbols ∪ delivery symbols); each is scored with the
    same combined strength, ranked, and filtered to moderate+ interest.

    Performance: loads the delivery corpus ONCE (all_advisories) and only pulls a
    per-symbol deal aggregate for the bounded set of deal symbols. Empty list
    when no deals/delivery data is on disk.
    """
    try:
        from .block_deals import deal_symbols
        from .delivery import all_advisories, latest_market_pcts
    except Exception:  # noqa: BLE001
        return []

    try:
        advisories = all_advisories()
    except Exception:  # noqa: BLE001
        advisories = {}
    try:
        market = latest_market_pcts()
    except Exception:  # noqa: BLE001
        market = []
    try:
        deal_syms = set(deal_symbols())
    except Exception:  # noqa: BLE001
        deal_syms = set()

    rows: list[dict] = []
    for sym in set(advisories.keys()) | deal_syms:
        deal = _deal_component(sym) if sym in deal_syms else None
        deliv = _deliv_component_from_advisory(advisories.get(sym))
        fi = _assemble(deal, deliv, market)
        if fi.get("available") and fi.get("level") in ("strong", "moderate"):
            rows.append({"symbol": sym, "flow_interest": fi})

    rows.sort(key=lambda r: -r["flow_interest"]["score"])
    return rows[:top_n]


def why_picked(payload: dict) -> str:
    """Concise price/volume rationale for a pick — the basis that stands on its
    own when the institutional-flow signals are suppressed.

    Reads only fields the payload already carries (gates cleared, confirmation
    strength + bonuses, entry stage). Shape-tolerant: `confirmation` may be a
    dict {score, bonuses_fired} or a bare float. Never raises.
    """
    bits: list[str] = []

    gc = payload.get("gate_confirmation_status") or {}
    passed = gc.get("passed") or []
    if passed:
        bits.append("cleared " + "/".join(passed))

    conf = payload.get("confirmation")
    score: Optional[float] = None
    bonuses: list = []
    if isinstance(conf, dict):
        score = conf.get("score")
        bonuses = conf.get("bonuses_fired") or []
    elif isinstance(conf, (int, float)):
        score = float(conf)
    if not bonuses:
        bonuses = (payload.get("confirmation_components") or {}).get("bonuses_fired") or []
    if score is not None:
        tail = f" ({len(bonuses)} bonus)" if bonuses else ""
        bits.append(f"confirmation {float(score):.2f}{tail}")

    stage = payload.get("entry_stage")
    if stage:
        bits.append(f"entry {str(stage).replace('_', ' ').lower()}")

    if not bits:
        return "Picked on price/volume gates."
    return "Picked on price/volume: " + "; ".join(bits) + "."


def assign_presentation_ranks(picks: list[dict]) -> None:
    """Attach `presentation_rank` (1..N) to each pick payload, in place.

    Ordered by (flow interest DESC, confirmation rank ASC). NON-DESTRUCTIVE:
    does not reorder the `picks` list and does not touch `rank`, `selected`,
    `composite_score`, or `confirmation_score`. Picks with no flow data sort
    last on interest and therefore fall back to their confirmation order — so an
    offline run yields presentation_rank == confirmation rank.
    """
    if not picks:
        return

    def _key(p: dict) -> tuple[int, int]:
        fi = p.get("flow_interest") or {}
        interest = fi.get("score", 0) if fi.get("available") else -1
        conf_rank = p.get("rank") or 10_000
        return (-interest, conf_rank)

    order = sorted(range(len(picks)), key=lambda i: _key(picks[i]))
    for position, idx in enumerate(order, start=1):
        picks[idx]["presentation_rank"] = position
