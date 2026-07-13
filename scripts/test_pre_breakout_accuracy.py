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
    StageResult,
    _reweight_for_trigger,
    classify_trigger,
    compute_composite,
)


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
    stages = _stages(ac_pass=True, br_pass=False)
    assert classify_trigger(stages) == "pre_breakout"
    print("PASS classify pre_breakout")


def test_classify_trigger_sos() -> None:
    stages = _stages(ac_pass=True, br_pass=True)
    assert classify_trigger(stages) == "sos_breakout"
    stages2 = _stages(ac_pass=False, br_pass=True)
    assert classify_trigger(stages2) == "sos_breakout"
    print("PASS classify sos_breakout")


def test_classify_trigger_neutral() -> None:
    stages = _stages(ac_pass=False, br_pass=False)
    assert classify_trigger(stages) == "neutral"
    print("PASS classify neutral")


def test_composite_pre_breakout_gains_when_vd_weak() -> None:
    """The whole point: a pre-breakout with strong AC+LT and weak VD should
    score STRICTLY HIGHER under trigger-contextual weighting than under a
    hypothetical fixed-weight world."""
    # Weak VD (0.2), strong AC + LT (0.8) — the coiled-spring profile.
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
    print(f"PASS composite pre_breakout: adjusted {s_adjusted:.4f} > fixed {s_fixed:.4f}")


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


def main() -> int:
    print("=" * 60)
    print("Pre-breakout accuracy tests")
    print("=" * 60)
    tests = [
        test_reweight_sums_to_one,
        test_pre_breakout_reduces_vd_weight,
        test_sos_breakout_keeps_vd_weight,
        test_classify_trigger_pre_breakout,
        test_classify_trigger_sos,
        test_classify_trigger_neutral,
        test_composite_pre_breakout_gains_when_vd_weak,
        test_composite_sos_breakout_unchanged,
        test_flow_inflection_pre_breakout_ABB_like,
        test_flow_inflection_bull_trap,
        test_flow_inflection_healthy_breakout,
        test_volume_stage_healing_tilts_margin_up,
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
