"""Offline tests for the Coiled Accumulators watch cohort
(backend/coiled_accumulators.py).

A coiled accumulator is a base that (1) is in a base/coil stage, (2) has NOT
broken out, (3) has coiled >= COIL_MIN_BASE_DAYS, (4) has banked accumulation
(90d OBV positive + durable), (5) is STILL absorbing at the right edge, and
(6) is not distributing. Presentation/monitoring-only: never changes selection,
so these are pure-function tests over synthetic payloads. No network.

Run: python -m unittest backend.tests.test_coiled_accumulators -v
"""
from __future__ import annotations

import os
import unittest

from backend import coiled_accumulators as C
from backend.entry_stage_label import COILED_PRE_BREAKOUT, LATE_CHASE


def _payload(**over) -> dict:
    """A clean, qualifying coiled accumulator (modelled on real OFSS 2026-08-17):
    coiled 37 sessions, no breakout, durable 90d/180d OBV, right-edge demand,
    no distribution. Override any field per test."""
    p = {
        "symbol": "TEST.NS",
        "company": "Test Co",
        "rank": 1,
        "entry_stage": COILED_PRE_BREAKOUT,
        "gate_confirmation_status": {"passed": ["CS", "VD"], "failed": ["BR"]},
        "volume_event": {"direction": "neutral", "kind": "neutral", "base_days": 37},
        "flow_timeframes": {
            "obv_10d_norm_slope_pct": 3.7,
            "obv_30d_norm_slope_pct": -24.0,
            "obv_flow_inflection": "healing",
            "obv_90d_norm_slope_pct": 95.0,
            "obv_180d_norm_slope_pct": 295.0,
            "up_down_vol_ratio_90d": 1.81,
        },
        "stealth_demand": {"ratio": 1.85, "in_dryup": True, "window": 10},
        "accumulation_assessment": {"contradictions": [], "would_veto_shadow": False},
        # Coil geometry + plan — the support point ("enter on or before") reads the
        # 25-bar base low, falling back to the protective stop.
        "entry_stage_features": {"base_low_25": 3500.0, "base_high_25": 3800.0},
        "price_plan": {"entry": 3720.0, "stop": 3450.0, "t1": 3900.0, "t2": 4100.0},
        "current_price": 3720.0,
    }
    p.update(over)
    return p


class TestAssess(unittest.TestCase):
    def test_clean_coil_qualifies(self):
        r = C.assess_coiled_accumulator(_payload())
        self.assertTrue(r["qualifies"], r["reason"])
        self.assertEqual(r["blocks"], [])
        self.assertEqual(r["coil_age_days"], 37)
        self.assertTrue(r["not_broken_out"])
        self.assertTrue(r["flow_strengthening"])  # healing inflection

    def test_broken_out_disqualifies(self):
        r = C.assess_coiled_accumulator(_payload(
            gate_confirmation_status={"passed": ["CS", "VD", "BR"], "failed": []},
        ))
        self.assertFalse(r["qualifies"])
        self.assertTrue(any("already broke out" in b for b in r["blocks"]))

    def test_base_too_short(self):
        ve = {"direction": "neutral", "kind": "neutral", "base_days": 10}
        r = C.assess_coiled_accumulator(_payload(volume_event=ve))
        self.assertFalse(r["qualifies"])
        self.assertTrue(any("base only 10d" in b for b in r["blocks"]))

    def test_missing_base_days(self):
        ve = {"direction": "neutral", "kind": "neutral"}  # no base_days
        r = C.assess_coiled_accumulator(_payload(volume_event=ve))
        self.assertFalse(r["qualifies"])
        self.assertIsNone(r["coil_age_days"])
        self.assertTrue(any("coil age unknown" in b for b in r["blocks"]))

    def test_wrong_stage(self):
        r = C.assess_coiled_accumulator(_payload(entry_stage=LATE_CHASE))
        self.assertFalse(r["qualifies"])
        self.assertTrue(any("not a base/coil stage" in b for b in r["blocks"]))

    def test_no_accumulation_banked(self):
        ft = dict(_payload()["flow_timeframes"])
        ft["obv_90d_norm_slope_pct"] = -5.0
        r = C.assess_coiled_accumulator(_payload(flow_timeframes=ft))
        self.assertFalse(r["qualifies"])
        self.assertTrue(any("90d OBV not positive" in b for b in r["blocks"]))

    def test_accumulation_not_durable(self):
        ft = dict(_payload()["flow_timeframes"])
        ft["obv_90d_norm_slope_pct"] = 4.0   # positive
        ft["obv_180d_norm_slope_pct"] = -3.0  # but 180d weak
        ft["up_down_vol_ratio_90d"] = 0.8     # and net selling
        r = C.assess_coiled_accumulator(_payload(flow_timeframes=ft))
        self.assertFalse(r["qualifies"])
        self.assertTrue(any("not durable" in b for b in r["blocks"]))

    def test_not_absorbing_now(self):
        # This is THE discriminator vs a dead base: banked accumulation but the
        # right edge has gone quiet -> apathy, not a loaded spring.
        r = C.assess_coiled_accumulator(_payload(
            stealth_demand={"ratio": 1.0, "in_dryup": True, "window": 10},
        ))
        self.assertFalse(r["qualifies"])
        self.assertTrue(any("not absorbing now" in b for b in r["blocks"]))

    def test_distribution_veto_contradiction(self):
        r = C.assess_coiled_accumulator(_payload(
            accumulation_assessment={
                "contradictions": ["distribution-into-strength"],
                "would_veto_shadow": False,
            },
        ))
        self.assertFalse(r["qualifies"])
        self.assertIn("distribution-into-strength", r["blocks"])

    def test_distribution_veto_bearish_volume_event(self):
        r = C.assess_coiled_accumulator(_payload(
            volume_event={"direction": "bearish", "kind": "bearish_distribution", "base_days": 37},
        ))
        self.assertFalse(r["qualifies"])
        self.assertTrue(any("bearish" in b for b in r["blocks"]))

    def test_support_point_from_base_low(self):
        r = C.assess_coiled_accumulator(_payload())
        self.assertEqual(r["support_point"], 3500.0)      # 25-bar base low
        self.assertEqual(r["support_basis"], "25-bar base low")
        self.assertEqual(r["entry_reference"], 3720.0)    # planned entry (buy-zone top)
        self.assertIn("still absorbing", r["volume_gate"])
        self.assertIn("Enter on or before", r["reason"])

    def test_support_point_falls_back_to_stop(self):
        p = _payload()
        p["entry_stage_features"] = {}   # no base_low_25 persisted
        r = C.assess_coiled_accumulator(p)
        self.assertEqual(r["support_point"], 3450.0)      # protective stop
        self.assertEqual(r["support_basis"], "protective stop")

    def test_support_point_absent_when_no_levels(self):
        p = _payload()
        p["entry_stage_features"] = {}
        p["price_plan"] = {}
        p["current_price"] = None
        r = C.assess_coiled_accumulator(p)
        self.assertIsNone(r["support_point"])
        self.assertIsNone(r["entry_reference"])
        self.assertNotIn("Enter on or before", r["reason"])  # no fabricated level

    def test_never_raises_on_junk(self):
        for junk in ({}, {"entry_stage": None}, {"flow_timeframes": None}):
            r = C.assess_coiled_accumulator(junk)
            self.assertFalse(r["qualifies"])
            self.assertIsNone(r["support_point"])          # nothing to derive from


class TestBuild(unittest.TestCase):
    def test_build_keeps_only_qualifiers(self):
        good = _payload(symbol="GOOD.NS")
        bad = _payload(symbol="BAD.NS", entry_stage=LATE_CHASE)
        rows = C.build_coiled_accumulators([good, bad])
        syms = {r["symbol"] for r in rows}
        self.assertIn("GOOD.NS", syms)
        self.assertNotIn("BAD.NS", syms)

    def test_env_disable(self):
        prev = os.environ.get("STOCKYA_COILED_WATCH")
        os.environ["STOCKYA_COILED_WATCH"] = "0"
        try:
            rows = C.build_coiled_accumulators([_payload()])
            self.assertEqual(rows, [])
        finally:
            if prev is None:
                os.environ.pop("STOCKYA_COILED_WATCH", None)
            else:
                os.environ["STOCKYA_COILED_WATCH"] = prev

    def test_awareness_resurface_note(self):
        # A strong coil the entry-readiness router binned as stale_base should be
        # re-surfaced here with source_section=awareness and a rebin note.
        p = _payload(symbol="RESCUE.NS")
        p["not_actionable"] = {"category": "stale_base", "why": "..."}
        rows = C.build_coiled_accumulators([p])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_section"], "awareness")
        self.assertEqual(rows[0]["also_flagged"], "stale_base")
        self.assertIn("still accumulating", rows[0]["why"])

    def test_pre_breakout_echo_and_prior_link(self):
        p = _payload(symbol="ECHO.NS")
        p["pre_breakout_eligibility"] = {"eligible": True}
        p["pick_history"] = [
            {"date": "2026-08-14", "score": 2.1},
            {"date": "2026-08-11", "score": 1.9},
        ]
        rows = C.build_coiled_accumulators([p])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["also_pre_breakout"])
        self.assertEqual(row["prior"]["prior_appearances"], 2)
        self.assertEqual(row["prior"]["appearances_incl_today"], 3)
        self.assertEqual(row["prior"]["first_seen"], "2026-08-11")  # oldest is last

    def test_build_never_raises_on_bad_rows(self):
        rows = C.build_coiled_accumulators([None, 42, "x", _payload(symbol="OK.NS")])
        self.assertEqual({r["symbol"] for r in rows}, {"OK.NS"})

    def test_build_row_carries_support_point(self):
        rows = C.build_coiled_accumulators([_payload(symbol="SUP.NS")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["support_point"], 3500.0)
        self.assertEqual(rows[0]["entry_reference"], 3720.0)
        self.assertTrue(rows[0]["volume_gate"])

    def test_sort_strengthening_first(self):
        strengthening = _payload(symbol="STRONG.NS")  # healing -> flow_strengthening
        ft = dict(_payload()["flow_timeframes"])
        ft["obv_flow_inflection"] = "neutral"
        ft["obv_10d_norm_slope_pct"] = -1.0  # not strengthening
        flat = _payload(symbol="FLAT.NS", flow_timeframes=ft)
        rows = C.build_coiled_accumulators([flat, strengthening])
        self.assertEqual(rows[0]["symbol"], "STRONG.NS")


if __name__ == "__main__":
    unittest.main()
