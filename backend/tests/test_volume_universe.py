"""The volume strategy scans exactly its Nifty Total Market universe, and never
selects a symbol outside it — independent of the configurable discovery universe
(STOCKYA_UNIVERSE). The universe was widened from the fixed Nifty-300 set to the
full Nifty Total Market list on 2026-09-02 (see CHANGELOG)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.pipeline import PipelineContext
from backend.stages import universe as universe_stage
from backend.stages.rank import _partition_volume_universe, rank_survivors
from backend.universe import (
    NIFTY_TOTAL_MARKET,
    VOLUME_UNIVERSE,
    VOLUME_UNIVERSE_LABEL,
    VOLUME_UNIVERSE_SET,
    _resolve_universe,
)

# A symbol guaranteed not to be an NSE member — robust to index rebalances.
_OUTSIDE = "ZZ_NOT_IN_UNIVERSE.NS"


class TestVolumeUniverseBoundary(unittest.TestCase):
    def test_volume_universe_is_the_nifty_total_market_set(self) -> None:
        self.assertEqual(VOLUME_UNIVERSE_LABEL, "Nifty Total Market")
        self.assertEqual(VOLUME_UNIVERSE, tuple(NIFTY_TOTAL_MARKET))
        # No duplicates, all Yahoo-suffixed, and a full-size list (~750).
        self.assertEqual(len(VOLUME_UNIVERSE), len(VOLUME_UNIVERSE_SET))
        self.assertGreaterEqual(len(VOLUME_UNIVERSE), 500)
        self.assertTrue(all(s.endswith(".NS") for s in VOLUME_UNIVERSE))
        self.assertNotIn(_OUTSIDE, VOLUME_UNIVERSE_SET)

    def test_discovery_universe_does_not_change_volume_boundary(self) -> None:
        # Selecting a smaller discovery universe must not alter the volume set:
        # the volume strategy always scans the Nifty Total Market list.
        small = _resolve_universe("nifty50")
        self.assertLess(len(small), len(VOLUME_UNIVERSE_SET))
        self.assertEqual(VOLUME_UNIVERSE, tuple(NIFTY_TOTAL_MARKET))

    def test_universe_gate_rejects_a_symbol_outside_the_volume_universe(self) -> None:
        ctx = PipelineContext(symbol=_OUTSIDE, trace_id="test", today_iso="2026-09-02")

        result = universe_stage.run(ctx)

        self.assertFalse(result.passed)
        self.assertIn("not in Nifty Total Market", result.reason)

    def test_ranker_partition_drops_outside_candidate(self) -> None:
        inside = SimpleNamespace(symbol=VOLUME_UNIVERSE[0])
        outside = SimpleNamespace(symbol=_OUTSIDE)

        eligible, excluded = _partition_volume_universe([outside, inside])

        self.assertEqual(eligible, [inside])
        self.assertEqual(excluded, [outside])

    def test_ranker_cannot_select_an_outside_candidate(self) -> None:
        outside = SimpleNamespace(symbol=_OUTSIDE, selected=True, rank=1)

        selected = rank_survivors([outside])

        self.assertEqual(selected, [])
        self.assertFalse(outside.selected)
        self.assertIsNone(outside.rank)


if __name__ == "__main__":
    unittest.main()
