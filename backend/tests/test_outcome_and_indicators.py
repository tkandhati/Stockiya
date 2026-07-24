"""Deterministic, offline unit tests — stdlib `unittest`, no network, no LLM.

Run:  python -m unittest backend.tests.test_outcome_and_indicators -v

Covers the safety-critical, evidence-free logic shipped 2026-07-24:
  * outcome tracker — as-of pricing, catch-up firing, per-pick horizons,
    idempotency, `declined` skipping, not-due deferral;
  * signed-pressure primitives — boundary math + None-safety.

Indicator tests use the REAL committed fixture `test_data/ABB.NS.csv` (never
synthetic bars); the pure-formula test uses hand-picked numeric boundaries,
not fabricated OHLCV series.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import backend.stages.outcome as O
from backend.indicators import (
    close_location_value,
    ewm_signed_pressure,
    signed_volume_pressure,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ABB = _PROJECT_ROOT / "test_data" / "ABB.NS.csv"


def _pick(symbol, trace_id, entry_date, horizon_days, ownership=""):
    return {
        "pick_id": trace_id, "trace_id": trace_id, "symbol": symbol,
        "entry_date": entry_date, "entry_price": "100", "stop_price": "92",
        "target_price": "116", "t1_price": "108", "t2_price": "116",
        "status": "open", "ownership": ownership, "horizon_days": horizon_days,
        "confirmation_score": "1.0", "shares_total": "10", "hit_t1": "",
        "exit_price": "", "exit_reason": "",
    }


class OutcomeTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.gettempdir()) / "outcomes_unittest.jsonl"
        if self.tmp.exists():
            self.tmp.unlink()
        self._orig_path = O._OUTCOMES_PATH
        self._orig_read = O._read_portfolio
        O._OUTCOMES_PATH = self.tmp
        # Two live picks (own horizons 30 / 90) + one declined (must skip).
        O._read_portfolio = lambda: [
            _pick("AAA.NS", "T-AAA", "2026-01-01", "30"),
            _pick("BBB.NS", "T-BBB", "2026-01-01", "90"),
            _pick("CCC.NS", "T-CCC", "2026-01-01", "30", ownership="declined"),
        ]

    def tearDown(self):
        O._OUTCOMES_PATH = self._orig_path
        O._read_portfolio = self._orig_read
        if self.tmp.exists():
            self.tmp.unlink()

    def _rows(self):
        return [json.loads(x) for x in self.tmp.read_text().splitlines()]

    def test_asof_catchup_horizons_and_idempotency(self):
        # Price stub encodes the requested as_of date, so exit_price proves
        # WHICH date was priced. today is far past 30/90 targets, before 180.
        price = lambda sym, as_of: float(as_of.toordinal())
        today = date(2026, 6, 1)

        r1 = O.run_outcome_tracker(price, today=today)
        rows = self._rows()

        # declined pick contributes nothing.
        self.assertFalse(any(x["symbol"] == "CCC.NS" for x in rows))
        # AAA fires at its own 30 (pick_target) and standard 90; BBB at 90
        # (standard+pick_target). 180 not yet due for either.
        self.assertEqual(r1["appended"], 3)
        self.assertEqual(sorted(x["horizon_days"] for x in rows), [30, 90, 90])
        self.assertFalse(any(x["horizon_days"] == 180 for x in rows))

        by = {(x["symbol"], x["horizon_days"]): x for x in rows}
        self.assertEqual(by[("AAA.NS", 30)]["horizon_kind"], "pick_target")
        self.assertEqual(by[("BBB.NS", 90)]["horizon_kind"], "standard+pick_target")

        # AS-OF pricing: exit_price is the close at the TARGET date, not today.
        aaa30 = by[("AAA.NS", 30)]
        target = date(2026, 1, 31)               # 2026-01-01 + 30
        self.assertEqual(aaa30["nominal_target_date"], target.isoformat())
        self.assertEqual(aaa30["exit_price"], round(float(target.toordinal()), 2))
        self.assertNotEqual(aaa30["exit_price"], round(float(today.toordinal()), 2))
        self.assertEqual(aaa30["snapshot_date"], today.isoformat())
        self.assertEqual(aaa30["snapshot_lag_days"], (today - target).days)

        # Idempotent: a second run appends nothing, skips all three.
        r2 = O.run_outcome_tracker(price, today=today)
        self.assertEqual(r2["appended"], 0)
        self.assertEqual(r2["skipped_already_logged"], 3)
        self.assertEqual(len(self._rows()), 3)

    def test_not_due_defers(self):
        price = lambda sym, as_of: 100.0
        # today before every target -> nothing fires (no permanent loss; a
        # later run will catch it).
        r = O.run_outcome_tracker(price, today=date(2026, 1, 2))
        self.assertEqual(r["appended"], 0)

    def test_default_asof_close_is_none_safe(self):
        # No data for a bogus symbol on this machine -> None, never raises.
        self.assertIsNone(O._default_asof_close("__NOPE__.NS", date(2026, 1, 1)))


class SignedPressureTest(unittest.TestCase):
    def test_clv_boundaries(self):
        # Pure formula boundaries (not market bars).
        self.assertAlmostEqual(close_location_value(0, 10, 0, 10), 1.0)   # close at high
        self.assertAlmostEqual(close_location_value(0, 10, 0, 0), -1.0)   # close at low
        self.assertAlmostEqual(close_location_value(0, 10, 0, 5), 0.0)    # mid
        self.assertIsNone(close_location_value(5, 5, 5, 5))               # zero range

    def test_signed_pressure_on_real_bars(self):
        if not _ABB.exists():
            self.skipTest("test_data/ABB.NS.csv not present")
        import pandas as pd
        df = pd.read_csv(_ABB, thousands=",").rename(columns={
            "OPEN": "Open", "HIGH": "High", "LOW": "Low",
            "CLOSE": "Close", "VOLUME": "Volume"})
        df = df[["Open", "High", "Low", "Close", "Volume"]].iloc[::-1].reset_index(drop=True)
        p = signed_volume_pressure(df)
        self.assertIsNotNone(p)
        for hl in (3, 10, 30):
            v = ewm_signed_pressure(p, hl)
            self.assertIsNotNone(v)
            self.assertTrue(-3.0 <= v <= 3.0)           # CLV[-1,1] * RV[0,3]
        # None-safe under the 61-bar floor.
        self.assertIsNone(signed_volume_pressure(df.tail(10)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
