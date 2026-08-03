"""Self-Veto Override at selection (2026-08-03): setups our own volume-signature
lens rates as entry_timing == "missed" (Stage 4 / distribution — "exit zone, not
entry") must NOT appear in the top-N picks.

Covers the pure partition used by rank_survivors, and confirms volume_signals
actually labels a clear downtrend "missed". No network.
Run: python -m unittest backend.tests.test_missed_exclusion -v
"""
from __future__ import annotations

import types
import unittest

import numpy as np
import pandas as pd

from backend.stages.rank import _partition_missed
from backend.volume_signals import compute as compute_volume_signals


def _survivor(symbol: str, timing):
    """Minimal stand-in for a PipelineResult carrying an entry-timing verdict."""
    comps = None if timing is _MISSING else {"entry_timing": timing}
    return types.SimpleNamespace(symbol=symbol, confirmation_components=comps)


_MISSING = object()


class TestPartitionMissed(unittest.TestCase):
    def test_only_missed_is_dropped(self):
        survivors = [
            _survivor("A", "early"),
            _survivor("B", "missed"),
            _survivor("C", "late"),
            _survivor("D", "unknown"),
        ]
        actionable, missed = _partition_missed(survivors)
        self.assertEqual([r.symbol for r in missed], ["B"])
        # early / late / unknown all remain selectable (user asked only for
        # "missed · exit zone, not entry" to be removed).
        self.assertEqual([r.symbol for r in actionable], ["A", "C", "D"])

    def test_missing_components_is_actionable_fail_open(self):
        survivors = [_survivor("A", None), _survivor("B", _MISSING)]
        actionable, missed = _partition_missed(survivors)
        self.assertEqual(missed, [])
        self.assertEqual([r.symbol for r in actionable], ["A", "B"])

    def test_all_missed_leaves_empty_pool(self):
        survivors = [_survivor("A", "missed"), _survivor("B", "missed")]
        actionable, missed = _partition_missed(survivors)
        self.assertEqual(actionable, [])          # -> top-N shows FEWER, never a distribution name
        self.assertEqual(len(missed), 2)


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
        # Below all falling MAs on a sustained decline -> Stage 4 / distribution.
        self.assertEqual(readout.entry_timing, "missed", readout.entry_timing_note)


if __name__ == "__main__":
    unittest.main()
