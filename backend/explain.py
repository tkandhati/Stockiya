"""Explain — turn a StageResult into one paragraph of plain English.

The simulation UI exists to teach, not just to score. For every gate's
pass-or-fail we render the WHY behind that gate's threshold and what the
specific number means. Templated, deterministic, no LLM.

If a gate's stage_id isn't covered here, we fall back to the reason / evidence
that the stage already produced — so adding a new gate doesn't crash; it just
gets a less polished explanation until someone writes a template.
"""

from __future__ import annotations

from .pipeline import StageResult


def _fmt_list(items: list[str]) -> str:
    return " ".join(items) if items else ""


def _hr(sr: StageResult) -> str:
    """[HR] Hard rejects — distribution / dumping defense."""
    f = sr.features or {}
    if sr.passed:
        return (
            "No signs of institutions unloading. The stock isn't extended above "
            "its 50-day MA, and it hasn't run up parabolically in the last month. "
            "Safe to evaluate the rest of the gates."
        )
    parts = []
    ret30 = f.get("ret_30d_pct")
    ext = f.get("extended_pct")
    if ret30 is not None and ret30 > 25:
        parts.append(
            f"The stock is up {ret30:.1f}% in the last 30 days — a parabolic move. "
            "When a stock runs that hard that fast, it's almost always retail FOMO "
            "buying into supply that institutions are quietly distributing. Buying "
            "here makes you the exit liquidity."
        )
    if ext is not None and ext > 25:
        parts.append(
            f"Price is {ext:.1f}% above the 50-day moving average. Minervini's "
            "'extended' rule says once a stock is more than 25% above its 50d MA, "
            "the easy money has been made and mean-reversion risk rises sharply. "
            "Wait for a pullback or a new base."
        )
    return _fmt_list(parts) or sr.reason


def _lt(sr: StageResult) -> str:
    """[LT] Long-term institutional flow."""
    f = sr.features or {}
    if sr.passed:
        return (
            f"Three months of institutional accumulation confirmed. "
            f"OBV is rising +{f.get('obv_90d_slope_pct', 0):.1f}% over 90 days "
            f"(net buying), up-days outweigh down-days by "
            f"{f.get('up_down_vol_ratio_90d', 0):.2f}× on volume, and the "
            f"150-day moving average is sloping "
            f"{f.get('ma150_slope_pct', 0):+.2f}% (long-term floor is rising). "
            "This is the 'why we believe it's institutional' check."
        )
    return (
        "Long-term institutional flow is missing. We need three independent "
        "checks pointing at quiet accumulation over the last quarter: rising "
        "OBV, up-day volume beating down-day volume, and a rising 150-day MA. "
        f"What we saw: {sr.reason}. Without sustained 3-month accumulation, "
        "any tight base today is just noise."
    )


def _cs(sr: StageResult) -> str:
    """[CS] Consolidation gate."""
    f = sr.features or {}
    if sr.passed:
        return (
            f"Tight base above the institutional floor. ATR/price is "
            f"{f.get('atr_pct', 0):.2f}% (≤4% threshold), the stock has held "
            f"in a ±10% band for {f.get('days_in_band', 0)} days "
            f"(≥25 required), and it's trading "
            f"{f.get('above_ma150_pct', 0):+.2f}% above the 150-day MA. "
            "This is Stage-1 accumulation in Weinstein terms / a VCP base "
            "in Minervini terms — supply has been absorbed and the next "
            "move tends to be the trend."
        )
    return (
        "No tight base found. We require: (a) range tight enough — ATR/Close "
        "≤ 4%, (b) duration mature — ≥25 trading days inside a ±10% band, "
        "(c) above the 150-day MA. "
        f"What we saw: {sr.reason}. Loose, short, or below-floor bases break "
        "out unreliably; the gate exists to skip them."
    )


def _vd(sr: StageResult) -> str:
    """[VD] Volume dry-up + bullish OBV-price divergence."""
    f = sr.features or {}
    div = (f.get("divergence") or {})
    if sr.passed:
        ratio = f.get("vol_ratio_5_50")
        return (
            f"Supply has dried up AND OBV is leading price up. "
            f"Last 5 days averaged {int((ratio or 0) * 100)}% of the 50-day "
            f"average volume (<50% threshold) — the quiet before institutions "
            f"reaccumulate. And the on-balance-volume line is in "
            f"'{div.get('form', '?')}' divergence: "
            f"{div.get('detail', '')}. Classic Wyckoff Phase-C signature."
        )
    return (
        "Either the volume hasn't dried up or there's no bullish OBV-price "
        "divergence. Both have to be present: dry-up tells us supply is "
        "exhausted near support; the divergence tells us smart money is "
        f"quietly buying. What we saw: {sr.reason}."
    )


def _br(sr: StageResult) -> str:
    """[BR] Breakout."""
    f = sr.features or {}
    if sr.passed:
        return (
            f"End-of-day breakout confirmed. Close {f.get('close', 0):.2f} is "
            f"{f.get('break_pct', 0):+.2f}% above the prior 20-day high "
            f"({f.get('resistance_20d', 0):.2f}), today's volume is "
            f"{f.get('vol_ratio_today_50d', 0):.2f}× the 50-day average "
            "(institutions participating), and price closed in the upper "
            f"{int(100 * (f.get('upper_third_ratio') or 0))}% of the day's "
            "range (no late-day pullback). Thrust is real."
        )
    return (
        "No clean breakout today. We require all three: close above the 20-day "
        "high, ≥1.5× average volume, and a close in the upper third of the "
        "candle. Any one missing means it's either a fake-out (no volume) or "
        f"a stalling thrust (weak close). What we saw: {sr.reason}."
    )


def _ingest(sr: StageResult) -> str:
    f = sr.features or {}
    if sr.passed:
        bars = f.get("bars", 0)
        return (
            f"Data loaded — {bars} daily bars available. "
            "Enough history for every downstream gate to compute its long-term lens."
        )
    return (
        f"Couldn't load enough history to evaluate the gates. {sr.reason}. "
        "Likely cause: ticker too new, or the data source returned a short window."
    )


def _universe(sr: StageResult) -> str:
    if sr.passed:
        return "Ticker is in the Nifty 100 universe — eligible for the strategy."
    return (
        "Ticker isn't in the Nifty 100 universe. The strategy is intentionally "
        "scoped to high-liquidity large-caps where the institutional volume "
        "signature is reliable. Mid- and small-caps will be added later, once "
        "the strategy has 6+ months of live outcomes."
    )


_EXPLAINERS = {
    "U": _universe,
    "I": _ingest,
    "HR": _hr,
    "LT": _lt,
    "CS": _cs,
    "VD": _vd,
    "BR": _br,
}


def explain_stage(sr: StageResult) -> str:
    """Return an English paragraph describing the stage outcome."""
    fn = _EXPLAINERS.get(sr.stage_id)
    if fn is None:
        # Fallback for stages without a hand-written template
        if sr.passed:
            return "Gate passed: " + "; ".join(sr.evidence or [])
        return "Gate failed: " + (sr.reason or "no reason given")
    try:
        return fn(sr)
    except Exception:
        return (sr.reason or "Gate evaluated.")
