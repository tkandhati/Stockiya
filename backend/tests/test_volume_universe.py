"""The volume strategy must never select outside its fixed Nifty-300 set."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.pipeline import PipelineContext
from backend.stages import universe as universe_stage
from backend.stages.rank import _partition_volume_universe, rank_survivors
from backend.universe import (
    NIFTY_300,
    NIFTY_500,
    VOLUME_UNIVERSE,
    VOLUME_UNIVERSE_LABEL,
    VOLUME_UNIVERSE_SET,
    _resolve_universe,
)


class TestVolumeUniverseBoundary(unittest.TestCase):
    def test_volume_universe_is_exactly_the_300_name_set(self) -> None:
        self.assertEqual(VOLUME_UNIVERSE_LABEL, "Nifty 300")
        self.assertEqual(VOLUME_UNIVERSE, tuple(NIFTY_300))
        self.assertEqual(len(VOLUME_UNIVERSE), 300)
        self.assertEqual(len(VOLUME_UNIVERSE_SET), 300)

    def test_configured_discovery_universe_cannot_widen_volume_boundary(self) -> None:
        self.assertGreater(len(_resolve_universe("nifty500")), 300)
        self.assertEqual(len(VOLUME_UNIVERSE_SET), 300)
        self.assertTrue(VOLUME_UNIVERSE_SET.issubset(set(NIFTY_500)))

    def test_universe_gate_rejects_a_nifty500_name_outside_top_300(self) -> None:
        outside = next(symbol for symbol in NIFTY_500 if symbol not in VOLUME_UNIVERSE_SET)
        ctx = PipelineContext(symbol=outside, trace_id="test", today_iso="2026-08-13")

        result = universe_stage.run(ctx)

        self.assertFalse(result.passed)
        self.assertIn("not in Nifty 300", result.reason)

    def test_ranker_partition_drops_outside_candidate(self) -> None:
        inside = SimpleNamespace(symbol=VOLUME_UNIVERSE[0])
        outside_symbol = next(
            symbol for symbol in NIFTY_500 if symbol not in VOLUME_UNIVERSE_SET
        )
        outside = SimpleNamespace(symbol=outside_symbol)

        eligible, excluded = _partition_volume_universe([outside, inside])

        self.assertEqual(eligible, [inside])
        self.assertEqual(excluded, [outside])

    def test_ranker_cannot_select_an_outside_candidate(self) -> None:
        outside_symbol = next(
            symbol for symbol in NIFTY_500 if symbol not in VOLUME_UNIVERSE_SET
        )
        outside = SimpleNamespace(symbol=outside_symbol, selected=True, rank=1)

        selected = rank_survivors([outside])

        self.assertEqual(selected, [])
        self.assertFalse(outside.selected)
        self.assertIsNone(outside.rank)


if __name__ == "__main__":
    unittest.main()
