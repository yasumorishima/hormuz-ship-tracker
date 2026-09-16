"""Tests for the static-field backfill."""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from enrich import backfill_static  # noqa: E402

SCHEMA = """
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi INTEGER NOT NULL, timestamp TEXT NOT NULL,
    latitude REAL, longitude REAL, speed REAL, course REAL, heading REAL,
    ship_name TEXT, ship_type INTEGER, destination TEXT,
    draught REAL, length REAL, width REAL, flag TEXT,
    received_at TEXT NOT NULL
);
"""


def row(conn, mmsi, received_at, ship_name="", ship_type=None, destination=""):
    conn.execute(
        "INSERT INTO positions (mmsi, timestamp, latitude, longitude, "
        "ship_name, ship_type, destination, received_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (mmsi, received_at, 26.0, 56.0, ship_name, ship_type, destination, received_at),
    )


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)

    def test_a_later_window_fills_an_earlier_one(self):
        # window 1 saw the position but not the static frame
        row(self.conn, 1, "2026-09-16T00:00:00+00:00")
        # window 2, six minutes later, carried the description
        row(self.conn, 1, "2026-09-16T00:06:00+00:00",
            ship_name="TEST SHIP", ship_type=70, destination="JEBEL ALI")
        filled = backfill_static(self.conn)

        got = self.conn.execute(
            "SELECT ship_name, ship_type, destination FROM positions "
            "WHERE received_at = '2026-09-16T00:00:00+00:00'").fetchone()
        self.assertEqual(got, ("TEST SHIP", 70, "JEBEL ALI"))
        self.assertEqual(filled["ship_name"], 1)
        self.assertEqual(filled["ship_type"], 1)

    def test_nothing_is_copied_between_different_vessels(self):
        row(self.conn, 1, "2026-09-16T00:00:00+00:00", ship_name="ONE", ship_type=70)
        row(self.conn, 2, "2026-09-16T00:01:00+00:00")
        backfill_static(self.conn)
        got = self.conn.execute(
            "SELECT ship_name, ship_type FROM positions WHERE mmsi = 2").fetchone()
        self.assertEqual(got, ("", None), "a vessel took another vessel's identity")

    def test_the_most_recent_description_wins(self):
        row(self.conn, 1, "2026-09-16T00:00:00+00:00", destination="FUJAIRAH")
        row(self.conn, 1, "2026-09-16T00:05:00+00:00", destination="JEBEL ALI")
        row(self.conn, 1, "2026-09-16T00:10:00+00:00")
        backfill_static(self.conn)
        got = self.conn.execute(
            "SELECT destination FROM positions "
            "WHERE received_at = '2026-09-16T00:10:00+00:00'").fetchone()[0]
        self.assertEqual(got, "JEBEL ALI")

    def test_a_vessel_never_described_stays_empty(self):
        row(self.conn, 1, "2026-09-16T00:00:00+00:00")
        filled = backfill_static(self.conn)
        got = self.conn.execute(
            "SELECT ship_name, ship_type FROM positions").fetchone()
        self.assertEqual(got, ("", None))
        self.assertEqual(filled["ship_name"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
