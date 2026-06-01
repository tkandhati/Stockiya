"""[HR] Hard Rejects — never alert when institutions are likely dumping.

The mirror of the [LT] accumulation check. Even if every other gate would
pass, these two patterns are textbook signs that institutions are unloading
into retail-driven buying — exactly what PRINCIPLES Section 6 says we never
alert on.

Two checks; FAILURE on either drops the ticker:

  1. Parabolic 30-day move: last 30-bar return > +25 %
     → Stocks up that fast in a month are where retail FOMOs in. Smart
       money sells into the buying. Don't be the bag-holder.

  2. Extended above 50d MA: close > 1.25 × 50d MA
     → Minervini's "extended" rule. Once price is 25 %+ above its 50d MA,
       the easy money has been made. Late-stage entries see sharper mean
       reversion when institutions thin positions.

Runs AFTER [I] Ingest (needs >=50 bars) and BEFORE the rest of the chain —
cheaper than re-evaluating LT/CS/VD/BR on tickers that should never alert.

Fix points:
    PARABOLIC_30D_MAX_PCT  : max 30-bar return allowed   (default 25.0)
    EXTENDED_VS_MA50_MAX   : max close / 50d MA ratio    (default 1.25)
"""

from __future__ import annotations

from ..indicators import sma
from ..pipeline import PipelineContext, StageResult

stage_id = "HR"

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

PARABOLIC_30D_MAX_PCT: float = 25.0       # tunable
PARABOLIC_LOOKBACK_BARS: int = 30         # tunable
EXTENDED_VS_MA50_MAX: float = 1.25        # tunable
MA50_PERIOD: int = 50                     # tunable


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    min_bars = max(PARABOLIC_LOOKBACK_BARS + 1, MA50_PERIOD + 1)
    if df is None or df.empty or len(df) < min_bars:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {min_bars} bars)",
            fix_point="backend/stages/hard_rejects.py: ingest must produce enough bars",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    parabolic_max = float(overrides.get("hr_parabolic_30d_max_pct", PARABOLIC_30D_MAX_PCT))
    extended_max = float(overrides.get("hr_extended_vs_ma50_max", EXTENDED_VS_MA50_MAX))

    close = df["Close"]
    last_close = float(close.iloc[-1])
    close_30d_ago = float(close.iloc[-(PARABOLIC_LOOKBACK_BARS + 1)])
    ret_30d_pct = (last_close / close_30d_ago - 1) * 100 if close_30d_ago > 0 else 0.0

    ma50 = sma(close, MA50_PERIOD)
    extended_ratio = (last_close / ma50) if ma50 and ma50 > 0 else None

    features = {
        "ret_30d_pct": round(ret_30d_pct, 2),
        "ma50": round(ma50, 2) if ma50 is not None else None,
        "close_over_ma50": round(extended_ratio, 3) if extended_ratio is not None else None,
        "extended_pct": (
            round((extended_ratio - 1) * 100, 2) if extended_ratio is not None else None
        ),
    }

    failures: list[str] = []
    evidence: list[str] = []

    if ret_30d_pct > parabolic_max:
        failures.append(
            f"30d return {ret_30d_pct:+.1f}% > {parabolic_max:.0f}% "
            f"(parabolic — institutions likely distributing to retail)"
        )
    else:
        evidence.append(
            f"30d return {ret_30d_pct:+.1f}% <= {parabolic_max:.0f}% "
            f"(no parabolic move)"
        )

    if extended_ratio is None:
        failures.append("50d MA unavailable")
    elif extended_ratio > extended_max:
        failures.append(
            f"close {(extended_ratio - 1) * 100:+.1f}% above 50d MA "
            f"(> {(extended_max - 1) * 100:.0f}% — extended, late-stage)"
        )
    else:
        evidence.append(
            f"close {(extended_ratio - 1) * 100:+.1f}% above 50d MA "
            f"(<= {(extended_max - 1) * 100:.0f}% — not extended)"
        )

    passed = len(failures) == 0
    return StageResult(
        stage_id=stage_id,
        passed=passed,
        features=features,
        evidence=evidence if passed else failures,
        fix_point="backend/stages/hard_rejects.py — constants at top",
        reason=("no distribution signals" if passed else "; ".join(failures)),
    )
