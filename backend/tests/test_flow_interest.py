"""Offline tests for the SCORING-NEUTRAL institutional-flow presentation layer.

Verifies flow_interest never influences scores/selection and that
presentation ranking is a non-destructive reordering that falls back to the
confirmation order when no flow data exists. No network.

Run:  python -m unittest backend.tests.test_flow_interest -v
"""
from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend import block_deals as B
from backend import delivery as D
from backend import flow_interest as F


def _write_all_csv(base: Path, rows: list[dict]) -> None:
    with (base / "all.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=B._ALL_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in B._ALL_FIELDS})


class TestFlowInterest(unittest.TestCase):
    def test_none_when_no_data(self):
        with tempfile.TemporaryDirectory() as tdd, tempfile.TemporaryDirectory() as tdv:
            oldd, oldv = B._DEALS_DIR, D._DELIVERY_DIR
            B._DEALS_DIR, D._DELIVERY_DIR = Path(tdd), Path(tdv)
            try:
                _write_all_csv(Path(tdd), [])
                fi = F.flow_interest("NOTHING.NS")
            finally:
                B._DEALS_DIR, D._DELIVERY_DIR = oldd, oldv
        self.assertFalse(fi["available"])
        self.assertEqual(fi["level"], "none")
        self.assertEqual(fi["score"], 0)
        self.assertFalse(fi["analyze"])
        self.assertTrue(fi["suppressed"])   # completely suppressed -> bottom of list

    def test_strong_delivery_flags_analyze(self):
        # Delivery advisory passed in directly (as the orchestrator does);
        # deals silent (empty cache).
        with tempfile.TemporaryDirectory() as tdd:
            oldd = B._DEALS_DIR
            B._DEALS_DIR = Path(tdd)
            try:
                _write_all_csv(Path(tdd), [])
                advisory = {
                    "available": True, "latest_pct": 74.0, "avg_5d": 72.0,
                    "avg_20d": 70.0, "trend": "rising", "level": "strong", "days": 20,
                }
                fi = F.flow_interest("STRONGCO.NS", delivery=advisory)
            finally:
                B._DEALS_DIR = oldd
        self.assertTrue(fi["available"])
        self.assertEqual(fi["level"], "strong")     # avg_20d 70% >= strong band, rising
        self.assertTrue(fi["analyze"])
        self.assertFalse(fi["suppressed"])          # strong flow -> stays near the top
        self.assertIsNotNone(fi["components"]["delivery"])
        self.assertIsNone(fi["components"]["deal"])

    def test_weak_delivery_low_interest(self):
        with tempfile.TemporaryDirectory() as tdd:
            oldd = B._DEALS_DIR
            B._DEALS_DIR = Path(tdd)
            try:
                _write_all_csv(Path(tdd), [])
                advisory = {
                    "available": True, "latest_pct": 25.0, "avg_5d": 24.0,
                    "avg_20d": 25.0, "trend": "flat", "level": "weak", "days": 20,
                }
                fi = F.flow_interest("WEAKCO.NS", delivery=advisory)
            finally:
                B._DEALS_DIR = oldd
        self.assertTrue(fi["available"])          # data exists, just weak
        self.assertEqual(fi["level"], "low")      # floors near 0
        self.assertFalse(fi["analyze"])
        self.assertTrue(fi["suppressed"])         # present but too weak -> bottom

    def test_rising_deals_component(self):
        today = date.today()
        recent = (today - timedelta(days=2)).isoformat()
        older = (today - timedelta(days=20)).isoformat()
        with tempfile.TemporaryDirectory() as tdd, tempfile.TemporaryDirectory() as tdv:
            oldd, oldv = B._DEALS_DIR, D._DELIVERY_DIR
            B._DEALS_DIR, D._DELIVERY_DIR = Path(tdd), Path(tdv)
            try:
                _write_all_csv(Path(tdd), [
                    {"date": older, "symbol": "XCO.NS", "side": "BUY",
                     "qty": "1000", "client": "SOME HNI", "price": "10", "source": "bulk"},
                    {"date": recent, "symbol": "XCO.NS", "side": "BUY",
                     "qty": "1000", "client": "SOME HNI", "price": "10", "source": "bulk"},
                ])
                fi = F.flow_interest("XCO.NS")   # no delivery files -> deal leg only
            finally:
                B._DEALS_DIR, D._DELIVERY_DIR = oldd, oldv
        self.assertTrue(fi["available"])
        self.assertIsNotNone(fi["components"]["deal"])
        self.assertEqual(fi["components"]["deal"]["trend"], "rising")
        self.assertIsNone(fi["components"]["delivery"])


class TestPresentationRanks(unittest.TestCase):
    def test_reorders_by_interest_without_touching_scores(self):
        picks = [
            {"symbol": "A.NS", "rank": 1, "confirmation_score": 2.0,
             "flow_interest": {"available": True, "score": 20}},
            {"symbol": "B.NS", "rank": 2, "confirmation_score": 1.8,
             "flow_interest": {"available": True, "score": 90}},
            {"symbol": "C.NS", "rank": 3, "confirmation_score": 1.5,
             "flow_interest": {"available": True, "score": 55}},
        ]
        F.assign_presentation_ranks(picks)
        pr = {p["symbol"]: p["presentation_rank"] for p in picks}
        # Highest interest (B) presents first, then C, then A.
        self.assertEqual(pr, {"B.NS": 1, "C.NS": 2, "A.NS": 3})
        # Canonical confirmation rank + score are untouched.
        self.assertEqual([p["rank"] for p in picks], [1, 2, 3])
        self.assertEqual([p["confirmation_score"] for p in picks], [2.0, 1.8, 1.5])
        # List order itself is not mutated.
        self.assertEqual([p["symbol"] for p in picks], ["A.NS", "B.NS", "C.NS"])

    def test_falls_back_to_confirmation_order_without_flow(self):
        picks = [
            {"symbol": "A.NS", "rank": 1, "flow_interest": {"available": False, "score": 0}},
            {"symbol": "B.NS", "rank": 2, "flow_interest": {"available": False, "score": 0}},
            {"symbol": "C.NS", "rank": 3},   # no flow_interest key at all
        ]
        F.assign_presentation_ranks(picks)
        # No flow signal anywhere -> presentation rank mirrors confirmation rank.
        self.assertEqual([p["presentation_rank"] for p in picks], [1, 2, 3])

    def test_empty_is_safe(self):
        F.assign_presentation_ranks([])   # must not raise


class TestBatchLoaders(unittest.TestCase):
    def test_all_advisories_one_load(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            old = D._DELIVERY_DIR
            D._DELIVERY_DIR = base
            try:
                (base / "delivery_2026-07-23.csv").write_text("20,1,ABB,EQ,1000,700,70.00\n")
                (base / "delivery_2026-07-24.csv").write_text("20,1,ABB,EQ,1200,900,75.00\n")
                adv = D.all_advisories()
                mkt = D.latest_market_pcts()
            finally:
                D._DELIVERY_DIR = old
        self.assertIn("ABB.NS", adv)                 # keyed with .NS suffix
        self.assertTrue(adv["ABB.NS"]["available"])
        self.assertEqual(adv["ABB.NS"]["days"], 2)
        self.assertEqual(mkt, [75.0])                # latest file's cross-section

    def test_deal_symbols_one_read(self):
        recent = (date.today() - timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            old = B._DEALS_DIR
            B._DEALS_DIR = base
            try:
                _write_all_csv(base, [
                    {"date": recent, "symbol": "XCO.NS", "side": "BUY",
                     "qty": "1000", "client": "SOME HNI", "price": "10", "source": "bulk"},
                ])
                syms = B.deal_symbols()
            finally:
                B._DEALS_DIR = old
        self.assertEqual(syms, ["XCO.NS"])


class TestRetention(unittest.TestCase):
    def test_prune_all_csv_keeps_one_month(self):
        old = (date.today() - timedelta(days=40)).isoformat()   # outside 35d
        recent = (date.today() - timedelta(days=2)).isoformat()  # inside 35d
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            saved = B._DEALS_DIR
            B._DEALS_DIR = base
            try:
                _write_all_csv(base, [
                    {"date": old, "symbol": "OLD.NS", "side": "BUY", "qty": "1000",
                     "client": "X", "price": "10", "source": "bulk"},
                    {"date": recent, "symbol": "NEW.NS", "side": "BUY", "qty": "1000",
                     "client": "X", "price": "10", "source": "bulk"},
                ])
                removed = B.prune_all_csv(keep_days=35)
                # re-read what survived
                import csv as _csv
                with (base / "all.csv").open() as f:
                    rows = list(_csv.DictReader(f))
            finally:
                B._DEALS_DIR = saved
        self.assertEqual(removed, 1)
        self.assertEqual([r["symbol"] for r in rows], ["NEW.NS"])

    def test_prune_delivery_keeps_one_month(self):
        old = (date.today() - timedelta(days=40)).isoformat()
        recent = (date.today() - timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            saved = D._DELIVERY_DIR
            D._DELIVERY_DIR = base
            try:
                (base / f"delivery_{old}.csv").write_text("20,1,ABB,EQ,1000,700,70.00\n")
                (base / f"delivery_{recent}.csv").write_text("20,1,ABB,EQ,1200,900,75.00\n")
                removed = D.prune_old_files(keep_days=35)
                left = sorted(p.name for p in base.iterdir())
            finally:
                D._DELIVERY_DIR = saved
        self.assertEqual(removed, 1)
        self.assertEqual(left, [f"delivery_{recent}.csv"])


class TestWatchlistAndVsNormal(unittest.TestCase):
    def test_watchlist_ranks_and_filters(self):
        with tempfile.TemporaryDirectory() as tdd, tempfile.TemporaryDirectory() as tdv:
            oldd, oldv = B._DEALS_DIR, D._DELIVERY_DIR
            B._DEALS_DIR, D._DELIVERY_DIR = Path(tdd), Path(tdv)
            try:
                _write_all_csv(Path(tdd), [])   # no deals -> delivery-only watchlist
                (Path(tdv) / "delivery_2026-07-24.csv").write_text(
                    "20,1,STRONGCO,EQ,1000,780,78.00\n"   # strong -> included
                    "20,2,WEAKCO,EQ,1000,220,22.00\n"     # weak -> excluded
                )
                wl = F.build_watchlist()
            finally:
                B._DEALS_DIR, D._DELIVERY_DIR = oldd, oldv
        syms = [r["symbol"] for r in wl]
        self.assertIn("STRONGCO.NS", syms)
        self.assertNotIn("WEAKCO.NS", syms)          # weak flow is filtered out
        self.assertEqual(wl[0]["flow_interest"]["level"], "strong")

    def test_vs_normal_percentile(self):
        advisory = {
            "available": True, "latest_pct": 72.0, "avg_5d": 70.0,
            "avg_20d": 70.0, "trend": "rising", "level": "strong", "days": 20,
        }
        market = [10.0, 20.0, 30.0, 72.0]   # 72 is top of a 4-name cohort
        with tempfile.TemporaryDirectory() as tdd:
            oldd = B._DEALS_DIR
            B._DEALS_DIR = Path(tdd)
            try:
                _write_all_csv(Path(tdd), [])
                fi = F.flow_interest("STRONGCO.NS", delivery=advisory, market_pcts=market)
            finally:
                B._DEALS_DIR = oldd
        self.assertIsNotNone(fi["vs_normal"])
        self.assertEqual(fi["vs_normal"]["delivery_percentile"], 100.0)
        self.assertEqual(fi["vs_normal"]["cohort_n"], 4)


class TestObvDeliveryDivergence(unittest.TestCase):
    """SCORING-NEUTRAL advisory: fire only on strong-tape / weak-&-falling-flow.
    Cases mirror the real 2026-07-28 picks (ADANIENT diverges; GLAND/TITAN agree)."""

    @staticmethod
    def _ea(obv90, *, is_match=True, tier="early"):
        return {"is_match": is_match, "tier": tier,
                "features": {"obv_90d_norm_slope_pct": obv90}}

    @staticmethod
    def _dv(level, trend, *, available=True, latest=26.0, avg30=37.0):
        return {"available": available, "level": level, "trend": trend,
                "latest_pct": latest, "avg_30d": avg30}

    def test_fires_on_adanient_shape(self):
        # OBV +98% strong, delivery weak & falling -> contradiction surfaces.
        msg = F.obv_delivery_divergence(self._ea(98.42), self._dv("weak", "falling"))
        self.assertIsNotNone(msg)
        self.assertIn("delivery-divergence", msg)
        self.assertIn("26% today", msg)              # detail rendered from the numbers

    def test_silent_on_gland_shape(self):
        # OBV +116% strong but delivery strong & flat -> reads agree, no flag.
        self.assertIsNone(
            F.obv_delivery_divergence(self._ea(115.74), self._dv("strong", "flat")))

    def test_silent_on_titan_shape(self):
        # OBV +28%, delivery strong & flat -> consistent, no flag.
        self.assertIsNone(
            F.obv_delivery_divergence(self._ea(27.55), self._dv("strong", "flat")))

    def test_weak_but_flat_does_not_fire(self):
        # Low-delivery name sitting flat is not deterioration -> no flag.
        self.assertIsNone(
            F.obv_delivery_divergence(self._ea(98.0), self._dv("weak", "flat")))

    def test_weak_falling_but_tape_not_strong_does_not_fire(self):
        # No divergence when the tape itself is below the strong-OBV floor.
        self.assertIsNone(
            F.obv_delivery_divergence(self._ea(5.0), self._dv("weak", "falling")))

    def test_not_an_accumulation_match_does_not_fire(self):
        self.assertIsNone(
            F.obv_delivery_divergence(self._ea(98.0, is_match=False),
                                      self._dv("weak", "falling")))

    def test_none_safe(self):
        self.assertIsNone(F.obv_delivery_divergence(None, None))
        self.assertIsNone(F.obv_delivery_divergence(self._ea(98.0), None))
        self.assertIsNone(
            F.obv_delivery_divergence(self._ea(98.0), self._dv("weak", "falling",
                                                               available=False)))


class TestWhyPicked(unittest.TestCase):
    def test_summarizes_price_volume_basis(self):
        payload = {
            "gate_confirmation_status": {"passed": ["CS", "VD", "BR"]},
            "confirmation": {"score": 1.85, "bonuses_fired": ["MA stack 50>150>200",
                                                              "OBV-90d +7%"]},
            "entry_stage": "BREAKOUT_CONFIRMED_TODAY",
        }
        txt = F.why_picked(payload)
        self.assertIn("cleared CS/VD/BR", txt)
        self.assertIn("confirmation 1.85", txt)
        self.assertIn("2 bonus", txt)
        self.assertIn("breakout confirmed today", txt)

    def test_tolerates_float_confirmation_and_missing_fields(self):
        self.assertIn("confirmation 1.20", F.why_picked({"confirmation": 1.2}))
        # No usable fields -> still returns a sane sentence, never raises.
        self.assertTrue(F.why_picked({}).startswith("Picked on price/volume"))


if __name__ == "__main__":
    unittest.main()
