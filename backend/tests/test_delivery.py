"""Offline unit tests for the NSE delivery-% loader.

Run:  python -m unittest backend.tests.test_delivery -v

No network. Fixtures are written to a temp dir in the real NSE MTO format
(headers included, to prove they're skipped) and `delivery._DELIVERY_DIR` is
monkeypatched at each test.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend import delivery as D

_HEADER = [
    "Security Wise Delivery Position - Compulsory Rolling Settlement",
    "10,MTO,24072026,1442966914,0003105",
    "Trade Date <24-JUL-2026>,Settlement Type <N>",
    "Record Type,Sr No,Name of Security,Quantity Traded,"
    "Deliverable Quantity(gross across client level),"
    "% of Deliverable Quantity to Traded Quantity",
]


def _write(base: Path, date_iso: str, rows: list[tuple], *, headers: bool = True):
    lines: list[str] = list(_HEADER) if headers else []
    for i, (sym, series, traded, deliv, pct) in enumerate(rows, 1):
        lines.append(f"20,{i},{sym},{series},{traded},{deliv},{pct}")
    (base / f"delivery_{date_iso}.csv").write_text("\n".join(lines) + "\n")


class TestDeliveryLoader(unittest.TestCase):
    def _dir(self, base):
        old = D._DELIVERY_DIR
        D._DELIVERY_DIR = base
        return old

    def test_parse_eq_only_skips_headers_and_non_eq(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _write(base, "2026-07-24", [
                ("1018GS2026", "GS", 666, 666, "100.00"),   # govt sec — ignored
                ("182D070127", "TB", 300, 300, "100.00"),   # t-bill — ignored
                ("ABB", "EQ", 258917, 108928, "42.07"),     # equity — kept
                ("20MICRONS", "EQ", 119240, 67710, "56.78"),
            ])
            old = self._dir(base)
            try:
                adv = D.delivery_advisory("ABB.NS")     # .NS stripped -> ABB
                dates = D.available_dates()
            finally:
                D._DELIVERY_DIR = old
        self.assertTrue(adv["available"])
        self.assertEqual(adv["latest_pct"], 42.07)
        self.assertEqual(adv["latest_date"], "2026-07-24")
        self.assertEqual(adv["level"], "moderate")       # 40 <= 42 < 60
        self.assertEqual(adv["days"], 1)
        self.assertEqual(dates, ["2026-07-24"])

    def test_level_bands(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _write(base, "2026-07-24", [
                ("STRONGCO", "EQ", 100, 72, "72.00"),
                ("WEAKCO", "EQ", 100, 30, "30.00"),
            ])
            old = self._dir(base)
            try:
                strong = D.delivery_advisory("STRONGCO")
                weak = D.delivery_advisory("WEAKCO.NS")
            finally:
                D._DELIVERY_DIR = old
        self.assertEqual(strong["level"], "strong")
        self.assertEqual(weak["level"], "weak")
        self.assertIn("churn", weak["note"])

    def test_trend_rising(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            # 7 days: early low (20%), recent high (90%) -> last-5 mean > 20d mean.
            for i, pct in enumerate(["20.00", "20.00", "20.00", "20.00",
                                     "90.00", "90.00", "90.00"]):
                _write(base, f"2026-07-{10 + i:02d}", [("XCO", "EQ", 100, int(float(pct)), pct)])
            old = self._dir(base)
            try:
                adv = D.delivery_advisory("XCO")
            finally:
                D._DELIVERY_DIR = old
        self.assertEqual(adv["days"], 7)
        self.assertEqual(adv["latest_pct"], 90.0)
        self.assertEqual(adv["trend"], "rising")

    def test_multi_window_averages(self):
        # Ascending pcts 1..30 -> today=30, week(last5)=28, 15d=23, 20d=20.5, 30d=15.5
        series = [(f"2026-06-{i:02d}", float(i)) for i in range(1, 31)]
        adv = D._advisory_from_series(series)
        self.assertEqual(adv["latest_pct"], 30.0)
        self.assertEqual(adv["avg_5d"], 28.0)    # week
        self.assertEqual(adv["avg_15d"], 23.0)
        self.assertEqual(adv["avg_20d"], 20.5)   # internal
        self.assertEqual(adv["avg_30d"], 15.5)
        self.assertEqual(adv["days"], 30)

    def test_missing_dir_is_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            old = self._dir(Path(td) / "does_not_exist")
            try:
                adv = D.delivery_advisory("ABB.NS")
            finally:
                D._DELIVERY_DIR = old
        self.assertFalse(adv["available"])
        self.assertIsNone(adv["latest_pct"])

    def test_date_from_ddmmyyyy_filename(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            # NSE-native name MTO_DDMMYYYY.DAT, headers stripped.
            (base / "MTO_24072026.DAT").write_text("20,1,ABB,EQ,1000,700,70.00\n")
            old = self._dir(base)
            try:
                dates = D.available_dates()
                adv = D.delivery_advisory("ABB")
            finally:
                D._DELIVERY_DIR = old
        self.assertEqual(dates, ["2026-07-24"])
        self.assertEqual(adv["latest_pct"], 70.0)


class TestDeliveryStreak(unittest.TestCase):
    """Additive, scoring-neutral quiet-accumulation streak."""

    def test_counts_recent_consecutive_above_band(self):
        # pcts ascending by date; most-recent run >= 55 is the last 5 days.
        series = [(f"2026-06-{i:02d}", pct) for i, pct in
                  enumerate([40.0, 62.0, 61.0, 58.0, 65.0, 70.0], start=1)]
        adv = D._advisory_from_series(series)
        self.assertEqual(adv["accum_streak_days"], 5)
        self.assertEqual(adv["accum_streak_min_pct"], D.STREAK_MIN_PCT)

    def test_below_band_day_breaks_streak(self):
        series = [("2026-06-01", 70.0), ("2026-06-02", 68.0), ("2026-06-03", 40.0)]
        self.assertEqual(D._advisory_from_series(series)["accum_streak_days"], 0)

    def test_note_mentions_streak_when_long_enough(self):
        series = [(f"2026-06-{i:02d}", 66.0) for i in range(1, 5)]   # 4 days >= 55
        self.assertIn("quiet accumulation", D._advisory_from_series(series)["note"])

    def test_short_streak_not_in_note(self):
        series = [("2026-06-01", 40.0), ("2026-06-02", 66.0)]        # 1 day only
        self.assertNotIn("quiet accumulation", D._advisory_from_series(series)["note"])

    def test_unavailable_has_streak_keys(self):
        adv = D._advisory_from_series([])
        self.assertEqual(adv["accum_streak_days"], 0)
        self.assertIn("accum_streak_min_pct", adv)
        self.assertIsNone(adv["accum_drift"])


class TestDeliveryDrift(unittest.TestCase):
    """Rolling-avg-vs-rolling-avg 'slow accumulation' stack — spike-proof."""

    def test_slow_buildup_is_rising(self):
        # Steadily climbing delivery 1..30 -> avg_5d(28) > avg_15d(23) > avg_30d(15.5),
        # every rung separated by >> DRIFT_MIN_GAP.
        series = [(f"2026-06-{i:02d}", float(i)) for i in range(1, 31)]
        self.assertEqual(D._advisory_from_series(series)["accum_drift"], "rising")

    def test_single_spike_is_NOT_rising(self):
        # 29 flat days then one 95% spike: lifts avg_5d but 15d ≈ 30d -> not slow drift.
        pcts = [40.0] * 29 + [95.0]
        series = [(f"2026-06-{i:02d}", p) for i, p in enumerate(pcts, start=1)]
        self.assertEqual(D._advisory_from_series(series)["accum_drift"], "flat")

    def test_constant_is_flat(self):
        series = [(f"2026-06-{i:02d}", 50.0) for i in range(1, 31)]
        self.assertEqual(D._advisory_from_series(series)["accum_drift"], "flat")

    def test_none_when_insufficient_history(self):
        # < 30 days: 15d and 30d means collapse together -> can't confirm a slow drift.
        series = [(f"2026-06-{i:02d}", float(i)) for i in range(1, 8)]
        adv = D._advisory_from_series(series)
        self.assertIn(adv["accum_drift"], (None, "flat"))


class TestDeliverySignal(unittest.TestCase):
    """Normalized [0,1] blendable accumulation signal (presentation-only)."""

    def test_high_when_strong_level_streak_and_rising(self):
        series = [(f"2026-06-{i:02d}", 60.0 + 0.5 * i) for i in range(1, 31)]
        adv = D._advisory_from_series(series)
        self.assertIsNotNone(adv["accum_signal"])
        self.assertGreater(adv["accum_signal"], 0.7)

    def test_low_when_weak_churn(self):
        series = [(f"2026-06-{i:02d}", 30.0) for i in range(1, 31)]
        self.assertLessEqual(D._advisory_from_series(series)["accum_signal"], 0.2)

    def test_none_when_unavailable(self):
        self.assertIsNone(D._advisory_from_series([])["accum_signal"])

    def test_bounded_0_1(self):
        series = [(f"2026-06-{i:02d}", 99.0) for i in range(1, 31)]
        sig = D._advisory_from_series(series)["accum_signal"]
        self.assertGreaterEqual(sig, 0.0)
        self.assertLessEqual(sig, 1.0)


if __name__ == "__main__":
    unittest.main()
