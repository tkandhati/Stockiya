"""Offline tests for the delivery fetcher/status, deals rolling trend, and the
reasoning-checklist builder. No network (fetch is exercised in DEMO_MODE only).

Run:  python -m unittest backend.tests.test_delivery_fetch_and_reasoning -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend import block_deals as B
from backend import delivery as D
from backend import reasoning_points as R


class TestDeliveryFetchAndStatus(unittest.TestCase):
    def test_fetch_is_noop_in_demo_mode(self):
        prev = os.environ.get("DEMO_MODE")
        os.environ["DEMO_MODE"] = "1"
        try:
            self.assertEqual(D.fetch_and_cache_delivery(), [])
        finally:
            if prev is None:
                os.environ.pop("DEMO_MODE", None)
            else:
                os.environ["DEMO_MODE"] = prev

    def test_corpus_status_empty_and_populated(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            old = D._DELIVERY_DIR
            D._DELIVERY_DIR = base
            try:
                st0 = D.delivery_corpus_status()
                self.assertFalse(st0["available"])
                self.assertEqual(st0["days"], 0)

                (base / "delivery_2026-07-23.csv").write_text(
                    "20,1,ABB,EQ,1000,700,70.00\n20,2,TCS,EQ,500,100,20.00\n"
                )
                (base / "delivery_2026-07-24.csv").write_text(
                    "20,1,ABB,EQ,1200,900,75.00\n"
                )
                st = D.delivery_corpus_status()
            finally:
                D._DELIVERY_DIR = old
        self.assertTrue(st["available"])
        self.assertEqual(st["days"], 2)
        self.assertEqual(st["latest_date"], "2026-07-24")
        self.assertEqual(st["oldest_date"], "2026-07-23")
        self.assertEqual(st["symbols_latest"], 1)   # only ABB in the latest file


class TestDealsRollingTrend(unittest.TestCase):
    def _write_all_csv(self, base: Path, rows: list[dict]) -> None:
        import csv
        with (base / "all.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=B._ALL_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in B._ALL_FIELDS})

    def test_recent_acceleration_reads_rising(self):
        today = date.today()
        recent = (today - timedelta(days=2)).isoformat()   # inside 7d window
        older = (today - timedelta(days=20)).isoformat()   # inside 30d, outside 7d
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            old = B._DEALS_DIR
            B._DEALS_DIR = base
            try:
                self._write_all_csv(base, [
                    {"date": older, "symbol": "XCO.NS", "side": "BUY",
                     "qty": "1000", "client": "SOME HNI", "price": "10", "source": "bulk"},
                    {"date": recent, "symbol": "XCO.NS", "side": "BUY",
                     "qty": "1000", "client": "SOME HNI", "price": "10", "source": "bulk"},
                ])
                agg = B.aggregate_30d("XCO.NS")
            finally:
                B._DEALS_DIR = old
        self.assertEqual(agg.buy_qty, 2000)
        self.assertEqual(agg.net_qty, 2000)
        self.assertEqual(agg.net_qty_recent, 1000)
        # short daily rate (1000/7 ≈ 142.9) > long daily rate (2000/30 ≈ 66.7)
        self.assertEqual(agg.deal_trend, "rising")

    def test_no_deals_leaves_trend_none(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            old = B._DEALS_DIR
            B._DEALS_DIR = base
            try:
                self._write_all_csv(base, [])
                agg = B.aggregate_30d("NOPE.NS")
            finally:
                B._DEALS_DIR = old
        self.assertEqual(agg.buy_count + agg.sell_count, 0)
        self.assertIsNone(agg.deal_trend)


class TestReasoningBuilder(unittest.TestCase):
    def _base_payload(self) -> dict:
        return {
            "symbol": "ZZZ.NS",   # no deals cache -> deals step is 'silent'
            "gate_confirmation_status": {"passed": ["CS", "VD", "BR"],
                                         "failed": [], "counts": {"passed": 3, "total": 3}},
            "gates_evidence": {"CS": ["tight base"], "VD": ["dry-up + div"], "BR": ["broke out"]},
            "confirmation": {"score": 1.23, "bonuses_fired": ["b1"]},
            "accumulation_assessment": {"participant_evidence": "inferred", "level": "healthy",
                                        "score_0_100": 61.0},
            "entry_stage": "BREAKOUT_CONFIRMED_TODAY",
            "holding_horizon": {"days": 90, "basis": "vol", "source": "entry_estimate"},
        }

    def test_delivery_step_present_even_when_unavailable(self):
        payload = self._base_payload()
        payload["delivery"] = {"available": False, "latest_pct": None}
        pts = R.build_reasoning(payload)
        labels = [p["label"] for p in pts]
        # The whole point: delivery load status is ALWAYS a visible step.
        deliv = [p for p in pts if p["label"].startswith("Delivery %")]
        self.assertEqual(len(deliv), 1)
        self.assertIn("no delivery files loaded", deliv[0]["value"])
        # Core steps present too.
        self.assertIn("Consolidation base", labels)
        self.assertIn("Breakout thrust", labels)

    def test_delivery_step_reflects_loaded_advisory(self):
        payload = self._base_payload()
        payload["delivery"] = {
            "available": True, "latest_pct": 72.0, "latest_date": "2026-07-24",
            "avg_5d": 70.0, "avg_20d": 60.0, "trend": "rising", "level": "strong",
            "note": "", "days": 20,
        }
        pts = R.build_reasoning(payload)
        deliv = [p for p in pts if p["label"].startswith("Delivery %")][0]
        self.assertEqual(deliv["state"], "bullish")
        self.assertIn("72% strong", deliv["value"])
        self.assertIn("rising", deliv["value"])

    def test_never_raises_on_empty_payload(self):
        # Should degrade, not crash.
        pts = R.build_reasoning({})
        self.assertIsInstance(pts, list)


if __name__ == "__main__":
    unittest.main()
