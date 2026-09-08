"""Offline tests for the sequential BUY-readiness verdict (backend/buy_readiness.py).

Models the two incidents that motivated it:
  - Minda-like: a Stage-2 coil whose CMF/A-D are distributing while OBV looks fine
    -> must be AVOID, not a buy.
  - CG-Power-like: an impossible OBV slope (+3123%) -> DATA-INTEGRITY avoid.

Pure-function tests: hand-built payload dicts, no network, no data dir.
Run: python -m unittest backend.tests.test_buy_readiness -v
"""
from __future__ import annotations

import os
import unittest

from backend import buy_readiness as BR


def _payload(**over):
    """A clean Stage-2 coil that has NOT broken out (the baseline 'watch' case)."""
    p = {
        "symbol": "TEST.NS",
        "selection_tier": "confirmed",
        "confirmation": {
            "entry_timing": "mid",
            "weinstein_stage": "stage_2_advance",
            "selection_tier": "confirmed",
            "money_flow": {"cmf_21d": 0.10, "cmf_60d": 0.08, "ad_line_slope_pct": 12.0},
        },
        "flow_timeframes": {
            "up_down_vol_ratio_90d": 1.4,
            "obv_90d_norm_slope_pct": 8.0,
            "obv_180d_norm_slope_pct": 6.0,
        },
        "accumulation_assessment": {"contradictions": []},
        "gate_confirmation_status": {"passed": ["CS", "VD"], "failed": ["BR"]},
        "entry_stage_features": {"br_passed_today": False},
        "early_accumulation": {"features": {"durable_slow": True}},
        "price_plan": {"entry": 695.55},
    }
    # shallow-merge overrides (supports nested dicts one level deep)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(p.get(k), dict):
            p[k] = {**p[k], **v}
        else:
            p[k] = v
    return p


class BuyReadinessTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("STOCKYA_BUY_READINESS", None)

    def tearDown(self):
        os.environ.pop("STOCKYA_BUY_READINESS", None)

    def test_clean_coil_without_breakout_is_watch(self):
        v = BR.assess_buy_readiness(_payload())
        self.assertEqual(v["state"], "watch")
        self.assertEqual(v["category"], "setup_unconfirmed")
        self.assertTrue(v["layers"]["structure"]["pass"])
        self.assertTrue(v["layers"]["money_flow"]["pass"])
        self.assertFalse(v["layers"]["trigger"]["pass"])

    def test_full_stack_green_is_buy(self):
        v = BR.assess_buy_readiness(_payload(
            gate_confirmation_status={"passed": ["CS", "VD", "BR"], "failed": []},
            entry_stage_features={"br_passed_today": True},
        ))
        self.assertEqual(v["state"], "buy")
        self.assertTrue(all(v["layers"][k]["pass"] for k in v["layers"]))

    def test_distributing_coil_is_avoid_minda(self):
        # OBV/structure look fine, but CMF deeply negative + A/D falling.
        v = BR.assess_buy_readiness(_payload(
            confirmation={"money_flow": {"cmf_21d": -0.49, "ad_line_slope_pct": -150.0}},
        ))
        self.assertEqual(v["state"], "avoid")
        self.assertEqual(v["category"], "distribution")
        self.assertFalse(v["layers"]["money_flow"]["pass"])

    def test_distribution_contradiction_alone_is_avoid(self):
        v = BR.assess_buy_readiness(_payload(
            accumulation_assessment={"contradictions": [
                "money-flow distribution: CMF -0.20 — selling at the close"]},
        ))
        self.assertEqual(v["state"], "avoid")
        self.assertEqual(v["category"], "distribution")

    def test_absurd_obv_is_data_integrity_avoid_cgpower(self):
        v = BR.assess_buy_readiness(_payload(
            flow_timeframes={"obv_90d_norm_slope_pct": 3123.0},
        ))
        self.assertEqual(v["state"], "avoid")
        self.assertEqual(v["category"], "data_integrity")
        self.assertFalse(v["layers"]["data_integrity"]["pass"])

    def test_cmf_out_of_bounds_is_data_integrity(self):
        v = BR.assess_buy_readiness(_payload(
            confirmation={"money_flow": {"cmf_21d": 7.5}},
        ))
        self.assertEqual(v["category"], "data_integrity")

    def test_late_timing_is_avoid_structure(self):
        v = BR.assess_buy_readiness(_payload(
            confirmation={"entry_timing": "late"},
        ))
        self.assertEqual(v["state"], "avoid")
        self.assertFalse(v["layers"]["structure"]["pass"])

    def test_no_stage2_no_durable_is_avoid(self):
        v = BR.assess_buy_readiness(_payload(
            confirmation={"weinstein_stage": "stage_1_base"},
            early_accumulation={"features": {"durable_slow": False}},
        ))
        self.assertEqual(v["state"], "avoid")
        self.assertFalse(v["layers"]["structure"]["pass"])

    def test_lead_watch_tier_capped_to_watch_even_if_all_green(self):
        v = BR.assess_buy_readiness(_payload(
            selection_tier="lead_watch",
            confirmation={"selection_tier": "lead_watch"},
            gate_confirmation_status={"passed": ["CS", "VD", "BR"], "failed": []},
            entry_stage_features={"br_passed_today": True},
        ))
        self.assertEqual(v["state"], "watch")
        self.assertEqual(v["category"], "lead_watch")

    def test_disabled_returns_none(self):
        os.environ["STOCKYA_BUY_READINESS"] = "0"
        self.assertIsNone(BR.assess_buy_readiness(_payload()))

    def test_sequence_integrity_beats_distribution(self):
        # Both an absurd OBV AND deep-negative CMF -> integrity wins (checked first).
        v = BR.assess_buy_readiness(_payload(
            flow_timeframes={"obv_90d_norm_slope_pct": 5000.0},
            confirmation={"money_flow": {"cmf_21d": -0.49}},
        ))
        self.assertEqual(v["category"], "data_integrity")


if __name__ == "__main__":
    unittest.main()
