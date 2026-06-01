"""[CS] Consolidation gate.

Identifies stocks in a tight base above their 150-day moving average — the
institutional accumulation footprint from PRINCIPLES Section 2.

Three checks; all must pass:
  1. ATR(14) / Close <= 4 %                          (range is tight)
  2. days_within_band(close, +/-10 %) >= 25          (base mature; min 5 weeks)
  3. Close > 150d MA                                 (above the long-term floor)

NOTE: There is NO upper cap on consolidation duration. Long bases are
better. Stan Weinstein's Stage 1 bases run 6-18 months; Minervini's "perfect
base" can be even longer. A 25-day cap is the floor; the longer a base
holds at low ATR above the 150d MA, the higher the margin score (and
therefore the rank).

Margin scoring rewards bases up to ~90 days at full credit; longer than
that gets full credit too (institutional accumulation has compounded).

Fix points (top-of-file constants, all `# tunable`):
    ATR_PCT_MAX           : max tightness threshold
    MIN_DAYS_IN_BAND      : min consolidation duration (~5 weeks)
    BAND_PCT              : +/- price band defining "in range"
    MA_PERIOD             : long-term trend filter MA
    MARGIN_FULL_CREDIT_DAYS : in-band days that earn full duration margin
"""

from __future__ import annotations

from ..indicators import atr_pct, days_within_band, sma
from ..pipeline import PipelineContext, StageResult

stage_id = "CS"

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

ATR_PCT_MAX: float = 4.0                  # tunable
MIN_DAYS_IN_BAND: int = 25                # tunable (~5 weeks; the floor)
BAND_PCT: float = 0.10                    # tunable
MA_PERIOD: int = 150                      # tunable
MARGIN_FULL_CREDIT_DAYS: int = 90         # tunable; >=90 days in band = full margin


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    if df is None or df.empty or len(df) < MA_PERIOD + 1:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {MA_PERIOD + 1} bars)",
            fix_point="backend/stages/consolidation.py: ingest must produce >=151 bars",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    atr_max = float(overrides.get("cs_atr_pct_max", ATR_PCT_MAX))

    close = df["Close"]
    last_close = float(close.iloc[-1])

    atrp = atr_pct(df, 14)
    days_band = days_within_band(close, BAND_PCT)
    ma150 = sma(close, MA_PERIOD)

    features = {
        "last_close": round(last_close, 2),
        "atr_pct": round(atrp, 3) if atrp is not None else None,
        "days_in_band": days_band,
        "band_pct": BAND_PCT,
        "ma150": round(ma150, 2) if ma150 is not None else None,
        "above_ma150_pct": (
            round((last_close / ma150 - 1) * 100, 2) if ma150 else None
        ),
    }

    # ---- The three checks ----
    evidence: list[str] = []
    failures: list[str] = []

    if atrp is None:
        failures.append("ATR unavailable")
    elif atrp > atr_max:
        failures.append(f"ATR/price {atrp:.2f}% > {atr_max:.1f}% (range too wide)")
    else:
        evidence.append(f"ATR/price {atrp:.2f}% <= {atr_max:.1f}% (tight)")

    if days_band < MIN_DAYS_IN_BAND:
        failures.append(
            f"only {days_band} days in band (<{MIN_DAYS_IN_BAND} = base too young)"
        )
    else:
        evidence.append(
            f"{days_band} days in +/-{int(BAND_PCT*100)}% band "
            f"(>= {MIN_DAYS_IN_BAND} = base mature)"
        )

    if ma150 is None:
        failures.append("150d MA unavailable")
    elif last_close <= ma150:
        failures.append(
            f"close {last_close:.2f} <= 150d MA {ma150:.2f} (below institutional floor)"
        )
    else:
        evidence.append(
            f"close {last_close:.2f} > 150d MA {ma150:.2f} "
            f"({(last_close/ma150-1)*100:+.2f}%)"
        )

    # ---- Decision + margin score for ranker ----
    passed = len(failures) == 0
    margin = 0.0
    if passed and atrp is not None and ma150 is not None:
        # Tightness margin: how far below the (possibly overridden) max
        margin += max(0.0, (atr_max - atrp) / atr_max)
        # Above-MA margin: how far above 150d MA, capped at 10 % = full margin
        margin += min(1.0, max(0.0, (last_close / ma150 - 1) / 0.10))
        # Duration margin: 0.0 at MIN_DAYS_IN_BAND, 1.0 at MARGIN_FULL_CREDIT_DAYS+
        span = max(1, MARGIN_FULL_CREDIT_DAYS - MIN_DAYS_IN_BAND)
        margin += min(1.0, max(0.0, (days_band - MIN_DAYS_IN_BAND) / span))
        margin /= 3.0  # average of the three components, in [0, 1]

    return StageResult(
        stage_id=stage_id,
        passed=passed,
        score=round(margin, 4) if passed else 0.0,
        features=features,
        evidence=evidence if passed else failures,
        fix_point="backend/stages/consolidation.py — constants at top",
        reason=("passed all 3 checks" if passed else "; ".join(failures)),
    )
