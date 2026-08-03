"""Offline tests for the pre-breakout TAG guard (backend/pre_breakout_tag.py).

Covers the three user rules (2026-08-03):
  1. Self-Veto Override — any internal bearish flag disqualifies the tag.
  2. Unanimous Accumulation — coherent multi-timeframe flow, with the deliberate
     'healing' carve-out (negative-long / positive-short early bases stay eligible).
  3. Stealth Demand — right-edge up/down volume must prove active buying.

Plus the pure stealth_demand_ratio indicator. Presentation-only: the guard never
changes selection, so these are pure-function tests over synthetic payloads. No
network. Run: python -m unittest backend.tests.test_pre_breakout_tag -v
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backend import pre_breakout_tag as G
from backend.entry_stage_label import (
    BREAKOUT_CONFIRMED_TODAY,
    COILED_PRE_BREAKOUT,
    LATE_CHASE,
)
from backend.indicators import stealth_demand_ratio


def _payload(**over) -> dict:
    """A clean, eligible pre-breakout pick: coiled stage, all flow positive,
    strong right-edge demand, no contradictions. Override any field per test."""
    p = {
        "entry_stage": COILED_PRE_BREAKOUT,
        "early_accumulation": {"tier": "early", "features": {}},
        "accumulation_assessment": {"contradictions": [], "would_veto_shadow": False},
        "volume_event": {"direction": "bullish", "kind": "early_accumulation"},
        "flow_timeframes": {
            "obv_10d_norm_slope_pct": 8.0,
            "obv_30d_norm_slope_pct": 5.0,
            "obv_flow_inflection": "neutral",
            "obv_90d_norm_slope_pct": 12.0,
            "obv_180d_norm_slope_pct": 9.0,
            "up_down_vol_ratio_90d": 1.4,
        },
        "stealth_demand": {"ratio": 2.1, "in_dryup": True, "window": 10},
    }
    p.update(over)
    return p


class TestBaseline(unittest.TestCase):
    def test_clean_setup_is_eligible(self):
        r = G.assess_pre_breakout_tag(_payload())
        self.assertTrue(r["eligible"], r["reason"])
        self.assertEqual(r["self_veto"], [])
        self.assertEqual(r["flow_conflicts"], [])
        self.assertEqual(r["demand_conflicts"], [])

    def test_not_a_pre_breakout_setup(self):
        # A fresh confirmed breakout is not pre-breakout — ineligible, but with
        # NO veto/conflict (it's simply the wrong stage, not a disqualified one).
        r = G.assess_pre_breakout_tag(_payload(
            entry_stage=BREAKOUT_CONFIRMED_TODAY,
            early_accumulation={"tier": None, "features": {}},
        ))
        self.assertFalse(r["is_pre_breakout_setup"])
        self.assertFalse(r["eligible"])
        self.assertEqual(r["self_veto"], [])
        self.assertIn("not a pre-breakout setup", r["reason"])

    def test_never_raises_on_empty(self):
        r = G.assess_pre_breakout_tag({})
        self.assertFalse(r["eligible"])
        r2 = G.assess_pre_breakout_tag(None)  # type: ignore[arg-type]
        self.assertFalse(r2["eligible"])


class TestInstruction1SelfVeto(unittest.TestCase):
    def test_contradiction_disqualifies(self):
        r = G.assess_pre_breakout_tag(_payload(
            accumulation_assessment={
                "contradictions": ["distribution:dist_day_cluster:4"],
                "would_veto_shadow": True,
            },
        ))
        self.assertFalse(r["eligible"])
        self.assertTrue(any("distribution" in s for s in r["self_veto"]))

    def test_shadow_veto_alone_disqualifies(self):
        r = G.assess_pre_breakout_tag(_payload(
            accumulation_assessment={"contradictions": [], "would_veto_shadow": True},
        ))
        self.assertFalse(r["eligible"])
        self.assertIn("distribution-veto (shadow) fired", r["self_veto"])

    def test_bearish_volume_event_disqualifies(self):
        r = G.assess_pre_breakout_tag(_payload(
            volume_event={"direction": "bearish", "kind": "distribution"},
        ))
        self.assertFalse(r["eligible"])
        self.assertTrue(r["self_veto"])

    def test_internal_contradiction_early_but_extended(self):
        # One module says early-tier, another says the stage is LATE_CHASE.
        # Instruction 1: the bearish read wins.
        r = G.assess_pre_breakout_tag(_payload(
            entry_stage=LATE_CHASE,
            early_accumulation={"tier": "early", "features": {}},
        ))
        self.assertTrue(r["is_pre_breakout_setup"])   # early tier makes it a setup
        self.assertFalse(r["eligible"])               # ...but LATE_CHASE vetoes it
        self.assertTrue(any("extended/late/failed" in s for s in r["self_veto"]))


class TestInstruction2Flow(unittest.TestCase):
    def test_hemorrhaging_disqualifies(self):
        ft = _payload()["flow_timeframes"]
        ft.update(obv_10d_norm_slope_pct=-4.0, obv_30d_norm_slope_pct=-9.0,
                  obv_flow_inflection="hemorrhaging")
        r = G.assess_pre_breakout_tag(_payload(flow_timeframes=ft))
        self.assertFalse(r["eligible"])
        self.assertTrue(any("hemorrhaging" in s for s in r["flow_conflicts"]))

    def test_impossible_metric_disqualifies(self):
        ft = _payload()["flow_timeframes"]
        ft.update(obv_90d_norm_slope_pct=None)
        r = G.assess_pre_breakout_tag(_payload(flow_timeframes=ft))
        self.assertFalse(r["eligible"])
        self.assertTrue(any("impossible/missing metric" in s for s in r["flow_conflicts"]))

    def test_healing_carveout_keeps_early_base_eligible(self):
        # Long OBV still negative (window straddles the prior decline) but the
        # last ~2 weeks are turning up -> healing -> stays eligible by carve-out.
        ft = _payload()["flow_timeframes"]
        ft.update(obv_90d_norm_slope_pct=-6.0, obv_180d_norm_slope_pct=-3.0,
                  obv_30d_norm_slope_pct=-2.0, obv_10d_norm_slope_pct=+7.0,
                  obv_flow_inflection="healing", up_down_vol_ratio_90d=0.95)
        r = G.assess_pre_breakout_tag(_payload(flow_timeframes=ft))
        self.assertTrue(r["eligible"], r["reason"])
        self.assertTrue(r["healing_exemption"])

    def test_carveout_off_enforces_strict_unanimity(self):
        ft = _payload()["flow_timeframes"]
        ft.update(obv_90d_norm_slope_pct=-6.0, obv_180d_norm_slope_pct=-3.0,
                  obv_30d_norm_slope_pct=-2.0, obv_10d_norm_slope_pct=+7.0,
                  obv_flow_inflection="healing", up_down_vol_ratio_90d=0.95)
        r = G.assess_pre_breakout_tag(_payload(flow_timeframes=ft), healing_carveout=False)
        self.assertFalse(r["eligible"])
        self.assertFalse(r["healing_exemption"])
        self.assertTrue(any("long-term flow negative" in s for s in r["flow_conflicts"]))

    def test_negative_long_without_healing_disqualifies(self):
        # Long OBV negative and short NOT turning up -> not healing -> disqualified
        # even with the carve-out on.
        ft = _payload()["flow_timeframes"]
        ft.update(obv_90d_norm_slope_pct=-6.0, obv_10d_norm_slope_pct=-1.0,
                  obv_30d_norm_slope_pct=+1.0, obv_flow_inflection="neutral")
        r = G.assess_pre_breakout_tag(_payload(flow_timeframes=ft))
        self.assertFalse(r["eligible"])
        self.assertFalse(r["healing_exemption"])


class TestInstruction3StealthDemand(unittest.TestCase):
    def test_weak_right_edge_demand_disqualifies(self):
        r = G.assess_pre_breakout_tag(_payload(
            stealth_demand={"ratio": 0.7, "in_dryup": True, "window": 10},
        ))
        self.assertFalse(r["eligible"])
        self.assertTrue(any("no stealth demand" in s for s in r["demand_conflicts"]))
        self.assertIn("apathy", r["demand_conflicts"][0])

    def test_missing_demand_metric_disqualifies(self):
        r = G.assess_pre_breakout_tag(_payload(stealth_demand=None))
        self.assertFalse(r["eligible"])
        self.assertTrue(any("stealth-demand" in s for s in r["demand_conflicts"]))

    def test_strong_demand_passes(self):
        r = G.assess_pre_breakout_tag(_payload(
            stealth_demand={"ratio": 1.6, "in_dryup": True, "window": 10},
        ))
        self.assertTrue(r["eligible"], r["reason"])


class TestStealthDemandIndicator(unittest.TestCase):
    def _df(self, closes, vols) -> pd.DataFrame:
        idx = pd.date_range("2025-01-01", periods=len(closes), freq="B")
        return pd.DataFrame({
            "Open": closes, "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes], "Close": closes, "Volume": vols,
        }, index=idx)

    def test_none_on_short_history(self):
        df = self._df([100] * 10, [1000] * 10)
        self.assertIsNone(stealth_demand_ratio(df["Close"], df["Volume"]))

    def test_up_dominant_dryup_reads_stealth(self):
        rng = np.random.default_rng(0)
        closes = list(100 + rng.normal(0, 0.2, 60).cumsum())
        vols = [1_000_000] * 50  # baseline ADV
        # Right edge: quiet (dry-up) but up-days carry the volume.
        tail_closes, tail_vols = [], []
        c = closes[49]
        for i in range(10):
            up = i % 2 == 0
            c = c + (0.5 if up else -0.3)
            tail_closes.append(c)
            tail_vols.append(700_000 if up else 300_000)  # up-vol >> down-vol, < ADV
        closes = closes[:50] + tail_closes
        vols = vols + tail_vols
        df = self._df(closes, vols)
        out = stealth_demand_ratio(df["Close"], df["Volume"])
        self.assertIsNotNone(out)
        self.assertTrue(out["in_dryup"])            # mean tail volume < 0.8x ADV50
        self.assertGreater(out["ratio"], 1.5)       # up-day volume dominates

    def test_no_down_volume_saturates(self):
        closes = list(range(100, 160))              # every day up
        vols = [1_000_000] * 50 + [400_000] * 10
        df = self._df(closes, vols)
        out = stealth_demand_ratio(df["Close"], df["Volume"])
        self.assertEqual(out["ratio"], 5.0)         # no down-day volume -> saturates


if __name__ == "__main__":
    unittest.main()
