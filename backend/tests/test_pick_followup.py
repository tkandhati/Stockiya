"""Offline tests for the Pick Follow-up tracker (backend/pick_followup.py).

The tracker walks the open portfolio cohort, scores each name's accumulation
strength for every scan-day from the suggestion date to today (via the existing
accumulation_gauge historical spine), and classifies each row (coiling / firing
/ weakening / broke_down / watch / no-data). Presentation/monitoring-only: never
changes selection, so these are pure-function + monkeypatched-IO tests. No
network, no real data-dir dependency.

Run: python -m unittest backend.tests.test_pick_followup -v
"""
from __future__ import annotations

import os
import unittest

from backend import pick_followup as F


# A merged trace-feature bag that scores STRONG in the accumulation gauge
# (all three heavy components — 90d OBV, up/down-90d, ma150 slope — full credit).
_STRONG_FEAT = {
    "obv_90d_slope_pct": 8.0,
    "up_down_vol_ratio_90d": 1.4,
    "ma150_slope_pct": 1.0,
    "obv_slope_long_pct": 5.0,
    "current": 100.0,
}
# A bag that scores WEAK (institutions net-selling on the quarter).
_WEAK_FEAT = {
    "obv_90d_slope_pct": -40.0,
    "up_down_vol_ratio_90d": 0.7,
    "ma150_slope_pct": -2.0,
    "obv_slope_long_pct": -80.0,
    "current": 100.0,
}


class ClassifyTests(unittest.TestCase):
    # Signature: _classify(level_now, price_change_pct, strength_change, reached_expected)
    def test_coiling_is_building_volume_below_expected(self):
        # Strong flow, below the expected target — coiling regardless of the
        # exact price wiggle (up a little, down a little, choppy).
        self.assertEqual(F._classify(5, 0.0, 0.0, False), "coiling")
        self.assertEqual(F._classify(4, -5.0, 3.0, False), "coiling")
        self.assertEqual(F._classify(5, 6.0, 0.0, False), "coiling")   # up 6% but not at target
        # Level-3 counts only when strength is still rising.
        self.assertEqual(F._classify(3, 0.0, 5.0, False), "coiling")
        self.assertEqual(F._classify(3, 0.0, -1.0, False), "watch")

    def test_firing_when_expected_reached(self):
        self.assertEqual(F._classify(5, 15.0, 0.0, True), "firing")
        # reached_expected wins even with a modest % (target was close).
        self.assertEqual(F._classify(4, 4.0, 0.0, True), "firing")

    def test_broke_down_when_price_fell_through(self):
        self.assertEqual(F._classify(5, -20.0, 0.0, False), "broke_down")

    def test_weakening_when_flow_faded(self):
        self.assertEqual(F._classify(1, 0.0, 0.0, False), "weakening")
        self.assertEqual(F._classify(2, 2.0, 0.0, False), "weakening")
        # Strong level but strength collapsed since suggestion -> weakening.
        self.assertEqual(F._classify(4, 0.0, -30.0, False), "weakening")

    def test_no_data_when_unknown(self):
        self.assertEqual(F._classify(None, None, None, None), "no-data")
        self.assertEqual(F._classify(5, None, 0.0, False), "no-data")


class ConsolidationTests(unittest.TestCase):
    def _series(self, closes):
        return [{"date": f"2026-08-{i+1:02d}", "score": 80, "close": c} for i, c in enumerate(closes)]

    def test_tight_base_is_small(self):
        cons = F._consolidation(self._series([100, 102, 101, 103, 100]))
        self.assertEqual(cons["size"], "small")
        self.assertEqual(cons["days"], 5)

    def test_wide_base_is_big(self):
        cons = F._consolidation(self._series([100, 120, 90, 130, 100]))
        self.assertEqual(cons["size"], "big")

    def test_too_few_points_no_size(self):
        cons = F._consolidation(self._series([100, 101]))
        self.assertIsNone(cons["size"])


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        # Symbol has 3 trace dates: two deep (scorable), one shallow (only U/I).
        self._dates = ["2026-07-20", "2026-07-21", "2026-07-22"]
        self._stages = {
            "2026-07-20": {"LT": dict(_STRONG_FEAT)},
            "2026-07-21": {"LT": dict(_STRONG_FEAT, current=101.0)},
            "2026-07-22": {"U": {"in_universe": True}, "I": {"has_ohlcv": False}},  # shallow
        }
        self._orig_dates = F._all_trace_dates
        self._orig_read = F._read_trace_stages
        F._all_trace_dates = lambda sym: list(self._dates)
        F._read_trace_stages = lambda sym, d: self._stages.get(d, {})

    def tearDown(self):
        F._all_trace_dates = self._orig_dates
        F._read_trace_stages = self._orig_read

    def test_skips_days_before_since_and_shallow_days(self):
        series = F.accumulation_trajectory("X.NS", "2026-07-21")
        # 07-20 excluded (before since); 07-22 excluded (shallow / no score).
        self.assertEqual([p["date"] for p in series], ["2026-07-21"])
        self.assertEqual(series[0]["level"], 5)
        self.assertEqual(series[0]["close"], 101.0)

    def test_full_window_keeps_both_deep_days(self):
        series = F.accumulation_trajectory("X.NS", "2026-07-01")
        self.assertEqual([p["date"] for p in series], ["2026-07-20", "2026-07-21"])

    def test_points_carry_continuous_obv90(self):
        series = F.accumulation_trajectory("X.NS", "2026-07-01")
        # obv_90d_slope_pct from _STRONG_FEAT (8.0) is carried through as obv90.
        self.assertEqual(series[0]["obv90"], 8.0)
        self.assertEqual(series[0]["ud90"], 1.4)


class CoilQualityTests(unittest.TestCase):
    def test_strong_volume_flat_price_scores_high(self):
        q = F.coil_quality(obv90=30.0, ud90=1.4, price_change_pct=0.0)
        self.assertGreaterEqual(q["score"], 95)
        self.assertEqual(q["volume_add"], 1.0)
        self.assertEqual(q["price_stillness"], 1.0)

    def test_same_volume_but_price_moved_scores_lower(self):
        flat = F.coil_quality(30.0, 1.4, 0.0)["score"]
        moved = F.coil_quality(30.0, 1.4, 8.0)["score"]   # up 8% -> stillness spent
        self.assertLess(moved, flat)

    def test_weak_volume_scores_low(self):
        q = F.coil_quality(obv90=-50.0, ud90=0.7, price_change_pct=0.0)
        self.assertEqual(q["volume_add"], 0.0)
        self.assertLess(q["score"], 60)

    def test_no_volume_data_is_none(self):
        q = F.coil_quality(obv90=None, ud90=None, price_change_pct=0.0)
        self.assertIsNone(q["score"])


class TractionTests(unittest.TestCase):
    def test_breaking_out_when_above_pivot(self):
        t = F.assess_traction({"break_pct": 1.0, "resistance_20d": 100.0, "vol_ratio_today_50d": 1.5})
        self.assertEqual(t["level"], "breaking_out")
        self.assertEqual(t["distance_to_pivot_pct"], 0.0)

    def test_building_near_pivot_with_clues(self):
        t = F.assess_traction({
            "break_pct": -2.0, "resistance_20d": 100.0,
            "obv_flow_inflection": "healing",
            "vol_ratio_today_50d": 1.6, "anomaly_cluster_count_15d": 2,
        })
        self.assertEqual(t["level"], "building")
        self.assertEqual(t["distance_to_pivot_pct"], 2.0)
        self.assertGreaterEqual(len(t["clues"]), 2)

    def test_early_when_one_clue_far_from_pivot(self):
        t = F.assess_traction({
            "break_pct": -8.0, "resistance_20d": 100.0,
            "obv_slope_short_pct": 5.0, "obv_slope_long_pct": 3.0,
        })
        self.assertEqual(t["level"], "early")

    def test_quiet_when_no_clues(self):
        t = F.assess_traction({
            "break_pct": -8.0, "resistance_20d": 100.0,
            "obv_slope_short_pct": 1.0, "obv_slope_long_pct": 2.0,
            "vol_ratio_today_50d": 0.9, "upper_third_ratio": 0.2,
            "anomaly_cluster_count_15d": 0,
        })
        self.assertEqual(t["level"], "quiet")

    def test_unknown_without_trace(self):
        self.assertEqual(F.assess_traction({})["level"], "unknown")


class BuildTests(unittest.TestCase):
    def setUp(self):
        # One open portfolio row + a strong 2-day trajectory, flat price.
        self._orig_tracked = F._tracked_rows
        self._orig_traj = F.accumulation_trajectory
        self._orig_base = F._base_low_from_pick
        F._tracked_rows = lambda today: [{
            "symbol": "AAA.NS", "company": "Alpha", "pick_id": "P-0001",
            "entry_date": "2026-08-01", "entry_price": "100", "stop_price": "92",
            "status": "open", "ownership": "suggested",
        }]
        F.accumulation_trajectory = lambda sym, since: [
            {"date": "2026-08-01", "score": 85, "level": 5, "color": "#059669", "label": "STRONG",
             "close": 100.0, "obv90": 28.0, "ud90": 1.4},
            {"date": "2026-08-10", "score": 90, "level": 5, "color": "#059669", "label": "STRONG",
             "close": 100.5, "obv90": 30.0, "ud90": 1.4},
        ]
        F._base_low_from_pick = lambda sym, d: 96.0

    def tearDown(self):
        F._tracked_rows = self._orig_tracked
        F.accumulation_trajectory = self._orig_traj
        F._base_low_from_pick = self._orig_base
        os.environ.pop("STOCKYA_FOLLOWUP_WATCH", None)

    def test_row_shape_and_coiling_classification(self):
        rows = F.build_pick_followup("2026-08-29")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["symbol"], "AAA.NS")
        self.assertEqual(r["accum_now"]["score"], 90)
        self.assertEqual(r["accum_at_suggest"]["score"], 85)
        self.assertEqual(r["strength_change"], 5)
        # flat price (+0.5%), strong flow -> coiling
        self.assertAlmostEqual(r["price_change_pct"], 0.5, places=2)
        self.assertEqual(r["status"], "coiling")
        self.assertEqual(r["support1"], 96.0)          # base low
        self.assertEqual(r["support2"], 92.0)          # protective stop
        self.assertEqual(r["days_tracked"], 2)
        # Continuous coil quality computed + this row flagged the best pick.
        self.assertIsInstance(r["coil_score"], int)
        self.assertGreaterEqual(r["coil_score"], 90)
        self.assertEqual(r["obv90_now"], 30.0)
        self.assertTrue(r["is_top_pick"])
        self.assertTrue(r["volume_still_building"])   # OBV-90d positive and rising

    def test_env_gate_disables(self):
        os.environ["STOCKYA_FOLLOWUP_WATCH"] = "0"
        self.assertEqual(F.build_pick_followup("2026-08-29"), [])

    def test_never_raises_on_bad_rows(self):
        F._tracked_rows = lambda today: [{"symbol": None}, {}, {"symbol": "OK.NS", "entry_date": "2026-08-01"}]
        F.accumulation_trajectory = lambda sym, since: []
        # Must not raise even with junk rows / empty trajectory.
        rows = F.build_pick_followup("2026-08-29")
        self.assertIsInstance(rows, list)


if __name__ == "__main__":
    unittest.main()
