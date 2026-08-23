"""Offline tests for the monitor-stability changes:

  A. Trajectory OBV-90d prefers the zero-crossing-safe normalized metric when
     both entry and current traces carry it; falls back to the legacy metric
     otherwise (no regression for older positions).
  B. Missing price -> `data_unavailable` action (never a masquerading HOLD),
     while a hard stop still exits immediately.

No network. Run: python -m unittest backend.tests.test_monitor_stability -v
"""
from __future__ import annotations

import unittest

from backend.action_labels import DATA_UNAVAILABLE, action_label
from backend.positions_view import _action_for
from backend.signal_trajectory import _build_report


def _obv_indicator(report):
    for ind in report.indicators:
        if ind.name.startswith("obv_90d"):
            return ind
    return None


class TestTrajectoryObvPreference(unittest.TestCase):
    def _current(self, *, norm):
        cur = {
            "obv_90d_slope_pct": 55.0,
            "up_down_vol_ratio_90d": 1.6,
            "ma150_slope_pct": 1.0,
        }
        if norm is not None:
            cur["obv_90d_norm_slope_pct"] = norm
        return cur

    def test_prefers_norm_when_both_present(self):
        entry = {
            "obv_90d_slope_pct": 50.0,
            "obv_90d_norm_slope_pct": 20.0,
            "up_down_vol_ratio_90d": 1.5,
            "ma150_slope_pct": 0.8,
        }
        report = _build_report(
            entry_lt=entry, entry_vd={}, entry_br={},
            current=self._current(norm=25.0), trading_days_since_entry=0,
        )
        obv = _obv_indicator(report)
        self.assertEqual(obv.name, "obv_90d_norm_slope_pct")
        self.assertEqual(obv.entry_value, 20.0)
        self.assertEqual(obv.current_value, 25.0)

    def test_falls_back_to_legacy_when_norm_absent(self):
        entry = {  # legacy-only entry trace (pre-migration position)
            "obv_90d_slope_pct": 50.0,
            "up_down_vol_ratio_90d": 1.5,
            "ma150_slope_pct": 0.8,
        }
        report = _build_report(
            entry_lt=entry, entry_vd={}, entry_br={},
            current=self._current(norm=None), trading_days_since_entry=0,
        )
        obv = _obv_indicator(report)
        self.assertEqual(obv.name, "obv_90d_slope_pct")


class TestDataUnavailableAction(unittest.TestCase):
    def _kw(self, **over):
        kw = dict(
            close=100.0, entry=100.0, stop=92.0, t1=108.0, t2=116.0,
            hit_t1=False, days_held=1, shares_at_t1=0, shares_at_t2=1,
        )
        kw.update(over)
        return kw

    def test_missing_price_is_data_unavailable_not_hold(self):
        action, note, new_stop = _action_for(**self._kw(close=None))
        self.assertEqual(action, "data_unavailable")
        self.assertNotEqual(action, "hold")

    def test_hard_stop_still_immediate(self):
        action, _, _ = _action_for(**self._kw(close=91.0))  # <= stop 92
        self.assertEqual(action, "exit_stop")

    def test_label_maps_data_unavailable(self):
        self.assertEqual(action_label("hold", close_available=False), DATA_UNAVAILABLE)
        self.assertEqual(action_label("data_unavailable"), DATA_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
