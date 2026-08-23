"""Offline tests for the per-run data-health summary (backend/data_health.py).

Proves silent ingest failures are counted and classified instead of being
discarded. No network. Run: python -m unittest backend.tests.test_data_health -v
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.ingest_health import classify_ingest_failure, summarize_data_health


def _result(symbol: str, passed: bool, reason: str | None = None):
    """Minimal PipelineResult stand-in: `.symbol` + `.stage_results['I']`."""
    sr = SimpleNamespace(passed=passed, reason=reason)
    return SimpleNamespace(symbol=symbol, stage_results={"I": sr})


class TestClassifyIngestFailure(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(classify_ingest_failure("fetch failed: HTTPError: 429"), "network")
        self.assertEqual(classify_ingest_failure("no OHLCV from data source"), "empty")
        self.assertEqual(classify_ingest_failure("only 120 bars, need >=200"), "short_history")
        self.assertEqual(classify_ingest_failure("no current price from data source"), "no_price")
        self.assertEqual(classify_ingest_failure("data source missing OHLCV: x"), "missing_source")
        self.assertEqual(classify_ingest_failure("no bars at or before as_of=2026-08-14"), "no_bars_asof")
        self.assertEqual(classify_ingest_failure("something weird"), "other")
        self.assertEqual(classify_ingest_failure(None), "other")


class TestSummarizeDataHealth(unittest.TestCase):
    def test_counts_ok_failures_and_crashes(self):
        results = (
            [_result(f"OK{i}.NS", True) for i in range(6)]
            + [_result("NET.NS", False, "fetch failed: timeout")]
            + [_result("EMPTY.NS", False, "no OHLCV from data source")]
            + [_result("NEW.NS", False, "only 120 bars, need >=200")]
        )
        # attempted=10 but only 9 results returned -> 1 crashed future.
        dh = summarize_data_health(results, attempted=10)

        self.assertEqual(dh["attempted"], 10)
        self.assertEqual(dh["results_returned"], 9)
        self.assertEqual(dh["crashed"], 1)
        self.assertEqual(dh["ingested_ok"], 6)
        self.assertEqual(dh["failed_by_reason"]["network"], 1)
        self.assertEqual(dh["failed_by_reason"]["empty"], 1)
        self.assertEqual(dh["failed_by_reason"]["short_history"], 1)
        self.assertEqual(dh["failed_by_reason"]["crashed"], 1)
        # short_history is a benign exclusion; silent_failures = net+empty+crashed.
        self.assertEqual(dh["silent_failures"], 3)
        self.assertEqual(dh["coverage_pct"], 60.0)
        self.assertIn("NET.NS", dh["failed_symbols"]["network"])

    def test_all_clean(self):
        results = [_result(f"S{i}.NS", True) for i in range(5)]
        dh = summarize_data_health(results, attempted=5)
        self.assertEqual(dh["ingested_ok"], 5)
        self.assertEqual(dh["silent_failures"], 0)
        self.assertEqual(dh["coverage_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
