"""Selection vetoes that keep unsuitable-to-BUY setups out of the top-N:

  - Self-Veto Override (2026-08-03): entry_timing == "missed" (Stage 4 /
    distribution — "exit zone, not entry").
  - Day-0 coherence gate (2026-08-04): a setup that would IMMEDIATELY trip the
    exit monitor (distribution-day cluster / AVWAP breakdown) must not be shown
    as a buy — so the picks page and positions page agree on day 0.

Covers the pure partition used by rank_survivors, and confirms volume_signals
labels a clear downtrend "missed". No network.
Run: python -m unittest backend.tests.test_missed_exclusion -v
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from backend.stages.rank import _partition_selectable, _selection_veto_reason
from backend.volume_signals import compute as compute_volume_signals

_MISSING = object()


def _survivor(symbol: str, timing=_MISSING, *, day0=None):
    """Minimal stand-in for a PipelineResult carrying selection-veto inputs."""
    comps = {}
    if timing is not _MISSING:
        comps["entry_timing"] = timing
    if day0 is not None:
        comps["day0_exit_watch"] = day0
    return SimpleNamespace(
        symbol=symbol,
        confirmation_components=(comps if comps else None),
    )


class TestPartitionSelectable(unittest.TestCase):
    def test_only_missed_is_dropped_by_default(self):
        # Defaults: EXCLUDE_MISSED_ENTRY=True, EXCLUDE_LATE_ENTRY=False.
        survivors = [
            _survivor("A", "early"),
            _survivor("B", "missed"),
            _survivor("C", "late"),      # late stays (opt-in exclusion is off)
            _survivor("D", "unknown"),
        ]
        actionable, excluded = _partition_selectable(survivors)
        self.assertEqual([r.symbol for r, _ in excluded], ["B"])
        self.assertEqual([r.symbol for r in actionable], ["A", "C", "D"])
        self.assertIn("missed", excluded[0][1])

    def test_day0_exit_watch_excluded(self):
        survivors = [
            _survivor("A", "early"),
            _survivor("B", "mid", day0="3 distribution days (>= 3)"),
            _survivor("C", "early", day0="AVWAP breakdown from base low"),
        ]
        actionable, excluded = _partition_selectable(survivors)
        self.assertEqual([r.symbol for r in actionable], ["A"])
        self.assertEqual({r.symbol for r, _ in excluded}, {"B", "C"})
        self.assertTrue(all("day-0 exit-watch" in reason for _, reason in excluded))

    def test_missing_components_is_actionable_fail_open(self):
        survivors = [_survivor("A", _MISSING), _survivor("B", "early")]
        actionable, excluded = _partition_selectable(survivors)
        self.assertEqual(excluded, [])
        self.assertEqual([r.symbol for r in actionable], ["A", "B"])

    def test_all_vetoed_leaves_empty_pool(self):
        survivors = [_survivor("A", "missed"), _survivor("B", "early", day0="x")]
        actionable, excluded = _partition_selectable(survivors)
        self.assertEqual(actionable, [])          # -> top-N shows FEWER, never a bad name
        self.assertEqual(len(excluded), 2)

    def test_veto_reason_none_when_clean(self):
        self.assertIsNone(_selection_veto_reason(_survivor("A", "early")))


class TestVolumeSignalsLabelsDowntrendMissed(unittest.TestCase):
    def _downtrend_df(self, n: int = 250) -> pd.DataFrame:
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        close = np.linspace(300.0, 150.0, n)              # steady Stage-4 decline
        return pd.DataFrame({
            "Open": close + 1.0,
            "High": close + 2.0,
            "Low": close - 2.0,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        }, index=idx)

    def test_downtrend_is_missed(self):
        readout = compute_volume_signals(self._downtrend_df(), "DOWN")
        self.assertEqual(readout.entry_timing, "missed", readout.entry_timing_note)


if __name__ == "__main__":
    unittest.main()
