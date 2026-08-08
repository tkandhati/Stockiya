from __future__ import annotations

import os
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from backend.price_trend import service as pt_service
from backend.price_trend.scanner import scan_symbol
from backend.universe import UNIVERSE


def _pre_breakout_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=190)
    first = np.linspace(80.0, 105.0, 150)
    base = np.linspace(105.0, 113.0, 40)
    close = np.concatenate([first, base])
    close[-10:] = np.linspace(111.8, 114.2, 10)
    high = close + 0.8
    low = close - 0.8
    high[-25] = 115.0
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": np.linspace(1_000, 50_000, len(close)),
        },
        index=dates,
    )


class PriceTrendScannerTests(unittest.TestCase):
    def test_finds_pre_breakout_structure(self) -> None:
        candidate = scan_symbol("TEST.NS", _pre_breakout_frame())

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertIn(candidate.status, {"ready", "forming"})
        self.assertGreaterEqual(candidate.distance_to_breakout_pct, 0)
        self.assertLessEqual(candidate.distance_to_breakout_pct, 2.5)
        self.assertEqual(candidate.breakout_price, 115.0)

    def test_volume_cannot_change_price_trend_result(self) -> None:
        low_volume = _pre_breakout_frame()
        high_volume = low_volume.copy()
        high_volume["Volume"] = np.where(
            np.arange(len(high_volume)) % 2, 1, 10**12
        )

        first = scan_symbol("TEST.NS", low_volume)
        second = scan_symbol("TEST.NS", high_volume)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.model_dump(), second.model_dump())

    def test_completed_breakout_is_not_shown(self) -> None:
        frame = _pre_breakout_frame()
        frame.loc[frame.index[-1], ["High", "Close"]] = [121.0, 120.0]

        self.assertIsNone(scan_symbol("TEST.NS", frame))


class PriceTrendScanLimitTests(unittest.TestCase):
    """Guards the important fix: scan the FULL universe by default, not [:30]."""

    def test_defaults_to_full_universe(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRICE_TREND_SCAN_LIMIT", None)
            self.assertEqual(pt_service._scan_limit(), len(UNIVERSE))

    def test_zero_or_negative_means_full(self) -> None:
        for value in ("0", "-5"):
            with mock.patch.dict(os.environ, {"PRICE_TREND_SCAN_LIMIT": value}):
                self.assertEqual(pt_service._scan_limit(), len(UNIVERSE))

    def test_positive_caps_but_never_beyond_universe(self) -> None:
        with mock.patch.dict(os.environ, {"PRICE_TREND_SCAN_LIMIT": "12"}):
            self.assertEqual(pt_service._scan_limit(), 12)
        with mock.patch.dict(os.environ, {"PRICE_TREND_SCAN_LIMIT": "99999"}):
            self.assertEqual(pt_service._scan_limit(), len(UNIVERSE))

    def test_garbage_falls_back_to_full(self) -> None:
        with mock.patch.dict(os.environ, {"PRICE_TREND_SCAN_LIMIT": "abc"}):
            self.assertEqual(pt_service._scan_limit(), len(UNIVERSE))


if __name__ == "__main__":
    unittest.main()
