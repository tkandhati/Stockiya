"""Offline tests for the VPA smart-money read (backend/smart_money.py) and its
bounded tilt into the coiling coil-quality ranker (backend/pick_followup.py).

Pure-function tests in the style of test_pick_followup.py: hand-built trace-feature
bags + delivery-advisory dicts, no network, no data-dir dependency.

Run: python -m unittest backend.tests.test_smart_money -v
"""
from __future__ import annotations

import os
import unittest

from backend import pick_followup as F
from backend import smart_money as SM


def _adv(**kw) -> dict:
    """A delivery.delivery_advisory-shaped dict (available by default)."""
    base = {
        "available": True,
        "latest_pct": 55.0,
        "latest_date": "2026-08-28",
        "level": "moderate",
        "trend": "flat",
        "avg_5d": 55.0, "avg_15d": 54.0, "avg_20d": 53.0, "avg_30d": 52.0,
        "accum_streak_days": 0,
        "accum_drift": "flat",
        "accum_signal": 0.5,
    }
    base.update(kw)
    return base


def _keys(res: dict) -> set:
    return {s["key"] for s in res["signals"]}


class StructuralAccumulationTests(unittest.TestCase):
    def test_fires_on_moderate_volume_high_delivery_flat_price(self):
        res = SM.assess_smart_money(
            {"vol_ratio_5_50": 1.1},
            _adv(latest_pct=58.0, level="moderate", trend="rising"),
            price_change_pct=1.0, obv90=6.0, ud90=1.3,
        )
        self.assertIn("structural_accumulation", _keys(res))
        self.assertFalse(res["warning"])
        self.assertEqual(res["kind"], "bullish")
        self.assertGreater(res["confirmation"], 0.0)
        self.assertEqual(res["headline"], "Structural accumulation")

    def test_does_not_fire_on_volume_spike(self):
        # A big volume spike is NOT the moderate-volume Coulling read.
        res = SM.assess_smart_money(
            {"vol_ratio_5_50": 2.6},
            _adv(latest_pct=65.0, level="strong"),
            price_change_pct=1.0, obv90=6.0, ud90=1.3,
        )
        self.assertNotIn("structural_accumulation", _keys(res))

    def test_does_not_fire_once_price_has_moved(self):
        res = SM.assess_smart_money(
            {"vol_ratio_5_50": 1.1},
            _adv(latest_pct=62.0, level="strong"),
            price_change_pct=15.0, obv90=6.0, ud90=1.3,   # move already started
        )
        self.assertNotIn("structural_accumulation", _keys(res))

    def test_missing_volume_ratio_still_reads_from_delivery(self):
        # A rejected-day trace carries no volume ratio; delivery still carries it.
        res = SM.assess_smart_money(
            {}, _adv(latest_pct=63.0, level="strong"),
            price_change_pct=0.0, obv90=5.0, ud90=1.2,
        )
        self.assertIn("structural_accumulation", _keys(res))


class QuietAccumulationTests(unittest.TestCase):
    def test_fires_on_streak(self):
        res = SM.assess_smart_money(
            {}, _adv(latest_pct=45.0, level="moderate", accum_streak_days=4),
            price_change_pct=0.0, obv90=3.0, ud90=1.1,
        )
        self.assertIn("quiet_accumulation", _keys(res))

    def test_fires_on_rising_drift(self):
        res = SM.assess_smart_money(
            {}, _adv(latest_pct=45.0, level="moderate", accum_drift="rising"),
            price_change_pct=0.0, obv90=3.0, ud90=1.1,
        )
        self.assertIn("quiet_accumulation", _keys(res))


class NoSupplyTests(unittest.TestCase):
    def test_dryup_with_demand_is_absorption(self):
        res = SM.assess_smart_money(
            {"dry_up_streak_days_p25": 5}, None,
            price_change_pct=0.0, obv90=4.0, ud90=1.3,
        )
        self.assertIn("no_supply", _keys(res))
        self.assertEqual(res["kind"], "bullish")
        self.assertGreater(res["confirmation"], 0.0)

    def test_dryup_without_demand_is_apathy(self):
        res = SM.assess_smart_money(
            {"dry_up_streak_days_p25": 5, "up_down_vol_ratio_90d": 0.9}, None,
            price_change_pct=0.0, obv90=4.0,
        )
        self.assertIn("dry_up_apathy", _keys(res))
        self.assertNotIn("no_supply", _keys(res))
        # Apathy is neutral — earns no ranking credit.
        self.assertEqual(res["confirmation"], 0.0)


class DistributionWarningTests(unittest.TestCase):
    def test_weak_delivery_is_churn_warning(self):
        res = SM.assess_smart_money(
            {}, _adv(latest_pct=25.0, level="weak"),
            price_change_pct=0.0, obv90=5.0, ud90=1.2,
        )
        self.assertTrue(res["warning"])
        self.assertEqual(res["confirmation"], 0.0)
        self.assertEqual(res["headline"], "Distribution risk")

    def test_distribution_days_force_zero_confirmation_over_bullish(self):
        # Delivery looks structural, but 3 distribution days veto the boost.
        res = SM.assess_smart_money(
            {"vol_ratio_5_50": 1.1, "dist_day_count_15": 3},
            _adv(latest_pct=65.0, level="strong"),
            price_change_pct=0.0, obv90=5.0, ud90=1.3,
        )
        self.assertTrue(res["warning"])
        self.assertEqual(res["confirmation"], 0.0)
        self.assertNotIn("structural_accumulation", _keys(res))

    def test_hemorrhaging_and_negative_obv_warn(self):
        res = SM.assess_smart_money(
            {"obv_flow_inflection": "hemorrhaging"}, None,
            price_change_pct=0.0, obv90=-12.0,
        )
        self.assertTrue(res["warning"])


class ContractTests(unittest.TestCase):
    def test_confirmation_bounded_0_1(self):
        res = SM.assess_smart_money(
            {"vol_ratio_5_50": 1.0, "dry_up_streak_days_p25": 8},
            _adv(latest_pct=80.0, level="strong", trend="rising",
                 accum_streak_days=6, accum_drift="rising"),
            price_change_pct=0.0, obv90=9.0, ud90=1.5,
        )
        self.assertGreaterEqual(res["confirmation"], 0.0)
        self.assertLessEqual(res["confirmation"], 1.0)

    def test_empty_inputs_are_neutral(self):
        res = SM.assess_smart_money({}, None)
        self.assertEqual(res["signals"], [])
        self.assertEqual(res["confirmation"], 0.0)
        self.assertFalse(res["warning"])

    def test_disabled_by_env(self):
        os.environ["STOCKYA_SMART_MONEY"] = "0"
        try:
            res = SM.assess_smart_money(
                {"vol_ratio_5_50": 1.1}, _adv(latest_pct=65.0, level="strong"),
                price_change_pct=0.0, obv90=5.0, ud90=1.3,
            )
            self.assertEqual(res["signals"], [])
            self.assertEqual(res["confirmation"], 0.0)
        finally:
            os.environ.pop("STOCKYA_SMART_MONEY", None)


class CoilQualityTiltTests(unittest.TestCase):
    def test_zero_confirmation_is_unchanged(self):
        base = F.coil_quality(6.0, 1.1, 0.0)
        with_zero = F.coil_quality(6.0, 1.1, 0.0, smart_money_confirmation=0.0)
        self.assertEqual(base, with_zero)

    def test_bullish_confirmation_lifts_volume_add_bounded(self):
        base = F.coil_quality(6.0, 1.1, 0.0)                     # partial volume_add
        lifted = F.coil_quality(6.0, 1.1, 0.0, smart_money_confirmation=1.0)
        self.assertGreater(lifted["volume_add"], base["volume_add"])
        delta = lifted["volume_add"] - base["volume_add"]
        self.assertLessEqual(round(delta, 6), F.SMART_MONEY_MAX_TILT + 1e-9)

    def test_tilt_cannot_exceed_one(self):
        # Already-full volume_add stays clamped at 1.0 under a full tilt.
        q = F.coil_quality(30.0, 1.4, 0.0, smart_money_confirmation=1.0)
        self.assertEqual(q["volume_add"], 1.0)


if __name__ == "__main__":
    unittest.main()
