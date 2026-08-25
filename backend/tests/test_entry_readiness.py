"""Offline tests for the entry-readiness router (backend/entry_readiness.py).

Main list = enterable today (entry_timing early/mid); everything else routes to
the awareness section with a category. No network.
Run: python -m unittest backend.tests.test_entry_readiness -v
"""
from __future__ import annotations

import os
import unittest

from backend import entry_readiness as er


def _p(symbol, timing, br_failed=True):
    return {
        "symbol": symbol,
        "gate_confirmation_status": {"failed": (["BR"] if br_failed else [])},
        "confirmation": {"entry_timing": timing, "weinstein_stage": "stage_2_advance"},
    }


class TestEntryReadiness(unittest.TestCase):
    def test_early_and_mid_are_enterable(self):
        self.assertIsNone(er.entry_readiness(_p("A.NS", "early")))
        self.assertIsNone(er.entry_readiness(_p("B.NS", "mid")))
        # A fresh breakout (BR passed) that is still mid is enterable too.
        self.assertIsNone(er.entry_readiness(_p("C.NS", "mid", br_failed=False)))

    def test_late_prebreakout_is_late_entry(self):
        r = er.entry_readiness(_p("POLYCAB.NS", "late"))
        self.assertIsNotNone(r)
        self.assertEqual(r["category"], "late_entry")

    def test_late_breakout_is_extended(self):
        r = er.entry_readiness(_p("OBEROIRLTY.NS", "late", br_failed=False))
        self.assertEqual(r["category"], "extended_breakout")

    def test_missed_is_distribution(self):
        self.assertEqual(er.entry_readiness(_p("D.NS", "missed"))["category"], "distribution")

    def test_unknown_is_timing_unclear(self):
        self.assertEqual(er.entry_readiness(_p("E.NS", "unknown"))["category"], "timing_unclear")
        # Missing entry_timing entirely -> treated as unknown -> awareness.
        self.assertEqual(
            er.entry_readiness({"symbol": "F.NS", "confirmation": {}})["category"],
            "timing_unclear",
        )


def _with_history(symbol, timing, prices, today_price):
    """A pre-breakout payload with a pick_history trail (newest-first prices)."""
    p = _p(symbol, timing)  # BR failing (pre-breakout)
    p["price_plan"] = {"entry": today_price}
    p["pick_history"] = [{"date": f"2026-08-{10 + i:02d}", "entry": pr}
                         for i, pr in enumerate(prices)]
    return p


class TestStaleBase(unittest.TestCase):
    def test_recurring_flat_prebreakout_is_stale(self):
        # 'mid' would normally be enterable, but a recurring flat base is stale.
        p = _with_history("INDUSINDBK.NS", "mid", [1005.0, 1010.0], today_price=1008.0)
        r = er.entry_readiness(p)
        self.assertIsNotNone(r)
        self.assertEqual(r["category"], "stale_base")

    def test_recurring_but_moving_is_not_stale(self):
        # Same recurrence, but +12% net move -> genuinely progressing -> enterable.
        p = _with_history("MOVER.NS", "mid", [900.0, 950.0], today_price=1008.0)
        self.assertIsNone(er.entry_readiness(p))

    def test_single_appearance_not_stale(self):
        p = _with_history("FRESH.NS", "mid", [1005.0], today_price=1006.0)
        self.assertIsNone(er.entry_readiness(p))


class TestSplit(unittest.TestCase):
    def setUp(self):
        os.environ.pop("STOCKYA_ENTERABLE_ONLY", None)

    def tearDown(self):
        os.environ.pop("STOCKYA_ENTERABLE_ONLY", None)

    def test_split_and_annotate(self):
        main, aware = er.split_enterable([_p("KEEP.NS", "early"), _p("MOVE.NS", "late")])
        self.assertEqual([p["symbol"] for p in main], ["KEEP.NS"])
        self.assertEqual([p["symbol"] for p in aware], ["MOVE.NS"])
        self.assertIn("not_actionable", aware[0])

    def test_env_flag_disables(self):
        os.environ["STOCKYA_ENTERABLE_ONLY"] = "0"
        main, aware = er.split_enterable([_p("MOVE.NS", "late")])
        self.assertEqual(len(main), 1)
        self.assertEqual(aware, [])


class TestReadiness(unittest.TestCase):
    def test_enterable_pick_gets_enter_badge(self):
        p = _p("A.NS", "early")
        r = er.stamp_readiness(p)
        self.assertTrue(r["enterable"])
        self.assertEqual(r["category"], "enterable")
        self.assertEqual(r["tone"], "enter")
        self.assertIn("early", r["label"])
        self.assertIs(p["readiness"], r)  # stamped onto the payload

    def test_awareness_pick_mirrors_category_and_tone(self):
        # Route it first so it carries not_actionable, then stamp.
        p = _p("MOVE.NS", "late")
        p["not_actionable"] = er.entry_readiness(p)   # category=late_entry
        r = er.stamp_readiness(p)
        self.assertFalse(r["enterable"])
        self.assertEqual(r["category"], "late_entry")
        self.assertEqual(r["tone"], "watch")
        self.assertTrue(r["why"])

    def test_distribution_is_avoid_tone(self):
        p = _p("D.NS", "missed")
        p["not_actionable"] = er.entry_readiness(p)   # category=distribution
        r = er.stamp_readiness(p)
        self.assertEqual(r["tone"], "avoid")

    def test_main_show_all_default_on_and_reversible(self):
        os.environ.pop("STOCKYA_MAIN_SHOW_ALL", None)
        self.assertTrue(er.main_show_all())
        os.environ["STOCKYA_MAIN_SHOW_ALL"] = "0"
        try:
            self.assertFalse(er.main_show_all())
        finally:
            os.environ.pop("STOCKYA_MAIN_SHOW_ALL", None)


if __name__ == "__main__":
    unittest.main()
