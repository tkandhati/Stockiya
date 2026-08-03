"""Offline tests for the 2026-08-03 exit-side changes:

  A. ATR-adaptive stop + R-based ladder in position_sizer (opt-in via atr_pct;
     fixed-8% model unchanged when atr_pct is None).
  B. Day-45 tighten to entry - 0.5R in positions_view._action_for.
  C. PRINCIPLES §5 exit-watch signals: distribution_day_count + avwap_breakdown
     indicators, and their integration into the trajectory report.

No network. Run: python -m unittest backend.tests.test_exit_layers -v
"""
from __future__ import annotations

import unittest

import pandas as pd

from backend.indicators import (
    anchored_vwap_from_low,
    avwap_breakdown,
    distribution_day_count,
)
from backend.position_sizer import size_position
from backend.positions_view import _action_for
from backend.signal_trajectory import _build_report


class TestAtrAdaptiveSizing(unittest.TestCase):
    def test_fixed_model_unchanged_when_no_atr(self):
        p = size_position(100_000, 100.0)
        self.assertAlmostEqual(p.stop, 92.0)
        self.assertAlmostEqual(p.t1, 108.0)
        self.assertAlmostEqual(p.t2, 116.0)

    def test_high_atr_widens_stop_and_scales_ladder(self):
        # ATR 6% -> 2x = 12% > 8% floor: stop 88, R = 12, T1 = +1R, T2 = +2R.
        p = size_position(100_000, 100.0, atr_pct=0.06)
        self.assertAlmostEqual(p.stop, 88.0)
        self.assertAlmostEqual(p.t1, 112.0)   # entry + 1R
        self.assertAlmostEqual(p.t2, 124.0)   # entry + 2R
        # Risk stays 1% of account: fewer shares because per-share risk is bigger.
        self.assertLessEqual(p.risk_pct_of_account, 0.01 + 1e-9)

    def test_low_atr_respects_8pct_floor(self):
        # ATR 2% -> 2x = 4% < 8% floor: stop stays at the 8% floor, fixed ladder.
        p = size_position(100_000, 100.0, atr_pct=0.02)
        self.assertAlmostEqual(p.stop, 92.0)
        self.assertAlmostEqual(p.t1, 108.0)
        self.assertAlmostEqual(p.t2, 116.0)


class TestDay45HalfR(unittest.TestCase):
    def _day45(self, *, entry, stop, t1, t2):
        return _action_for(
            close=entry, entry=entry, stop=stop, t1=t1, t2=t2,
            hit_t1=False, days_held=50, shares_at_t1=5, shares_at_t2=5,
        )

    def test_eight_pct_stop_tightens_to_minus_4pct(self):
        action, _note, new_stop = self._day45(entry=100.0, stop=92.0, t1=108.0, t2=116.0)
        self.assertEqual(action, "tighten_stop_45")
        self.assertAlmostEqual(new_stop, 96.0)   # (100+92)/2 = 0.5R = entry-4%

    def test_wide_stop_tightens_to_half_r_not_flat_4pct(self):
        # A 15% stop -> 0.5R = entry-7.5% = 92.5, NOT the old flat entry-4% (96).
        action, _note, new_stop = self._day45(entry=100.0, stop=85.0, t1=115.0, t2=130.0)
        self.assertEqual(action, "tighten_stop_45")
        self.assertAlmostEqual(new_stop, 92.5)


class TestDistributionDayCount(unittest.TestCase):
    # ADV-relative rule (2026-08-04): a distribution day is a down-close on volume
    # ABOVE the trailing 20d average — NOT merely above the prior day (that loose
    # form tripped >= 3 on a majority of stocks). ADV is measured on the prefix
    # before the 15-bar window; need >= 15 + 20 + 1 = 36 bars.
    def test_counts_only_heavy_volume_downcloses(self):
        prefix_c, prefix_v = [100.0] * 45, [1000.0] * 45          # adv20 -> 1000
        win_c = [99, 100, 99, 100, 99, 100, 99, 100, 100, 100, 100, 100, 100, 100, 100]
        win_v = [1500, 1000, 1500, 1000, 1500, 1000, 800, 1000, 1000, 1000, 1000,
                 1000, 1000, 1000, 1000]
        closes = pd.Series(prefix_c + win_c)
        vols = pd.Series(prefix_v + win_v)
        # 3 down-closes on 1500 (> adv 1000); the 4th down-close is on 800 (< adv)
        # and must NOT count.
        self.assertEqual(distribution_day_count(closes, vols, lookback=15), 3)

    def test_light_volume_downcloses_not_counted(self):
        # Every down-close is on volume ABOVE the PRIOR day but BELOW the 20d avg.
        # The old (prior-day) rule would have counted these; the ADV rule must not.
        prefix_c, prefix_v = [100.0] * 45, [2000.0] * 45          # adv20 -> 2000
        win_c = [99, 100] * 7 + [99]
        win_v = [1100, 1050] * 7 + [1100]                          # all < adv 2000
        closes = pd.Series(prefix_c + win_c)
        vols = pd.Series(prefix_v + win_v)
        self.assertEqual(distribution_day_count(closes, vols, lookback=15), 0)

    def test_short_history_zero(self):
        self.assertEqual(
            distribution_day_count(pd.Series([100.0] * 20), pd.Series([1000.0] * 20),
                                   lookback=15),
            0,
        )


class TestAvwapBreakdown(unittest.TestCase):
    def _df(self, closes):
        return pd.DataFrame({
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1000] * len(closes),
        })

    def test_two_closes_below_after_holding_breaks(self):
        # Base low 100, ran to 120 (AVWAP settles ~118), then pulled back to 108:
        # below the cost basis but ABOVE the base low, so the anchor stays put.
        closes = [100] + [120] * 20 + [108, 108]
        out = avwap_breakdown(self._df(closes), anchor_lookback=90, confirm=2)
        self.assertIsNotNone(out)
        self.assertTrue(out["broke"])
        self.assertLess(out["last_close"], out["avwap"])

    def test_holding_above_does_not_break(self):
        closes = [100] + [120] * 20 + [121, 121]       # still above the AVWAP
        out = avwap_breakdown(self._df(closes), anchor_lookback=90, confirm=2)
        self.assertIsNotNone(out)
        self.assertFalse(out["broke"])

    def test_anchor_is_the_lowest_close(self):
        # Lowest close is bar 0 (=100); AVWAP is computed forward from there.
        av = anchored_vwap_from_low(self._df([100] + [110] * 10), anchor_lookback=90)
        self.assertIsNotNone(av)
        self.assertGreater(av, 100.0)


class TestTrajectoryExitWatchIntegration(unittest.TestCase):
    _STABLE_LT = {
        "obv_90d_slope_pct": 6.0,
        "up_down_vol_ratio_90d": 1.3,
        "ma150_slope_pct": 1.5,
    }

    def _current(self, **over):
        base = dict(self._STABLE_LT)
        base.update(over)
        return base

    def test_distribution_cluster_flips_report(self):
        report = _build_report(
            entry_lt=self._STABLE_LT, entry_vd={}, entry_br={},
            current=self._current(distribution_day_count_15=4),
            trading_days_since_entry=10,
        )
        names = [i.name for i in report.indicators]
        self.assertIn("distribution_day_count", names)
        self.assertTrue(report.exit_recommendation)

    def test_avwap_breakdown_flips_report(self):
        report = _build_report(
            entry_lt=self._STABLE_LT, entry_vd={}, entry_br={},
            current=self._current(
                avwap_breakdown={"avwap": 110.0, "confirm": 2, "broke": True,
                                 "last_close": 90.0},
            ),
            trading_days_since_entry=10,
        )
        names = [i.name for i in report.indicators]
        self.assertIn("avwap_breakdown", names)
        self.assertTrue(report.exit_recommendation)

    def test_below_threshold_does_not_flip(self):
        report = _build_report(
            entry_lt=self._STABLE_LT, entry_vd={}, entry_br={},
            current=self._current(
                distribution_day_count_15=2,
                avwap_breakdown={"avwap": 110.0, "confirm": 2, "broke": False,
                                 "last_close": 115.0},
            ),
            trading_days_since_entry=10,
        )
        names = [i.name for i in report.indicators]
        self.assertNotIn("distribution_day_count", names)
        self.assertNotIn("avwap_breakdown", names)
        self.assertFalse(report.exit_recommendation)


if __name__ == "__main__":
    unittest.main()
