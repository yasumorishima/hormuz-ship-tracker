"""Tests for the file selection the publishing job depends on.

`files_since` decides what a publish run reads. It is pure, so it can be
tested against synthetic listings — which matters because the smoke job in CI
only ever loads the archive, and the shard-reading path stays unexercised
until collection actually starts.
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    import hf_store
except ImportError:  # pragma: no cover
    hf_store = None

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)

LISTING = [
    ".gitattributes",
    "positions.parquet",
    "transit_events.parquet",
    "daily/2026-09-10.parquet",
    "daily/2026-09-14.parquet",
    "daily/2026-09-15.parquet",
    "raw/2026-09-15/235200.parquet",
    "raw/2026-09-16/000700.parquet",
    "raw/2026-09-16/114700.parquet",
]


@unittest.skipIf(hf_store is None, "pyarrow not installed")
class FilesSinceTest(unittest.TestCase):
    def since(self, hours):
        return hf_store.files_since(hours, files=LISTING, now=NOW)

    def test_a_48_hour_window_takes_today_and_the_two_days_before(self):
        got = self.since(48)
        self.assertIn("raw/2026-09-16/114700.parquet", got)
        self.assertIn("raw/2026-09-15/235200.parquet", got)
        self.assertIn("daily/2026-09-15.parquet", got)
        self.assertIn("daily/2026-09-14.parquet", got)
        self.assertNotIn("daily/2026-09-10.parquet", got)

    def test_the_archive_is_never_picked_up_by_the_window(self):
        """It has no day in its path, and a 48-hour publish must not drag
        175k rows of 2026-04 into a map of this week."""
        for hours in (0, 48, 720):
            self.assertNotIn("positions.parquet", self.since(hours))
            self.assertNotIn("transit_events.parquet", self.since(hours))

    def test_nothing_but_parquet_comes_back(self):
        for path in self.since(720):
            self.assertTrue(path.endswith(".parquet"), path)

    def test_a_zero_hour_window_is_today_only(self):
        got = self.since(0)
        self.assertEqual(sorted(got), ["raw/2026-09-16/000700.parquet",
                                       "raw/2026-09-16/114700.parquet"])

    def test_the_window_rounds_outwards_rather_than_missing_a_file(self):
        """36 hours back from noon lands mid-day; the whole day comes."""
        self.assertIn("raw/2026-09-15/235200.parquet", self.since(36))
        self.assertIn("daily/2026-09-15.parquet", self.since(36))


@unittest.skipIf(hf_store is None, "pyarrow not installed")
class PathTest(unittest.TestCase):
    def test_a_shard_lands_where_files_since_looks_for_it(self):
        when = datetime(2026, 9, 16, 11, 47, 3, tzinfo=timezone.utc)
        path = hf_store.shard_path(when)
        self.assertEqual(path, "raw/2026-09-16/114703.parquet")
        self.assertIn(path, hf_store.files_since(1, files=[path], now=NOW))

    def test_a_day_file_lands_where_files_since_looks_for_it(self):
        path = hf_store.daily_path(datetime(2026, 9, 15))
        self.assertEqual(path, "daily/2026-09-15.parquet")
        self.assertIn(path, hf_store.files_since(48, files=[path], now=NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
