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


if __name__ == "__main__":
    unittest.main()
