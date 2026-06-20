"""[BR] Breakout gate — end-of-day trigger.

Proves institutions stepped in today. All three checks must pass on the
last EOD bar:

  1. Close > rolling 20d high of prior bars             (resistance break)
  2. Today's volume >= 1.3 x adv(50)                    (volume confirms)
  3. (close - low) / (high - low) >= 0.67               (upper-third close)

Together: a strong close through prior resistance on heavy volume with no
late-day pullback — the institutional fingerprint.

Fix points:
    RESISTANCE_LOOKBACK     : bars for prior resistance (default 20)
    VOLUME_BREAKOUT_MULT    : today / adv(50) min ratio (default 1.5)
    UPPER_THIRD_RATIO_MIN   : (close-low)/(high-low) min (default 0.67)
    ADV_WINDOW              : window for adv (default 50)
"""

from __future__ import annotations

from ..indicators import adv, last_bar_upper_third_ratio, rolling_high
from ..pipeline import PipelineContext, StageResult

stage_id = "BR"

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

RESISTANCE_LOOKBACK: int = 20          # tunable
VOLUME_BREAKOUT_MULT: float = 1.3      # tunable
UPPER_THIRD_RATIO_MIN: float = 0.67    # tunable
ADV_WINDOW: int = 50                   # tunable


def run(ctx: PipelineContext) -> StageResult:
    df = ctx.ohlcv
    min_bars = max(ADV_WINDOW, RESISTANCE_LOOKBACK + 1)
    if df is None or df.empty or len(df) < min_bars:
        return StageResult(
            stage_id=stage_id, passed=False,
            reason=f"insufficient history (need >= {min_bars} bars)",
            fix_point="backend/stages/breakout.py: ingest must produce enough bars",
        )

    overrides: dict = getattr(ctx, "overrides", {}) or {}
    vol_mult_min = float(overrides.get("br_volume_mult", VOLUME_BREAKOUT_MULT))

    last = df.iloc[-1]
    close = float(last["Close"])
    high = float(last["High"])
    low = float(last["Low"])
    vol_today = float(last["Volume"])

    resistance = rolling_high(df["High"], RESISTANCE_LOOKBACK, exclude_today=True)
    adv_long = adv(df["Volume"], ADV_WINDOW)
    upper_third = last_bar_upper_third_ratio(df)

    vol_ratio = vol_today / adv_long if adv_long and adv_long > 0 else None

    features = {
        "close": round(close, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "resistance_20d": round(resistance, 2) if resistance is not None else None,
        "break_pct": (
            round((close / resistance - 1) * 100, 2)
            if resistance and resistance > 0 else None
        ),
        "vol_today": round(vol_today, 0),
        "adv_50d": round(adv_long, 0) if adv_long is not None else None,
        "vol_ratio_today_50d": (
            round(vol_ratio, 3) if vol_ratio is not None else None
        ),
        "upper_third_ratio": (
            round(upper_third, 3) if upper_third is not None else None
        ),
    }

    evidence: list[str] = []
    failures: list[str] = []

    # ---- Check 1: resistance break ----
    if resistance is None:
        failures.append("resistance unavailable")
    elif close <= resistance:
        failures.append(
            f"close {close:.2f} <= 20d high {resistance:.2f} (no breakout)"
        )
    else:
        evidence.append(
            f"close {close:.2f} > 20d high {resistance:.2f} "
            f"({(close/resistance-1)*100:+.2f}%)"
        )

    # ---- Check 2: volume confirm ----
    if vol_ratio is None:
        failures.append("volume confirmation unavailable")
    elif vol_ratio < vol_mult_min:
        failures.append(
            f"volume {vol_ratio:.2f}x adv(50) < {vol_mult_min:.1f}x "
            f"(insufficient participation)"
        )
    else:
        evidence.append(
            f"volume {vol_ratio:.2f}x adv(50) >= {vol_mult_min:.1f}x"
        )

    # ---- Check 3: upper-third close ----
    if upper_third is None:
        failures.append("candle range unavailable (high == low)")
    elif upper_third < UPPER_THIRD_RATIO_MIN:
        failures.append(
            f"close at {upper_third*100:.0f}% of candle "
            f"(< {UPPER_THIRD_RATIO_MIN*100:.0f}% upper-third threshold)"
        )
    else:
        evidence.append(
            f"close at {upper_third*100:.0f}% of candle "
            f"(>= {UPPER_THIRD_RATIO_MIN*100:.0f}%)"
        )

    # ---- Decision + margin ----
    passed = len(failures) == 0
    margin = 0.0
    if passed and resistance and adv_long and vol_ratio is not None and upper_third is not None:
        # Break margin: 5 % above resistance = full credit
        break_margin = min(1.0, max(0.0, (close / resistance - 1) / 0.05))
        # Volume margin: 3 x adv(50) = full credit
        vol_margin = min(
            1.0, max(0.0, (vol_ratio - vol_mult_min) / 1.5)
        )
        # Upper-third margin: scaled into [0, 1]
        ut_margin = max(
            0.0,
            (upper_third - UPPER_THIRD_RATIO_MIN) / (1.0 - UPPER_THIRD_RATIO_MIN),
        )
        margin = (break_margin + vol_margin + ut_margin) / 3.0

    return StageResult(
        stage_id=stage_id,
        passed=passed,
        score=round(margin, 4) if passed else 0.0,
        features=features,
        evidence=evidence if passed else failures,
        fix_point="backend/stages/breakout.py — constants at top",
        reason=("passed all 3 checks" if passed else "; ".join(failures)),
    )
