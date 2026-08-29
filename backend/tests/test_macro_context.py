"""Offline tests for the scoring-neutral macro/conviction context
(backend/macro_context.py).

Covers the pure analytics (regime, return correlation, leadership, export tag,
sector lookup) and graceful degradation (disabled / offline -> None, never
raises). No network: the fetch path is exercised only for its offline fallback.

Run: python -m unittest backend.tests.test_macro_context -v
"""
from __future__ import annotations

import os
import unittest

import pandas as pd

from backend import macro_context as M


def _series(vals, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(vals), freq="D")
    return pd.Series(vals, index=idx, dtype="float64")


class RegimeTests(unittest.TestCase):
    def test_uptrend_is_tailwind(self):
        s = _series([100 * (1.002 ** i) for i in range(120)])
        self.assertEqual(M.market_regime(s), "tailwind")

    def test_downtrend_is_headwind(self):
        s = _series([100 * (0.999 ** i) for i in range(120)])
        self.assertEqual(M.market_regime(s), "headwind")

    def test_short_series_is_none(self):
        self.assertIsNone(M.market_regime(_series([100, 101, 102])))
        self.assertIsNone(M.market_regime(None))


class CorrelationTests(unittest.TestCase):
    def test_identical_series_corr_one(self):
        s = _series([100 * (1.001 ** i) for i in range(90)])
        self.assertEqual(M.returns_correlation(s, s), 1.0)

    def test_thin_overlap_is_none(self):
        a = _series([100, 101, 102, 103, 104])
        b = _series([100, 99, 98, 97, 96])
        self.assertIsNone(M.returns_correlation(a, b))

    def test_none_inputs(self):
        self.assertIsNone(M.returns_correlation(None, _series([1, 2, 3])))


class LeadershipTests(unittest.TestCase):
    def test_leader_when_outperforms(self):
        stock = _series([100 * (1.003 ** i) for i in range(80)])
        bench = _series([100 * (0.999 ** i) for i in range(80)])
        out = M.leadership(stock, bench)
        self.assertEqual(out["label"], "leader")
        self.assertGreater(out["rel_pct"], 0)

    def test_laggard_when_underperforms(self):
        stock = _series([100 * (0.999 ** i) for i in range(80)])
        bench = _series([100 * (1.003 ** i) for i in range(80)])
        self.assertEqual(M.leadership(stock, bench)["label"], "laggard")

    def test_short_history_is_none(self):
        self.assertIsNone(M.leadership(_series([1, 2, 3]), _series([1, 2, 3])))


class ExportSectorTests(unittest.TestCase):
    def test_export_by_sector(self):
        self.assertEqual(M.export_exposure("IT")["exposure"], "high")
        self.assertEqual(M.export_exposure("Banking")["exposure"], "low")
        self.assertEqual(M.export_exposure("Auto")["exposure"], "medium")
        self.assertEqual(M.export_exposure(None)["exposure"], "unknown")
        self.assertEqual(M.export_exposure("NoSuchSector")["exposure"], "unknown")

    def test_sector_lookup_with_and_without_suffix(self):
        smap = {"ABB.NS": "CapitalGoods"}
        self.assertEqual(M.sector_for("ABB.NS", smap), "CapitalGoods")
        self.assertEqual(M.sector_for("abb.ns", smap), "CapitalGoods")
        self.assertIsNone(M.sector_for("ZZZZ.NS", smap))

    def test_bundled_sector_map_loads(self):
        smap = M.load_sector_map()
        # The shipped seed map resolves a few well-known names.
        self.assertEqual(M.sector_for("TCS.NS", smap), "IT")
        self.assertEqual(M.sector_for("SUNPHARMA.NS", smap), "Pharma")


class DegradationTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("STOCKYA_MACRO_CONTEXT", None)

    def test_disabled_build_context_is_none(self):
        os.environ["STOCKYA_MACRO_CONTEXT"] = "0"
        self.assertFalse(M._enabled())
        self.assertIsNone(M.build_context("TCS.NS", {}, M.load_sector_map()))
        self.assertEqual(M.fetch_market_series(), {})

    def test_build_context_never_raises_on_unknown_symbol(self):
        # Enabled, but empty market + unknown symbol + no stock data -> None,
        # and it must not raise even though the fetch path is touched.
        os.environ["STOCKYA_MACRO_CONTEXT"] = "1"
        out = M.build_context("ZZZZ_NOT_A_SYMBOL.NS", {}, {})
        self.assertIsNone(out)

    def test_build_context_sector_only_when_offline(self):
        # Known sector + empty market (offline) still yields a context block
        # carrying sector/export, with US/leadership None.
        os.environ["STOCKYA_MACRO_CONTEXT"] = "1"
        out = M.build_context("TCS.NS", {}, {"TCS.NS": "IT"})
        self.assertIsNotNone(out)
        self.assertEqual(out["sector"], "IT")
        self.assertEqual(out["export"]["exposure"], "high")
        self.assertIsNone(out["us"]["regime"])


if __name__ == "__main__":
    unittest.main()
