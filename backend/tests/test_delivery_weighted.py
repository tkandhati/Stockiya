"""Offline unit tests for the fresh delivery-led analysis (presentation overlay).

Run:  python -m unittest backend.tests.test_delivery_weighted -v

Pure function, no network, no clock. Proves the analysis is a FRESH ranking —
a strong-delivery low-composite name outranks a high-composite no-delivery pick —
and that it collapses to composite order when no delivery data is present.
"""
from __future__ import annotations

import unittest

from backend.delivery_weighted import build_delivery_analysis


def _adv(available: bool, signal=None):
    return {"available": available, "accum_signal": signal}


class TestDeliveryAnalysis(unittest.TestCase):
    def test_delivery_leads_over_composite(self):
        cands = [
            {"symbol": "A.NS", "composite_score": 0.20, "delivery": _adv(True, 0.9)},
            {"symbol": "B.NS", "composite_score": 0.40, "delivery": _adv(False)},
        ]
        rows = build_delivery_analysis(cands, {"B.NS"})
        # A has strong delivery + low composite; B is the top composite but no
        # delivery. Delivery-led => A ranks first even though B is a volume pick.
        self.assertEqual(rows[0]["symbol"], "A.NS")
        self.assertGreater(rows[0]["fresh_score"], rows[1]["fresh_score"])

    def test_no_delivery_falls_back_to_composite_order(self):
        cands = [
            {"symbol": "A.NS", "composite_score": 0.20, "delivery": _adv(False)},
            {"symbol": "B.NS", "composite_score": 0.40, "delivery": _adv(False)},
        ]
        rows = build_delivery_analysis(cands)
        self.assertEqual([r["symbol"] for r in rows], ["B.NS", "A.NS"])
        self.assertTrue(all(r["delivery_signal"] is None for r in rows))

    def test_in_picks_flag(self):
        cands = [{"symbol": "A.NS", "composite_score": 0.3, "delivery": _adv(True, 0.5)}]
        rows = build_delivery_analysis(cands, {"A.NS"})
        self.assertTrue(rows[0]["in_picks"])

    def test_top_n_cap(self):
        cands = [
            {"symbol": f"S{i}.NS", "composite_score": 0.1 * i, "delivery": _adv(False)}
            for i in range(1, 6)
        ]
        self.assertEqual(len(build_delivery_analysis(cands, top_n=2)), 2)

    def test_empty_pool(self):
        self.assertEqual(build_delivery_analysis([]), [])

    def test_deterministic_tie_break_by_symbol(self):
        cands = [
            {"symbol": "Z.NS", "composite_score": 0.3, "delivery": _adv(False)},
            {"symbol": "A.NS", "composite_score": 0.3, "delivery": _adv(False)},
        ]
        rows = build_delivery_analysis(cands)
        self.assertEqual([r["symbol"] for r in rows], ["A.NS", "Z.NS"])


if __name__ == "__main__":
    unittest.main()
