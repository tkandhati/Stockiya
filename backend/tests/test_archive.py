"""Offline unit tests for the data-file archiver.

Run:  python -m unittest backend.tests.test_archive -v

No network, no clock dependence (a fixed `today` is passed in). Files are
created in a temp tree and archived into a temp archive dir.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from backend import archive as A


class TestFileDate(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(A._file_date("picks_2026-07-24.json"), date(2026, 7, 24))
        self.assertEqual(A._file_date("pos_2026-01-02.jsonl"), date(2026, 1, 2))
        self.assertEqual(A._file_date("run_2024-06-06_RELIANCE.NS.jsonl"),
                         date(2024, 6, 6))

    def test_ddmmyyyy(self):
        self.assertEqual(A._file_date("MTO_24072026.DAT"), date(2026, 7, 24))

    def test_undated_is_none(self):
        self.assertIsNone(A._file_date("README.md"))
        self.assertIsNone(A._file_date("outcomes.jsonl"))


class TestArchive(unittest.TestCase):
    def _setup(self, td: Path):
        src = td / "delivery"
        src.mkdir()
        # old (should archive) + recent (should stay) + undated + protected
        (src / "delivery_2025-01-01.csv").write_text("old\n")
        (src / "delivery_2026-07-20.csv").write_text("recent\n")
        (src / "README.md").write_text("keep me\n")
        rules = [A.ArchiveRule("delivery", src, ["delivery_*", "*.DAT"], keep_days=260)]
        return src, rules

    def test_moves_only_aged_dated_files(self):
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            src, rules = self._setup(td)
            arch = td / "archive"
            today = date(2026, 7, 26)
            summary = A.run_archive(today, rules=rules, archive_dir=arch)

            self.assertEqual(summary["archived"], 1)
            self.assertEqual(summary["by_rule"]["delivery"]["moved"], 1)
            # old moved out, recent + README stay
            self.assertFalse((src / "delivery_2025-01-01.csv").exists())
            self.assertTrue((src / "delivery_2026-07-20.csv").exists())
            self.assertTrue((src / "README.md").exists())
            # old landed in the archive
            self.assertTrue((arch / "delivery" / "delivery_2025-01-01.csv").exists())

    def test_dry_run_moves_nothing(self):
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            src, rules = self._setup(td)
            arch = td / "archive"
            summary = A.run_archive(date(2026, 7, 26), rules=rules,
                                    archive_dir=arch, dry_run=True)
            self.assertEqual(summary["archived"], 1)          # would move 1
            self.assertTrue((src / "delivery_2025-01-01.csv").exists())  # but didn't
            self.assertFalse(arch.exists())

    def test_protected_never_moves(self):
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            src = td / "data"
            src.mkdir()
            # an aged but PROTECTED file that a loose glob could catch
            (src / "outcomes.jsonl").write_text("x\n")
            (src / "portfolio.csv").write_text("x\n")
            rules = [A.ArchiveRule("data", src, ["*"], keep_days=1)]
            summary = A.run_archive(date(2026, 7, 26), rules=rules,
                                    archive_dir=td / "archive")
            self.assertEqual(summary["archived"], 0)
            self.assertTrue((src / "outcomes.jsonl").exists())
            self.assertTrue((src / "portfolio.csv").exists())

    def test_missing_src_dir_is_safe(self):
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            rules = [A.ArchiveRule("nope", td / "absent", ["*"], keep_days=1)]
            summary = A.run_archive(date(2026, 7, 26), rules=rules,
                                    archive_dir=td / "archive")
            self.assertEqual(summary["archived"], 0)


if __name__ == "__main__":
    unittest.main()
