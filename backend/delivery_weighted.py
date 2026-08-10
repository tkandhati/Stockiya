"""Fresh, delivery-LED analysis over the day's eligible field — SCORING-NEUTRAL.

A SEPARATE presentation-layer view for the picks page. It is NOT a re-ranking of
the volume picks: it runs its own score over every hard-gate survivor the pipeline
evaluated today, with delivery weighted in as a first-class term, and returns its
own ranked shortlist. A strong-delivery near-miss can therefore outrank a volume
pick here — that's the point.

It never touches selection, the composite, the confirmation rank, sizing, exits,
or any other section: the orchestrator computes it AFTER selection and attaches it
to the payload as an additive `delivery_analysis` block (same posture as
`flow_interest` / the closest-to-firing panel).

The score
    fresh = w · delivery_signal + (1 − w) · base_norm
      delivery_signal : delivery.accum_signal ∈ [0,1] (0 when no delivery on disk)
      base_norm       : the candidate's composite S scaled to the day's strongest
                        survivor (so the two legs are comparable and `w` is honest)
      w               : DELIVERY_ANALYSIS_WEIGHT (delivery-led by design)

Determinism: pure function of its inputs; ties broken by symbol; no wall clock.
When no delivery data is on disk (firewall/offline), every delivery_signal is 0 and
the result collapses to the plain composite order — a safe, honest fallback.

Fix points:
    DELIVERY_ANALYSIS_WEIGHT : delivery's share of the fresh score (default 0.50)
    DELIVERY_ANALYSIS_TOP_N  : rows returned (default 8)
"""
from __future__ import annotations

from typing import Optional

DELIVERY_ANALYSIS_WEIGHT: float = 0.50
DELIVERY_ANALYSIS_TOP_N: int = 8


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def build_delivery_analysis(
    candidates: list[dict],
    picked_symbols: Optional[set[str]] = None,
    *,
    weight: float = DELIVERY_ANALYSIS_WEIGHT,
    top_n: int = DELIVERY_ANALYSIS_TOP_N,
) -> list[dict]:
    """Fresh delivery-led ranking of `candidates`.

    Each candidate: {symbol, company, composite_score, delivery(advisory dict)}.
    `picked_symbols` flags which rows are also volume picks (badge only).
    Returns up to `top_n` self-describing rows, ranked by fresh_score desc.
    """
    picked = picked_symbols or set()
    max_base = max(
        (float(c.get("composite_score") or 0.0) for c in candidates), default=0.0
    )
    rows: list[dict] = []
    for c in candidates:
        raw = max(0.0, float(c.get("composite_score") or 0.0))
        base_norm = (raw / max_base) if max_base > 0 else 0.0
        adv = c.get("delivery") or {}
        has_sig = bool(adv.get("available")) and adv.get("accum_signal") is not None
        sig_v = _clamp01(float(adv.get("accum_signal"))) if has_sig else 0.0
        fresh = round(weight * sig_v + (1.0 - weight) * _clamp01(base_norm), 4)
        rows.append({
            "symbol": c["symbol"],
            "company": c.get("company") or c["symbol"],
            "base_score": round(raw, 4),           # raw composite S (display)
            "base_norm": round(_clamp01(base_norm), 4),
            "delivery_signal": round(sig_v, 4) if has_sig else None,
            "fresh_score": fresh,
            "delivery": adv or None,
            "in_picks": c["symbol"] in picked,
        })
    rows.sort(key=lambda r: (-r["fresh_score"], r["symbol"]))
    return rows[:top_n]
