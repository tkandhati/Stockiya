"""Offline unit tests for the insights (bundle) module.

Run:  python -m unittest backend.tests.test_insights -v

No network, no clock dependence — every "as-of" is a fixed trading date and all
fixtures live in a temp tree. Verifies window slicing, pick classification,
the elimination funnel, entry-cohort bundle carry-forward, and determinism.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend import insights as I


def _business_days(start: date, n: int) -> list[str]:
    out: list[str] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:            # Mon-Fri
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# 40 trading days => exactly two complete 20-day windows.
DAYS = _business_days(date(2026, 1, 5), 40)     # 2026-01-05 is a Monday
W1 = DAYS[:20]
W2 = DAYS[20:]


def _write_picks_calendar(data_dir: Path, days: list[str]) -> None:
    for d in days:
        (data_dir / f"picks_{d}.json").write_text(
            json.dumps({"date": d, "picks": []}), encoding="utf-8"
        )


def _write_portfolio(data_dir: Path, rows: list[dict]) -> None:
    cols = [
        "pick_id", "trace_id", "entry_date", "symbol", "status",
        "exit_date", "exit_reason", "pnl_pct", "horizon_days", "ownership",
    ]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in cols))
    (data_dir / "portfolio.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_trace(data_dir: Path, day: str, symbol: str, rows: list[dict]) -> None:
    traces = data_dir / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    path = traces / f"run_{day}_{symbol}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"symbol": symbol, **r}) + "\n")


def _stage_rows(passing_through: list[str], fail_at: str | None) -> list[dict]:
    """Rows for the ordered funnel stages: pass each id until `fail_at`, then a
    hard gate short-circuits (no later rows)."""
    rows: list[dict] = []
    for sid in passing_through:
        if sid == fail_at:
            rows.append({"stage": sid, "passed": False})
            break
        rows.append({"stage": sid, "passed": True})
    return rows


class TestWindows(unittest.TestCase):
    def test_partition_complete_only(self):
        wins = I.partition_windows(DAYS, size=20)
        self.assertEqual(len(wins), 2)
        self.assertEqual(wins[0].index, 1)
        self.assertEqual(wins[0].start_iso, W1[0])
        self.assertEqual(wins[0].end_iso, W1[-1])
        self.assertEqual(wins[1].start_iso, W2[0])

    def test_partial_dropped_by_default(self):
        wins = I.partition_windows(DAYS + ["2026-04-01"], size=20)
        self.assertEqual(len(wins), 2)          # the lone trailing day is dropped

    def test_partial_included_on_flag(self):
        wins = I.partition_windows(DAYS + ["2026-04-01"], size=20,
                                   include_partial=True)
        self.assertEqual(len(wins), 3)
        self.assertEqual(len(wins[2].trading_days), 1)


class TestClassifyPick(unittest.TestCase):
    def test_open(self):
        p = I.classify_pick({"symbol": "X.NS", "entry_date": W1[0],
                             "status": "open"}, as_of=W1[-1])
        self.assertFalse(p.closed)

    def test_terminal_exit_closed(self):
        p = I.classify_pick({"symbol": "X.NS", "entry_date": W1[0],
                             "status": "target_hit", "exit_date": W1[5],
                             "pnl_pct": "12.3"}, as_of=W1[-1])
        self.assertTrue(p.closed)
        self.assertEqual(p.terminal_kind, "exit")
        self.assertAlmostEqual(p.pnl_pct, 12.3)

    def test_future_exit_still_open(self):
        # exit recorded AFTER as_of => open from as_of's viewpoint (determinism).
        p = I.classify_pick({"symbol": "X.NS", "entry_date": W1[0],
                             "status": "stopped", "exit_date": W2[5]},
                            as_of=W1[-1])
        self.assertFalse(p.closed)

    def test_declined_is_resolved(self):
        p = I.classify_pick({"symbol": "X.NS", "entry_date": W1[0],
                             "status": "open", "ownership": "declined"},
                            as_of=W1[-1])
        self.assertTrue(p.closed)
        self.assertEqual(p.terminal_kind, "declined")

    def test_superseded_kind(self):
        p = I.classify_pick({"symbol": "X.NS", "entry_date": W1[0],
                             "status": "superseded", "exit_date": W1[3]},
                            as_of=W1[-1])
        self.assertTrue(p.closed)
        self.assertEqual(p.terminal_kind, "superseded")


class TestFunnel(unittest.TestCase):
    def _setup(self, data_dir: Path) -> None:
        _write_picks_calendar(data_dir, DAYS)
        order = [sid for sid, _ in I.FUNNEL_STAGES]     # U,I,HR,LT,AC,CS,VD,BR
        # AAA passes everything and is selected.
        aaa = _stage_rows(order, fail_at=None) + [{"stage": "FINAL", "selected": True}]
        # BBB fails at AC (soft gate — chain would continue, but for the test we
        # stop at the failing row; funnel counts the stages that produced a row).
        bbb = _stage_rows(order, fail_at="AC") + [{"stage": "FINAL", "selected": False}]
        # CCC hard-rejected at HR — short-circuits, no later rows.
        ccc = _stage_rows(order, fail_at="HR")
        _write_trace(data_dir, W1[0], "AAA.NS", aaa)
        _write_trace(data_dir, W1[0], "BBB.NS", bbb)
        _write_trace(data_dir, W1[0], "CCC.NS", ccc)

    def test_counts_and_samples(self):
        with tempfile.TemporaryDirectory() as tds:
            dd = Path(tds)
            self._setup(dd)
            win = I.partition_windows(DAYS)[0]
            spine, funnel = I.build_funnel(dd, win)
            by = {s.stage_id: s for s in funnel}

            self.assertEqual(spine["ticker_days_screened"], 3)
            self.assertEqual(spine["distinct_picks_selected"], 1)

            self.assertEqual((by["U"].evaluated, by["U"].passed), (3, 3))
            self.assertEqual((by["HR"].evaluated, by["HR"].passed), (3, 2))
            self.assertEqual(by["HR"].sample_eliminated, ["CCC.NS"])
            # CCC short-circuited before LT, so only AAA+BBB reach LT.
            self.assertEqual(by["LT"].evaluated, 2)
            self.assertEqual((by["AC"].evaluated, by["AC"].passed), (2, 1))
            self.assertEqual(by["AC"].sample_eliminated, ["BBB.NS"])
            # Only AAA reaches the late gates.
            self.assertEqual(by["BR"].evaluated, 1)


class TestBundleCarryForward(unittest.TestCase):
    def _setup(self, data_dir: Path) -> None:
        _write_picks_calendar(data_dir, DAYS)
        _write_portfolio(data_dir, [
            # W1 cohort: P1 exits during W2, P2 exits inside W1, P4 declined.
            {"pick_id": "P1", "entry_date": W1[0], "symbol": "P1.NS",
             "status": "target_hit", "exit_date": W2[5], "exit_reason": "target",
             "pnl_pct": "11.0", "horizon_days": "90"},
            {"pick_id": "P2", "entry_date": W1[1], "symbol": "P2.NS",
             "status": "stopped", "exit_date": W1[5], "exit_reason": "stop",
             "pnl_pct": "-8.0", "horizon_days": "90"},
            {"pick_id": "P4", "entry_date": W1[2], "symbol": "P4.NS",
             "status": "open", "ownership": "declined"},
            # W2 cohort: P3 still open at end of W2.
            {"pick_id": "P3", "entry_date": W2[0], "symbol": "P3.NS",
             "status": "open", "horizon_days": "90"},
        ])

    def test_w1_report_shows_open(self):
        with tempfile.TemporaryDirectory() as tds:
            dd = Path(tds)
            self._setup(dd)
            wins = I.partition_windows(DAYS)
            ins = I.build_window_insight(dd, wins, target_index=1)
            self.assertEqual(ins.as_of, W1[-1])
            # W1 bundle open at end of W1 (P1's exit is in the future).
            self.assertEqual([b.window_index for b in ins.open_carry_forward], [1])
            self.assertEqual(ins.closed_this_window, [])

    def test_w2_report_closes_w1_and_opens_w2(self):
        with tempfile.TemporaryDirectory() as tds:
            dd = Path(tds)
            self._setup(dd)
            wins = I.partition_windows(DAYS)
            ins = I.build_window_insight(dd, wins, target_index=2)
            self.assertEqual(ins.as_of, W2[-1])
            # W1 became fully closed this window (P1 & P2 closed; P4 declined).
            self.assertEqual([b.window_index for b in ins.closed_this_window], [1])
            # W2 (new bundle) still open -> carried forward.
            self.assertEqual([b.window_index for b in ins.open_carry_forward], [2])
            self.assertEqual(ins.new_bundle.window_index, 2)

    def test_bundle_closed_flag(self):
        with tempfile.TemporaryDirectory() as tds:
            dd = Path(tds)
            self._setup(dd)
            wins = I.partition_windows(DAYS)
            portfolio = I.read_portfolio(dd)
            b1_at_w2 = I.build_bundle(portfolio, wins[0], as_of=W2[-1])
            self.assertTrue(b1_at_w2.closed)
            b1_at_w1 = I.build_bundle(portfolio, wins[0], as_of=W1[-1])
            self.assertFalse(b1_at_w1.closed)


class TestWriteAndDeterminism(unittest.TestCase):
    def _setup(self, data_dir: Path) -> None:
        _write_picks_calendar(data_dir, DAYS)
        _write_portfolio(data_dir, [
            {"pick_id": "P1", "entry_date": W1[0], "symbol": "P1.NS",
             "status": "open", "horizon_days": "90"},
        ])

    def test_run_writes_reports(self):
        with tempfile.TemporaryDirectory() as tds:
            dd = Path(tds)
            self._setup(dd)
            summary = I.run_insights(dd, which="all")
            self.assertEqual(summary["complete_windows"], 2)
            self.assertEqual(len(summary["written"]), 2)
            out = dd / I.INSIGHTS_DIRNAME
            self.assertTrue((out / "open_bundles.json").exists())
            self.assertTrue(any(out.glob("window_01_*.md")))
            self.assertTrue(any(out.glob("window_02_*.json")))

    def test_json_is_byte_identical_on_rerun(self):
        with tempfile.TemporaryDirectory() as tds:
            dd = Path(tds)
            self._setup(dd)
            wins = I.partition_windows(DAYS)
            a = json.dumps(I.to_json(I.build_window_insight(dd, wins, 2)), sort_keys=True)
            b = json.dumps(I.to_json(I.build_window_insight(dd, wins, 2)), sort_keys=True)
            self.assertEqual(a, b)

    def test_not_enough_days_is_graceful(self):
        with tempfile.TemporaryDirectory() as tds:
            dd = Path(tds)
            _write_picks_calendar(dd, DAYS[:10])    # < one window
            summary = I.run_insights(dd, which="latest")
            self.assertEqual(summary["complete_windows"], 0)
            self.assertIsNotNone(summary["skipped_reason"])


if __name__ == "__main__":
    unittest.main()
