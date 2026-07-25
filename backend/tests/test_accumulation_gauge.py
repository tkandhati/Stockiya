"""Deterministic, offline unit tests for the accumulation gauge + freshness.

Run:  python -m unittest backend.tests.test_accumulation_gauge -v

No network, no LLM, no disk. Every input is hand-constructed at numeric
boundaries so the mapping is auditable and re-runs are byte-identical.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import backend.accumulation_gauge as G
from backend import day_freshness as F

IST = ZoneInfo("Asia/Kolkata")


def _pos(overall="stable", *, action_label="MAINTAIN_HEALTHY", entry_stage="",
         current=110.0, stop=100.0, flip=False):
    return {
        "trajectory": {"overall": overall, "exit_recommendation": flip},
        "action_label": action_label,
        "entry_stage": entry_stage,
        "current_price": current,
        "stop_price": stop,
        "trajectory_flip": flip,
    }


class TestLiveSpine(unittest.TestCase):
    def test_overall_maps_to_five_levels(self):
        cases = {
            "strong": 5, "stable": 4, "unknown": 3,
            "weakening": 2, "flipped": 1,
        }
        for overall, want in cases.items():
            g = G.gauge_from_position(_pos(overall, action_label=""))
            self.assertEqual(g["level"], want, f"{overall} -> {g['level']}")

    def test_flip_forces_red(self):
        g = G.gauge_from_position(_pos("strong", flip=True))
        self.assertEqual(g["level"], 1)
        self.assertEqual(g["label"], "FLIPPED")

    def test_exit_label_forces_red(self):
        g = G.gauge_from_position(_pos("stable", action_label="EXIT_STOP"))
        self.assertEqual(g["level"], 1)

    def test_close_at_or_below_stop_forces_red(self):
        g = G.gauge_from_position(_pos("strong", current=99.0, stop=100.0))
        self.assertEqual(g["level"], 1)

    def test_review_caps_at_warning(self):
        g = G.gauge_from_position(
            _pos("stable", action_label="REVIEW_WEAKNESS_CONFIRMED"))
        self.assertEqual(g["level"], 2)

    def test_monitor_caps_at_caution(self):
        g = G.gauge_from_position(
            _pos("strong", action_label="MONITOR_EARLY_WEAKNESS"))
        self.assertEqual(g["level"], 3)

    def test_extend_confirms_strong(self):
        g = G.gauge_from_position(_pos("stable", action_label="EXTEND_5D"))
        self.assertEqual(g["level"], 5)

    def test_coiled_confirms_strong(self):
        g = G.gauge_from_position(
            _pos("stable", action_label="", entry_stage="COILED_PRE_BREAKOUT"))
        self.assertEqual(g["level"], 5)

    def test_extend_does_not_rescue_weakening(self):
        # base=2 (weakening); EXTEND only lifts >=4, so it must stay low.
        g = G.gauge_from_position(_pos("weakening", action_label="EXTEND_5D"))
        self.assertLess(g["level"], 4)

    def test_deterministic(self):
        p = _pos("stable")
        self.assertEqual(G.gauge_from_position(p), G.gauge_from_position(p))

    def test_contract_keys_present(self):
        g = G.gauge_from_position(_pos("stable"))
        for k in ("level", "color", "label", "message", "buffer_bucket",
                  "buffer_text", "buffer_sessions", "headroom_pct", "atr_pct",
                  "reviews_per_day", "reasons", "source", "score", "as_of"):
            self.assertIn(k, g)
        self.assertEqual(g["source"], "live")


class TestBuffer(unittest.TestCase):
    def test_sessions_math(self):
        # 10% headroom / 2% atr = 5 sessions.
        self.assertAlmostEqual(
            G.buffer_sessions(close=100.0, stop=90.0, atr_pct=2.0), 5.0, places=6)

    def test_through_stop_is_zero(self):
        self.assertEqual(G.buffer_sessions(close=90.0, stop=95.0, atr_pct=2.0), 0.0)

    def test_missing_atr_is_none(self):
        self.assertIsNone(G.buffer_sessions(close=100.0, stop=90.0, atr_pct=None))

    def test_buckets_by_sessions(self):
        # wide buffer -> can skip several; thin -> act now.
        wide = G.gauge_from_position(
            _pos("strong", current=100.0, stop=80.0), atr_pct=2.0)   # 10 sessions
        self.assertEqual(wide["buffer_bucket"], "SKIP_SEVERAL")
        thin = G.gauge_from_position(
            _pos("stable", current=100.0, stop=98.0), atr_pct=4.0)   # 0.5 sessions
        self.assertEqual(thin["buffer_bucket"], "THIS_REVIEW")

    def test_red_level_always_act_now(self):
        g = G.gauge_from_position(
            _pos("strong", current=100.0, stop=80.0, flip=True), atr_pct=2.0)
        self.assertEqual(g["level"], 1)
        self.assertEqual(g["buffer_bucket"], "ACT_NOW")

    def test_cadence_text_is_two_per_day(self):
        self.assertEqual(G.REVIEWS_PER_DAY, 2)


class TestHistoricalScore(unittest.TestCase):
    def test_all_strong_features_score_high(self):
        feat = {
            "obv_90d_slope_pct": 8.0,
            "up_down_vol_ratio_90d": 1.4,
            "ma150_slope_pct": 5.0,
            "obv_slope_long_pct": 3.0,
            "cmf_21d": 0.2,
            "atr_pct": 2.0,
        }
        g = G.gauge_from_trace_features(feat, close=100.0, stop=90.0, entry=92.0)
        self.assertEqual(g["level"], 5)
        self.assertEqual(g["score"], 100)
        self.assertEqual(g["source"], "historical")

    def test_all_weak_features_score_low(self):
        feat = {
            "obv_90d_slope_pct": -5.0,
            "up_down_vol_ratio_90d": 0.7,
            "ma150_slope_pct": -3.0,
            "obv_slope_long_pct": -5.0,
            "cmf_21d": -0.1,
            "atr_pct": 3.0,
        }
        g = G.gauge_from_trace_features(feat, close=100.0, stop=95.0, entry=98.0)
        self.assertEqual(g["level"], 1)
        self.assertEqual(g["score"], 0)

    def test_renormalizes_over_present_features(self):
        # Only two features present, both strong -> should still score 100.
        feat = {"obv_90d_slope_pct": 8.0, "up_down_vol_ratio_90d": 1.4, "atr_pct": 2.0}
        g = G.gauge_from_trace_features(feat, close=100.0, stop=90.0)
        self.assertEqual(g["score"], 100)
        self.assertEqual(g["level"], 5)

    def test_no_features_defaults_caution(self):
        g = G.gauge_from_trace_features({}, close=100.0, stop=90.0)
        self.assertEqual(g["level"], 3)
        self.assertIsNone(g["score"])

    def test_through_stop_forces_red_regardless_of_score(self):
        feat = {"obv_90d_slope_pct": 8.0, "up_down_vol_ratio_90d": 1.4, "atr_pct": 2.0}
        g = G.gauge_from_trace_features(feat, close=89.0, stop=90.0)
        self.assertEqual(g["level"], 1)


class TestFreshness(unittest.TestCase):
    """Single-cutoff rule: before 16:00 IST always refresh; at/after, frozen."""

    def _at(self, h, m):
        return datetime(2026, 7, 24, h, m, tzinfo=IST)

    def test_before_cutoff_regenerates(self):
        self.assertFalse(F.is_frozen(self._at(9, 30)))
        self.assertFalse(F.should_serve_cache(self._at(15, 59)))

    def test_at_and_after_cutoff_frozen(self):
        self.assertTrue(F.is_frozen(self._at(16, 0)))     # boundary inclusive
        self.assertTrue(F.should_serve_cache(self._at(16, 0)))
        self.assertTrue(F.should_serve_cache(self._at(17, 30)))

    def test_naive_datetime_assumed_ist(self):
        self.assertFalse(F.is_frozen(datetime(2026, 7, 24, 9, 0)))
        self.assertTrue(F.is_frozen(datetime(2026, 7, 24, 18, 0)))


if __name__ == "__main__":
    unittest.main()
