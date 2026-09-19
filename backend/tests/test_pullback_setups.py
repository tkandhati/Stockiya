"""Offline tests for Section 2 — Pullback Re-Entry Setups (backend/pullback_setups.py).

Pure-function tests over synthetic OHLCV: no network, no portfolio, no disk. We
build a rising accumulation base (so the interest gate passes), then splice an
impulse day + pullback + trigger onto the tail to exercise each branch of the
state machine.

Run: python -m unittest backend.tests.test_pullback_setups -v
"""
from __future__ import annotations

import unittest

import pandas as pd

from backend import pullback_setups as PS


def _frame(rows: list[dict]) -> pd.DataFrame:
    """rows: list of {open, high, low, close, volume} ascending. Adds a bdate index."""
    idx = pd.bdate_range("2025-01-01", periods=len(rows))
    df = pd.DataFrame(
        {
            "Open": [r["open"] for r in rows],
            "High": [r["high"] for r in rows],
            "Low": [r["low"] for r in rows],
            "Close": [r["close"] for r in rows],
            "Volume": [float(r["volume"]) for r in rows],
        },
        index=idx,
    )
    return df


def _rising_base(n: int, start: float = 100.0, up_vol=1_200_000, dn_vol=600_000) -> list[dict]:
    """A clean rising accumulation base: 3 up / 2 down, up-days carry more volume
    (OBV rises, up/down ratio > 1, close well above the 50d SMA)."""
    rows: list[dict] = []
    price = start
    for i in range(n):
        up = (i % 5) < 3
        prev = price
        price = price * (1.003 if up else 0.998)
        o = prev
        c = price
        hi = max(o, c) * 1.002
        lo = min(o, c) * 0.998
        rows.append({"open": o, "high": hi, "low": lo, "close": c, "volume": up_vol if up else dn_vol})
    return rows


def _falling_base(n: int, start: float = 200.0) -> list[dict]:
    """A distribution base: 2 up / 3 down, down-days carry more volume (OBV falls,
    up/down ratio < 1, price drifts below its 50d SMA) -> interest should fade."""
    rows: list[dict] = []
    price = start
    for i in range(n):
        up = (i % 5) < 2
        prev = price
        price = price * (1.002 if up else 0.997)
        o = prev
        c = price
        hi = max(o, c) * 1.002
        lo = min(o, c) * 0.998
        rows.append({"open": o, "high": hi, "low": lo, "close": c, "volume": 600_000 if up else 1_200_000})
    return rows


class TestPullbackSetups(unittest.TestCase):
    def _firing_rows(self) -> list[dict]:
        base = _rising_base(195)
        c194 = base[-1]["close"]
        # Day 0 (index 195): +2.5% impulse on 2.0M vol (>= 1.5x ADV50 ~ 0.9M).
        d0c = c194 * 1.025
        d0l = c194 * 1.005
        rows = base + [
            {"open": c194 * 1.006, "high": d0c * 1.005, "low": d0l, "close": d0c, "volume": 2_000_000},
        ]
        # Pullback (196, 197): quiet down-days = VDU (< 0.7x ADV50), holding > Day-0 low.
        p196 = d0c * 0.99
        rows.append({"open": d0c, "high": d0c, "low": p196 * 0.997, "close": p196, "volume": 400_000})
        p197 = p196 * 0.997
        rows.append({"open": p196, "high": p196, "low": p197 * 0.998, "close": p197, "volume": 500_000})
        # Consolidation (198) — becomes the "prior bar" for the trigger.
        p198 = p197 * 1.001
        rows.append({"open": p197, "high": p198 * 1.001, "low": p197 * 0.999, "close": p198, "volume": 500_000})
        # Trigger (199): high > prior high AND volume > prior volume.
        p199 = p198 * 1.01
        rows.append({"open": p198, "high": p199 * 1.005, "low": p198, "close": p199, "volume": 900_000})
        return rows

    def test_buy_trigger_fires_with_levels(self):
        row = PS.evaluate_symbol("TEST.NS", _frame(self._firing_rows()))
        self.assertEqual(row["status"], "buy_trigger", row["notes"])
        self.assertTrue(row["interest"]["persisted"])
        self.assertTrue(row["vdu"])
        # Entry/stop/target present and internally consistent (2:1, stop below low).
        self.assertIsNotNone(row["entry"])
        self.assertIsNotNone(row["stop"])
        self.assertIsNotNone(row["target1"])
        self.assertLess(row["stop"], row["entry"])
        # entry/stop/target1 are each rounded to 2 dp independently, so the 2:1
        # relation holds to within a paisa.
        risk = row["entry"] - row["stop"]
        self.assertAlmostEqual(row["target1"], row["entry"] + 2 * risk, delta=0.02)
        # Stop is 0.5% below the pullback low.
        self.assertAlmostEqual(row["stop"], row["pullback_low"] * (1 - PS.STOP_BUFFER), delta=0.02)

    def test_heavy_downday_invalidates(self):
        rows = self._firing_rows()
        # Turn bar 197 into a heavy-volume distribution down-day (> 1.2x ADV50).
        rows[197]["volume"] = 3_000_000
        row = PS.evaluate_symbol("TEST.NS", _frame(rows))
        self.assertEqual(row["status"], "invalidated", row["notes"])

    def test_close_below_day0_low_invalidates(self):
        rows = self._firing_rows()
        # Push bar 198 to close below the Day-0 low.
        day0_low = rows[195]["low"]
        rows[198]["close"] = day0_low * 0.98
        rows[198]["low"] = day0_low * 0.97
        row = PS.evaluate_symbol("TEST.NS", _frame(rows))
        self.assertEqual(row["status"], "invalidated", row["notes"])

    def test_interest_faded_excluded(self):
        row = PS.evaluate_symbol("WEAK.NS", _frame(_falling_base(200)))
        self.assertEqual(row["status"], "interest_faded", row["notes"])
        self.assertFalse(row["interest"]["persisted"])

    def test_insufficient_history(self):
        row = PS.evaluate_symbol("SHORT.NS", _frame(_rising_base(50)))
        self.assertEqual(row["status"], "insufficient_history")

    def test_no_impulse_when_interest_holds_but_no_spark(self):
        # A pure rising base with no impulse day in the last 20 sessions.
        row = PS.evaluate_symbol("CALM.NS", _frame(_rising_base(200)))
        self.assertEqual(row["status"], "no_impulse", row["notes"])
        self.assertTrue(row["interest"]["persisted"])


if __name__ == "__main__":
    unittest.main()
