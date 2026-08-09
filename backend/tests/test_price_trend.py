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


class PriceTrendLookupTests(unittest.TestCase):
    def test_arbitrary_symbol_uses_existing_scanner(self) -> None:
        with mock.patch.object(
            pt_service, "fetch_ohlcv", return_value=_pre_breakout_frame()
        ) as fetch:
            result = pt_service.lookup_price_trend("outside")

        fetch.assert_called_once_with("OUTSIDE.NS")
        self.assertTrue(result.price_history_available)
        self.assertTrue(result.matches_strategy)
        self.assertEqual(result.resolved_symbol, "OUTSIDE.NS")
        self.assertIsNotNone(result.candidate)

    def test_global_symbol_falls_back_after_nse_miss(self) -> None:
        def bars(symbol: str) -> pd.DataFrame:
            return pd.DataFrame() if symbol.endswith(".NS") else _pre_breakout_frame()

        with mock.patch.object(pt_service, "fetch_ohlcv", side_effect=bars) as fetch:
            result = pt_service.lookup_price_trend("AAPL")

        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            ["AAPL.NS", "AAPL"],
        )
        self.assertEqual(result.resolved_symbol, "AAPL")
        self.assertTrue(result.matches_strategy)

    def test_non_matching_symbol_returns_clear_result(self) -> None:
        frame = _pre_breakout_frame()
        frame.loc[frame.index[-1], ["High", "Close"]] = [121.0, 120.0]
        with mock.patch.object(pt_service, "fetch_ohlcv", return_value=frame):
            result = pt_service.lookup_price_trend("LATE.NS")

        self.assertTrue(result.price_history_available)
        self.assertFalse(result.matches_strategy)
        self.assertIsNone(result.candidate)
        self.assertIn("does not currently meet", result.message)

    def test_invalid_symbol_is_rejected_before_fetch(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid stock symbol"):
            pt_service.lookup_price_trend("../secret")


if __name__ == "__main__":
    unittest.main()
