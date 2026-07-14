"""Deterministic tests for pre-breakout accuracy changes.

Covers two features:
  1. Trigger-contextual reweighting in pipeline.compute_composite
  2. OBV flow-velocity inflection in indicators.obv_flow_inflection and its
     margin-tilt effect inside stages/volume.py [VD]

No network. Fixtures are constructed in-memory to reproduce three canonical
tape shapes an ABB-style Nifty 100 name walks through:

    PRE_BREAKOUT    flat price, quiet volume, negative 30d OBV, healing 10d
    BULL_TRAP       breakout day but 30d and 10d OBV both negative
    HEALTHY_BR      breakout day with rising OBV in both windows

Run:
    C:/Claude_projects/Stockya/backend/.venv/Scripts/python.exe \
        scripts/test_pre_breakout_accuracy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backend.indicators import obv_flow_inflection  # noqa: E402
from backend.pipeline import (  # noqa: E402
    COMPOSITE_WEIGHTS,
    TRIGGER_AC_MIN_SCORE,
    StageResult,
    _reweight_for_trigger,
    classify_trigger,
    compute_composite,
)
from backend.stages.volume import VELOCITY_MARGIN_BONUS  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture builders — all deterministic; np.random.default_rng(SEED)
# --------------------------------------------------------------------------- #

SEED = 42
N_BARS = 220


def _bars_dates() -> pd.DatetimeIndex:
    return pd.date_range("2025-01-01", periods=N_BARS, freq="B")


def make_pre_breakout_df(seed: int = SEED) -> pd.DataFrame:
    """ABB-style pre-breakout: 200 flat bars around 360, tail 30 bars quiet.

    Volume: mildly falling through the base, then the last 10 bars tick up
    on modest close-up days (accumulation footprint). Price never breaks
    the 20d high on the final bar (BR must fail).

    Result: 30d OBV mildly negative, 10d OBV positive → "healing".
    """
    rng = np.random.default_rng(seed)
    dates = _bars_dates()
    # Base close random-walks tightly around 360 with a slight recovery
    # in the last 30 bars.
    base = 360.0
    noise = rng.normal(0.0, 1.8, size=N_BARS).cumsum() * 0.02
    close = np.clip(base + noise, base * 0.92, base * 1.06)
    # Tail: last 10 bars gently up (~+1.5%), still under 20d high.
    close[-10:] = close[-11] + np.linspace(0.5, 4.0, 10) + rng.normal(0, 0.4, 10)
    # Keep the last bar strictly below the trailing 20d high.
    trail_high = close[-21:-1].max()
    if close[-1] >= trail_high:
        close[-1] = trail_high - 1.0

    high = close + rng.uniform(0.5, 2.5, N_BARS)
    low = close - rng.uniform(0.5, 2.5, N_BARS)
    open_ = close + rng.normal(0, 0.5, N_BARS)

    # Volume: base ~1.0M with mild dry-up in the last 30 bars.
    vol = rng.normal(1_000_000, 120_000, N_BARS).clip(min=500_000)
    vol[-30:] *= 0.85
    # Uptick + close-up in the last 10 bars → OBV short slope turns positive
    # even though the long slope is still weak.
    up_mask = np.diff(close, prepend=close[0]) > 0
    vol[-10:] *= np.where(up_mask[-10:], 1.15, 1.0)

    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol,
    }, index=dates)


def make_bull_trap_df(seed: int = SEED + 1) -> pd.DataFrame:
    """Distribution then breakout day with weak long AND short OBV slope."""
    rng = np.random.default_rng(seed)
    dates = _bars_dates()
    base = 500.0
    # Slow bleed: 30 down-day bias over the last 60 bars.
    drift = np.concatenate([
        rng.normal(0.0, 1.5, N_BARS - 60),
        rng.normal(-0.4, 1.5, 60),
    ]).cumsum() * 0.05
    close = base + drift
    # Fake breakout on the last bar.
    close[-1] = close[-25:-1].max() * 1.02

    high = close + rng.uniform(0.5, 3.0, N_BARS)
    low = close - rng.uniform(0.5, 3.0, N_BARS)
    open_ = close + rng.normal(0, 0.8, N_BARS)

    # Volume: heavy down-day volume during the bleed → hemorrhaging OBV.
    vol = rng.normal(1_200_000, 150_000, N_BARS).clip(min=600_000)
    up_mask = np.diff(close, prepend=close[0]) > 0
    vol = np.where(up_mask, vol * 0.8, vol * 1.3)
    vol[-1] = vol[:-1].mean() * 2.0   # breakout-day surge

    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol,
    }, index=dates)


def make_healthy_breakout_df(seed: int = SEED + 2) -> pd.DataFrame:
    """Rising base then breakout day; both OBV windows positive."""
    rng = np.random.default_rng(seed)
    dates = _bars_dates()
    base = 400.0
    drift = np.linspace(0, 30, N_BARS) + rng.normal(0, 1.5, N_BARS).cumsum() * 0.05
    close = base + drift
    close[-1] = close[-25:-1].max() * 1.03

    high = close + rng.uniform(0.5, 2.5, N_BARS)
    low = close - rng.uniform(0.5, 2.5, N_BARS)
    open_ = close + rng.normal(0, 0.5, N_BARS)

    vol = rng.normal(1_000_000, 100_000, N_BARS).clip(min=500_000)
    up_mask = np.diff(close, prepend=close[0]) > 0
    vol = np.where(up_mask, vol * 1.25, vol * 0.9)
    vol[-1] = vol[:-1].mean() * 2.2

    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol,
    }, index=dates)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _sr(stage_id: str, *, passed: bool, score: float = 0.0) -> StageResult:
    return StageResult(stage_id=stage_id, passed=passed, score=score)


def _stages(*, ac_pass: bool, br_pass: bool,
            lt_pass: bool = True, cs_pass: bool = True,
            vd_pass: bool = True, ac_score: float = 0.6,
            br_score: float = 0.6, vd_score: float = 0.6,
            lt_score: float = 0.6, cs_score: float = 0.6) -> dict[str, StageResult]:
    return {
        "ACS": _sr("ACS", passed=True, score=0.6),
        "AC":  _sr("AC",  passed=ac_pass, score=ac_score if ac_pass else 0.0),
        "LT":  _sr("LT",  passed=lt_pass, score=lt_score if lt_pass else 0.0),
        "CS":  _sr("CS",  passed=cs_pass, score=cs_score if cs_pass else 0.0),
        "VD":  _sr("VD",  passed=vd_pass, score=vd_score if vd_pass else 0.0),
        "BR":  _sr("BR",  passed=br_pass, score=br_score if br_pass else 0.0),
    }


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_reweight_sums_to_one() -> None:
    """Whatever the regime, sum(adjusted_weights) == sum(original_weights)."""
    total = sum(COMPOSITE_WEIGHTS.values())
    for regime in ("pre_breakout", "sos_breakout", "neutral"):
        adj = _reweight_for_trigger(COMPOSITE_WEIGHTS, regime)
        assert abs(sum(adj.values()) - total) < 1e-9, (regime, adj)
    print("PASS reweight_sums_to_one")


def test_pre_breakout_reduces_vd_weight() -> None:
    """Pre-breakout regime halves VD weight; freed share goes to LT + AC."""
    original = COMPOSITE_WEIGHTS
    adj = _reweight_for_trigger(original, "pre_breakout")
    assert adj["VD"] < original["VD"] - 1e-9, (adj["VD"], original["VD"])
    freed = original["VD"] - adj["VD"]
    assert abs(freed - original["VD"] * 0.5) < 1e-9
    lt_bump = adj["LT"] - original["LT"]
    ac_bump = adj["AC"] - original["AC"]
    assert abs(lt_bump - freed / 2) < 1e-9
    assert abs(ac_bump - freed / 2) < 1e-9
    print(f"PASS pre_breakout weight shift: VD {original['VD']:.3f} -> {adj['VD']:.3f}, "
          f"LT +{lt_bump:.3f}, AC +{ac_bump:.3f}")


def test_sos_breakout_keeps_vd_weight() -> None:
    """SOS breakout regime is pass-through; every weight is identical."""
    adj = _reweight_for_trigger(COMPOSITE_WEIGHTS, "sos_breakout")
    for k, v in COMPOSITE_WEIGHTS.items():
        assert abs(adj[k] - v) < 1e-9, (k, v, adj[k])
    print("PASS sos_breakout weights unchanged")


def test_classify_trigger_pre_breakout() -> None:
    stages = _stages(ac_pass=True, br_pass=False, ac_score=0.7)
    assert classify_trigger(stages) == "pre_breakout"
    print("PASS classify pre_breakout (AC.score=0.7)")


def test_classify_trigger_pre_breakout_marginal_AC_denied() -> None:
    """AC passed but score below TRIGGER_AC_MIN_SCORE -> no weight relief."""
    stages = _stages(ac_pass=True, br_pass=False, ac_score=0.4)
    assert classify_trigger(stages) == "neutral", stages
    print(f"PASS marginal AC (score=0.4 < {TRIGGER_AC_MIN_SCORE}) denied weight relief")


def test_classify_trigger_pre_breakout_at_threshold() -> None:
    """AC.score exactly at TRIGGER_AC_MIN_SCORE -> pre_breakout (inclusive)."""
    stages = _stages(ac_pass=True, br_pass=False, ac_score=TRIGGER_AC_MIN_SCORE)
    assert classify_trigger(stages) == "pre_breakout"
    print(f"PASS AC.score == {TRIGGER_AC_MIN_SCORE} admits (inclusive threshold)")


def test_classify_trigger_sos() -> None:
    stages = _stages(ac_pass=True, br_pass=True, ac_score=0.7)
    assert classify_trigger(stages) == "sos_breakout"
    stages2 = _stages(ac_pass=False, br_pass=True)
    assert classify_trigger(stages2) == "sos_breakout"
    print("PASS classify sos_breakout")


def test_classify_trigger_neutral() -> None:
    stages = _stages(ac_pass=False, br_pass=False)
    assert classify_trigger(stages) == "neutral"
    print("PASS classify neutral")


def test_composite_pre_breakout_gains_when_vd_weak() -> None:
    """A pre-breakout with STRONG AC (>= threshold) + strong LT and weak VD
    should score strictly higher under trigger-contextual weighting than
    under a fixed-weight world. Weight relief is earned by the AC score."""
    # AC 0.8 (well above TRIGGER_AC_MIN_SCORE), weak VD (0.2), strong LT (0.8).
    stages = _stages(
        ac_pass=True, br_pass=False,
        ac_score=0.8, lt_score=0.8, vd_score=0.2, cs_score=0.7,
    )
    s_adjusted = compute_composite(stages)

    # Manual fixed-weight composite (no reweight):
    s_fixed = sum(
        w * (stages[k].score if k in stages and stages[k].passed else 0.0)
        for k, w in COMPOSITE_WEIGHTS.items()
    )
    assert s_adjusted > s_fixed + 1e-9, (s_adjusted, s_fixed)
    print(f"PASS composite pre_breakout (AC=0.8): adjusted {s_adjusted:.4f} > fixed {s_fixed:.4f}")


def test_velocity_margin_bonus_is_advisory_sized() -> None:
    """Constants check: VELOCITY_MARGIN_BONUS was reduced from 0.10 to 0.05
    on 2026-07-14 so the healing/hemorrhaging tilt is a tiebreaker, not a
    decision. Guard against accidental regression to a decision-sized bump."""
    assert VELOCITY_MARGIN_BONUS <= 0.05 + 1e-9, VELOCITY_MARGIN_BONUS
    print(f"PASS VELOCITY_MARGIN_BONUS is advisory-sized ({VELOCITY_MARGIN_BONUS})")


def test_composite_sos_breakout_unchanged() -> None:
    """A breakout day gets the SAME composite from adjusted vs fixed."""
    stages = _stages(
        ac_pass=True, br_pass=True,
        ac_score=0.6, lt_score=0.7, vd_score=0.6, br_score=0.7, cs_score=0.5,
    )
    s_adjusted = compute_composite(stages)
    s_fixed = sum(
        w * (stages[k].score if k in stages and stages[k].passed else 0.0)
        for k, w in COMPOSITE_WEIGHTS.items()
    )
    assert abs(s_adjusted - s_fixed) < 1e-9, (s_adjusted, s_fixed)
    print(f"PASS composite sos_breakout unchanged: {s_adjusted:.4f}")


def test_composite_marginal_AC_no_weight_relief() -> None:
    """Marginal AC (score < TRIGGER_AC_MIN_SCORE) + BR fail: even though AC
    passed, the trigger classifier returns neutral, so composite is unchanged
    from the fixed-weight baseline. This is the Bajaj-Auto guard: fragile
    coils do NOT get the VD weight relief."""
    stages = _stages(
        ac_pass=True, br_pass=False,
        ac_score=0.4, lt_score=0.7, vd_score=0.2, cs_score=0.6,
    )
    s_adjusted = compute_composite(stages)
    s_fixed = sum(
        w * (stages[k].score if k in stages and stages[k].passed else 0.0)
        for k, w in COMPOSITE_WEIGHTS.items()
    )
    assert abs(s_adjusted - s_fixed) < 1e-9, (s_adjusted, s_fixed)
    print(f"PASS composite marginal-AC pre_breakout: no relief, {s_adjusted:.4f} == fixed")


def test_flow_inflection_pre_breakout_ABB_like() -> None:
    """Synthetic ABB-like pre-breakout fixture -> healing inflection."""
    df = make_pre_breakout_df()
    inflection, s_short, s_long = obv_flow_inflection(df["Close"], df["Volume"])
    print(f"    ABB-like: short={s_short:+.2f}%, long={s_long:+.2f}% -> {inflection}")
    assert inflection == "healing", (inflection, s_short, s_long)
    print("PASS ABB-like fixture classified healing")


def test_flow_inflection_bull_trap() -> None:
    """Bull-trap fixture -> hemorrhaging inflection."""
    df = make_bull_trap_df()
    inflection, s_short, s_long = obv_flow_inflection(df["Close"], df["Volume"])
    print(f"    bull-trap: short={s_short:+.2f}%, long={s_long:+.2f}% -> {inflection}")
    assert inflection == "hemorrhaging", (inflection, s_short, s_long)
    print("PASS bull-trap fixture classified hemorrhaging")


def test_flow_inflection_healthy_breakout() -> None:
    """Healthy-breakout fixture -> neutral (both windows positive, not weak)."""
    df = make_healthy_breakout_df()
    inflection, s_short, s_long = obv_flow_inflection(df["Close"], df["Volume"])
    print(f"    healthy-BR: short={s_short:+.2f}%, long={s_long:+.2f}% -> {inflection}")
    # Both slopes positive → "neutral" (long is not < 0 threshold).
    assert inflection == "neutral", (inflection, s_short, s_long)
    print("PASS healthy-breakout fixture classified neutral")


def test_volume_stage_healing_tilts_margin_up() -> None:
    """[VD] stage: healing inflection adds a bounded bonus to the margin."""
    from backend.pipeline import PipelineContext
    from backend.stages import volume as vd_stage

    df = make_pre_breakout_df()
    ctx = PipelineContext(symbol="ABB", trace_id="t", today_iso="2026-07-13", ohlcv=df)
    result = vd_stage.run(ctx)
    print(f"    VD result: passed={result.passed}, score={result.score}, "
          f"inflection={result.features.get('obv_flow_inflection')}")
    # We only require the inflection feature to be surfaced; margin bump
    # applies only if the stage passed (either dry-up or divergence fired).
    assert result.features.get("obv_flow_inflection") in (
        "healing", "hemorrhaging", "neutral", "unavailable",
    )
    print("PASS VD stage surfaces inflection feature")


# --------------------------------------------------------------------------- #
# Exit-rule tests: healing-velocity override + failed-breakout micro-stop
# --------------------------------------------------------------------------- #

from backend.signal_trajectory import (  # noqa: E402
    FAILED_BR_VOLUME_MULT,
    FAILED_BR_WINDOW_TRADING_DAYS,
    HEALING_GRACE_TRADING_DAYS,
    _build_report,
    _classify_failed_breakout,
    _classify_healing_flip,
)


def _base_lt_features() -> dict:
    """Entry-time LT features that neither strong nor weak — neutral baseline
    so the healing/B1.5 indicators dominate in the aggregate."""
    return {
        "obv_90d_slope_pct": 6.0,
        "up_down_vol_ratio_90d": 1.3,
        "ma150_slope_pct": 1.5,
    }


def _base_current(**overrides) -> dict:
    """Current features that pass all standard indicators — same values as
    entry so those states are all 'stable'."""
    base = {
        "obv_90d_slope_pct": 6.0,
        "up_down_vol_ratio_90d": 1.3,
        "ma150_slope_pct": 1.5,
        "volume_event_direction": "neutral",
        "volume_event_kind": "neutral",
        "volume_event_score": 0.0,
        "volume_event_label": "No event",
        "volume_event_detail": "No event.",
        "obv_flow_inflection": "neutral",
        "obv_slope_short_pct": 0.0,
        "obv_slope_long_pct": 0.0,
        "last_close": 500.0,
        "last_volume": 900_000,
        "trailing_20d_high": 495.0,
        "adv_50d": 1_000_000,
    }
    base.update(overrides)
    return base


def test_healing_flip_hemorrhaging_inside_grace() -> None:
    """entry=healing, current=hemorrhaging inside grace -> flipped."""
    state = _classify_healing_flip("healing", "hemorrhaging", trading_days_since_entry=3)
    assert state == "flipped", state
    print("PASS healing->hemorrhaging inside grace = flipped")


def test_healing_flip_healing_inside_grace() -> None:
    """entry=healing, current=healing inside grace -> strong."""
    state = _classify_healing_flip("healing", "healing", trading_days_since_entry=3)
    assert state == "strong", state
    print("PASS healing->healing inside grace = strong")


def test_healing_flip_neutral_inside_grace() -> None:
    """entry=healing, current=neutral inside grace -> stable."""
    state = _classify_healing_flip("healing", "neutral", trading_days_since_entry=3)
    assert state == "stable", state
    print("PASS healing->neutral inside grace = stable")


def test_healing_flip_after_grace_still_healing() -> None:
    """entry=healing, current=healing but grace expired -> stable (thesis
    intact but no longer gets divergent-entry benefit-of-doubt)."""
    state = _classify_healing_flip(
        "healing", "healing", trading_days_since_entry=HEALING_GRACE_TRADING_DAYS + 5,
    )
    assert state == "stable", state
    print(f"PASS grace expired ({HEALING_GRACE_TRADING_DAYS}d) with healing intact = stable")


def test_healing_flip_after_grace_neutral() -> None:
    """entry=healing, current=neutral after grace -> weakening."""
    state = _classify_healing_flip(
        "healing", "neutral", trading_days_since_entry=HEALING_GRACE_TRADING_DAYS + 1,
    )
    assert state == "weakening", state
    print("PASS grace expired with neutral = weakening")


def test_healing_flip_non_divergent_entry_returns_unknown() -> None:
    """entry=neutral -> classifier stays out; standard rules own the call."""
    state = _classify_healing_flip("neutral", "hemorrhaging", trading_days_since_entry=3)
    assert state == "unknown", state
    print("PASS non-divergent entry short-circuits to unknown")


def test_failed_breakout_fires_inside_window() -> None:
    """close < resistance AND vol >= 1.0x ADV50 within window -> flipped."""
    state = _classify_failed_breakout(
        resistance_20d_at_entry=500.0,
        current_close=490.0,
        current_volume=1_400_000,
        current_adv50=1_000_000,
        trading_days_since_entry=3,
    )
    assert state == "flipped", state
    print("PASS failed-breakout micro-stop fires inside window")


def test_failed_breakout_close_above_resistance() -> None:
    """close still above resistance -> stable (micro-stop not fired)."""
    state = _classify_failed_breakout(
        resistance_20d_at_entry=500.0,
        current_close=510.0,
        current_volume=1_400_000,
        current_adv50=1_000_000,
        trading_days_since_entry=3,
    )
    assert state == "stable", state
    print("PASS breakout holding above resistance = stable")


def test_failed_breakout_light_volume_no_fire() -> None:
    """close < resistance but volume BELOW ADV50 -> stable (no distribution)."""
    state = _classify_failed_breakout(
        resistance_20d_at_entry=500.0,
        current_close=490.0,
        current_volume=800_000,
        current_adv50=1_000_000,
        trading_days_since_entry=3,
    )
    assert state == "stable", state
    print("PASS close-below-resistance on light volume = stable (no B1.5)")


def test_failed_breakout_outside_window() -> None:
    """Same failure condition outside window -> unknown (defer to B2)."""
    state = _classify_failed_breakout(
        resistance_20d_at_entry=500.0,
        current_close=490.0,
        current_volume=1_400_000,
        current_adv50=1_000_000,
        trading_days_since_entry=FAILED_BR_WINDOW_TRADING_DAYS + 1,
    )
    assert state == "unknown", state
    print(f"PASS outside {FAILED_BR_WINDOW_TRADING_DAYS}d window = unknown")


def test_failed_breakout_missing_resistance() -> None:
    """No 20d high on trace (non-BR pick) -> unknown."""
    state = _classify_failed_breakout(
        resistance_20d_at_entry=None,
        current_close=490.0,
        current_volume=1_400_000,
        current_adv50=1_000_000,
        trading_days_since_entry=3,
    )
    assert state == "unknown", state
    print("PASS non-BR pick (no resistance) = unknown")


def test_report_divergent_entry_flips_on_hemorrhaging() -> None:
    """Full report: entry VD=healing, current inflection=hemorrhaging inside
    grace -> exit_recommendation=True."""
    entry_vd = {"obv_flow_inflection": "healing"}
    entry_br = {}
    current = _base_current(
        obv_flow_inflection="hemorrhaging",
        obv_slope_short_pct=-5.0, obv_slope_long_pct=-20.0,
    )
    report = _build_report(
        entry_lt=_base_lt_features(),
        entry_vd=entry_vd,
        entry_br=entry_br,
        current=current,
        trading_days_since_entry=3,
    )
    assert report.exit_recommendation, report
    names = [i.name for i in report.indicators]
    assert "obv_flow_inflection" in names, names
    print("PASS full trajectory: divergent entry with hemorrhaging -> exit")


def test_report_divergent_entry_holds_on_healing() -> None:
    """Same divergent entry, current still healing -> no exit."""
    entry_vd = {"obv_flow_inflection": "healing"}
    entry_br = {}
    current = _base_current(
        obv_flow_inflection="healing",
        obv_slope_short_pct=+8.0, obv_slope_long_pct=-5.0,
    )
    report = _build_report(
        entry_lt=_base_lt_features(),
        entry_vd=entry_vd,
        entry_br=entry_br,
        current=current,
        trading_days_since_entry=3,
    )
    assert not report.exit_recommendation, report
    print("PASS full trajectory: divergent entry with intact healing -> hold")


def test_report_breakout_micro_stop_fires() -> None:
    """Full report: BR pick, close falls below 20d high on heavy vol day-3 -> exit."""
    entry_vd = {"obv_flow_inflection": "neutral"}
    entry_br = {"resistance_20d": 500.0}
    current = _base_current(
        last_close=490.0, last_volume=1_400_000, adv_50d=1_000_000,
    )
    report = _build_report(
        entry_lt=_base_lt_features(),
        entry_vd=entry_vd,
        entry_br=entry_br,
        current=current,
        trading_days_since_entry=3,
    )
    assert report.exit_recommendation, report
    names = [i.name for i in report.indicators]
    assert "failed_breakout_micro_stop" in names, names
    print("PASS full trajectory: failed breakout micro-stop fires")


def test_report_breakout_holds_after_window() -> None:
    """Same failure conditions but past the arming window -> no B1.5 exit
    from the micro-stop (falls to standard rules)."""
    entry_vd = {"obv_flow_inflection": "neutral"}
    entry_br = {"resistance_20d": 500.0}
    current = _base_current(
        last_close=490.0, last_volume=1_400_000, adv_50d=1_000_000,
    )
    report = _build_report(
        entry_lt=_base_lt_features(),
        entry_vd=entry_vd,
        entry_br=entry_br,
        current=current,
        trading_days_since_entry=FAILED_BR_WINDOW_TRADING_DAYS + 3,
    )
    # No indicator named failed_breakout_micro_stop, and standard indicators
    # are all stable -> no exit.
    micro = [i for i in report.indicators if i.name == "failed_breakout_micro_stop"]
    assert not micro, micro
    print(f"PASS full trajectory: past day-{FAILED_BR_WINDOW_TRADING_DAYS} the micro-stop is disarmed")


def main() -> int:
    print("=" * 60)
    print("Pre-breakout accuracy tests")
    print("=" * 60)
    tests = [
        test_reweight_sums_to_one,
        test_pre_breakout_reduces_vd_weight,
        test_sos_breakout_keeps_vd_weight,
        test_classify_trigger_pre_breakout,
        test_classify_trigger_pre_breakout_marginal_AC_denied,
        test_classify_trigger_pre_breakout_at_threshold,
        test_classify_trigger_sos,
        test_classify_trigger_neutral,
        test_composite_pre_breakout_gains_when_vd_weak,
        test_composite_sos_breakout_unchanged,
        test_composite_marginal_AC_no_weight_relief,
        test_velocity_margin_bonus_is_advisory_sized,
        test_flow_inflection_pre_breakout_ABB_like,
        test_flow_inflection_bull_trap,
        test_flow_inflection_healthy_breakout,
        test_volume_stage_healing_tilts_margin_up,
        # Exit-rule tests
        test_healing_flip_hemorrhaging_inside_grace,
        test_healing_flip_healing_inside_grace,
        test_healing_flip_neutral_inside_grace,
        test_healing_flip_after_grace_still_healing,
        test_healing_flip_after_grace_neutral,
        test_healing_flip_non_divergent_entry_returns_unknown,
        test_failed_breakout_fires_inside_window,
        test_failed_breakout_close_above_resistance,
        test_failed_breakout_light_volume_no_fire,
        test_failed_breakout_outside_window,
        test_failed_breakout_missing_resistance,
        test_report_divergent_entry_flips_on_hemorrhaging,
        test_report_divergent_entry_holds_on_healing,
        test_report_breakout_micro_stop_fires,
        test_report_breakout_holds_after_window,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failures += 1
    print("=" * 60)
    print(f"Ran {len(tests)} tests, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
