"""Regression tests for the final volatility and accumulation pick guards."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from backend.pipeline import (
    COMPOSITE_WEIGHTS,
    PipelineContext,
    PipelineResult,
    StageResult,
)
from backend.stages import accumulation
from backend.stages.rank import _selection_veto_reason, rank_lead_fallback
from backend.universe import VOLUME_UNIVERSE


def _rank_candidate(
    *,
    atr_pct: float,
    ac_passed: bool,
    durable_slow: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        confirmation_components={
            "entry_timing": "early",
            "day0_exit_watch": None,
            "early_accumulation": {
                "features": {"durable_slow": durable_slow},
            },
        },
        stage_results={
            "CS": StageResult(
                stage_id="CS",
                passed=True,
                features={"atr_pct": atr_pct},
            ),
            "AC": StageResult(stage_id="AC", passed=ac_passed),
        },
    )


class TestFinalPickQualityGuard(unittest.TestCase):
    def test_rejects_candidate_above_three_percent_atr(self) -> None:
        reason = _selection_veto_reason(
            _rank_candidate(atr_pct=3.01, ac_passed=True)
        )
        self.assertIn("high volatility", reason or "")

    def test_accepts_tight_candidate_with_direct_accumulation(self) -> None:
        reason = _selection_veto_reason(
            _rank_candidate(atr_pct=3.0, ac_passed=True)
        )
        self.assertIsNone(reason)

    def test_rejects_candidate_without_accumulation_evidence(self) -> None:
        reason = _selection_veto_reason(
            _rank_candidate(atr_pct=2.0, ac_passed=False, durable_slow=False)
        )
        self.assertIn("no confirmed accumulation", reason or "")

    def test_durable_slow_flow_can_confirm_when_ac_misses(self) -> None:
        reason = _selection_veto_reason(
            _rank_candidate(atr_pct=2.0, ac_passed=False, durable_slow=True)
        )
        self.assertIsNone(reason)


class TestStableVolumeAccumulation(unittest.TestCase):
    @staticmethod
    def _base(*, close_location: float) -> pd.DataFrame:
        """Flat, tight 180-bar base with exactly stable 1.00x volume."""
        n = 180
        low = np.full(n, 99.0)
        high = np.full(n, 101.0)
        close = low + (high - low) * close_location
        return pd.DataFrame(
            {
                "Open": np.full(n, 100.0),
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": np.full(n, 1_000_000.0),
            },
            index=pd.date_range("2025-01-01", periods=n, freq="B"),
        )

    def test_stable_volume_with_rising_adi_is_captured(self) -> None:
        ctx = PipelineContext(
            symbol="TEST.NS",
            trace_id="test",
            today_iso="2026-01-01",
            ohlcv=self._base(close_location=0.9),
        )

        result = accumulation.run(ctx)

        self.assertTrue(result.passed, result.evidence)
        self.assertEqual(result.features["vol_ratio"], 1.0)
        self.assertGreater(result.features["adi_slope"], 0.0)

    def test_stable_volume_without_rising_adi_still_fails(self) -> None:
        ctx = PipelineContext(
            symbol="TEST.NS",
            trace_id="test",
            today_iso="2026-01-01",
            ohlcv=self._base(close_location=0.5),
        )

        result = accumulation.run(ctx)

        self.assertFalse(result.passed)


# A stage that genuinely carries composite weight, so the confirmation ranking
# inside rank_lead_fallback is deterministic regardless of the live weight config.
_WEIGHTED_GID = next((g for g, w in COMPOSITE_WEIGHTS.items() if w and w > 0), "CS")


def _hard_survivor(
    symbol: str,
    *,
    composite: float,
    atr_pct: float = 2.0,
    ac_passed: bool = True,
    lead_score: float = 0.5,
) -> PipelineResult:
    """A hard-gate survivor double (ohlcv=None; guards fail-safe on no bars)."""
    cs = StageResult(
        stage_id="CS", passed=True, features={"atr_pct": atr_pct},
        score=(lead_score if _WEIGHTED_GID == "CS" else 0.0),
    )
    ac = StageResult(
        stage_id="AC", passed=ac_passed,
        score=(lead_score if _WEIGHTED_GID == "AC" else 0.0),
    )
    stages = {"CS": cs, "AC": ac}
    if _WEIGHTED_GID not in ("CS", "AC"):
        stages[_WEIGHTED_GID] = StageResult(
            stage_id=_WEIGHTED_GID, passed=True, score=lead_score
        )
    return PipelineResult(
        symbol=symbol, trace_id="t", passed_gates=True, composite_score=composite,
        selected=False, rank=None, stage_results=stages, pick_payload={},
    )


class TestGuaranteedLeadFallback(unittest.TestCase):
    """The day is never empty when a calm, accumulation-confirmed base coils
    just under the confirmation threshold — but the fallback keeps every quality
    floor except tau."""

    def setUp(self) -> None:
        self.sym_a, self.sym_b = VOLUME_UNIVERSE[0], VOLUME_UNIVERSE[1]

    def test_surfaces_best_below_tau_accumulation_confirmed_lead(self) -> None:
        strong = _hard_survivor(self.sym_a, composite=0.26, lead_score=0.9)
        weak = _hard_survivor(self.sym_b, composite=0.20, lead_score=0.1)

        lead = rank_lead_fallback([weak, strong])

        self.assertIsNotNone(lead)
        self.assertEqual(lead.symbol, self.sym_a)  # highest confirmation wins
        self.assertEqual(lead.confirmation_components["selection_tier"], "lead_watch")
        self.assertIn("Approaching confirmation", lead.confirmation_components["lead_note"])

    def test_high_volatility_lead_is_still_vetoed(self) -> None:
        # tau is relaxed in the fallback, but the ATR ceiling is NOT — a volatile
        # name that would whipsaw the next day never becomes the guaranteed lead.
        volatile = _hard_survivor(self.sym_a, composite=0.26, atr_pct=3.6)
        self.assertIsNone(rank_lead_fallback([volatile]))

    def test_no_accumulation_lead_is_still_vetoed(self) -> None:
        no_accum = _hard_survivor(self.sym_a, composite=0.26, ac_passed=False)
        self.assertIsNone(rank_lead_fallback([no_accum]))

    def test_empty_pool_returns_none(self) -> None:
        self.assertIsNone(rank_lead_fallback([]))

    def test_composite_floor_refuses_a_too_weak_lead(self) -> None:
        weakish = _hard_survivor(self.sym_a, composite=0.10)
        self.assertIsNone(rank_lead_fallback([weakish], min_composite=0.20))


if __name__ == "__main__":
    unittest.main()
