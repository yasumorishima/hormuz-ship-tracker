"""Tests for the shared stream parser.

`land_filter` is stubbed out: it pulls in Shapely and a 73 KB polygon file to
answer one question, and what is under test here is the parser, not the mask.
The stub makes the answer controllable, which is what the tests need.
"""

import sys
import types
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_stub = types.ModuleType("land_filter")
_stub.ON_LAND = False
_stub.is_on_land = lambda lat, lon: _stub.ON_LAND
sys.modules["land_filter"] = _stub

import ais_parse  # noqa: E402


def position(mmsi=123456789, lat=26.0, lon=56.0, name="TEST SHIP"):
    return (
        '{"MessageType":"PositionReport",'
        '"MetaData":{"MMSI":%d,"ShipName":"%s",'
        '"time_utc":"2026-09-16 06:57:51.594510977 +0000 UTC"},'
        '"Message":{"PositionReport":{"Latitude":%s,"Longitude":%s,'
        '"Sog":12.5,"Cog":270.0,"TrueHeading":271}}}'
    ) % (mmsi, name, lat, lon)


class FakeClock:
    """Stands in for time.monotonic, which counts from boot, not from now."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class ThrottleTest(unittest.TestCase):
    def setUp(self):
        _stub.ON_LAND = False
        self.clock = FakeClock(5.0)
        self._real = ais_parse.time.monotonic
        ais_parse.time.monotonic = self.clock

    def tearDown(self):
        ais_parse.time.monotonic = self._real

    def test_first_sighting_is_kept_early_in_a_machines_life(self):
        """The regression this guards: a 0 sentinel made `now - prev` small.

        On a hosted runner the process can start while monotonic() is still
        below the window length, and every vessel's first report would then be
        thrown away — silently, and worst for anchored vessels, which repeat
        least often.
        """
        parser = ais_parse.StreamParser(position_interval_sec=180)
        self.assertLess(self.clock.t, 180, "the test must run inside the window")
        row = parser.feed(position())
        self.assertIsNotNone(row, "first sighting was dropped")
        self.assertEqual(parser.throttled, 0)

    def test_second_report_inside_the_window_is_throttled(self):
        parser = ais_parse.StreamParser(position_interval_sec=180)
        self.assertIsNotNone(parser.feed(position()))
        self.clock.t += 10
        self.assertIsNone(parser.feed(position()))
        self.assertEqual(parser.throttled, 1)

    def test_report_after_the_window_is_kept_again(self):
        parser = ais_parse.StreamParser(position_interval_sec=180)
        self.assertIsNotNone(parser.feed(position()))
        self.clock.t += 181
        self.assertIsNotNone(parser.feed(position()))

    def test_each_vessel_is_throttled_separately(self):
        parser = ais_parse.StreamParser(position_interval_sec=180)
        self.assertIsNotNone(parser.feed(position(mmsi=111111111)))
        self.assertIsNotNone(parser.feed(position(mmsi=222222222)))
        self.assertEqual(parser.throttled, 0)


class RowShapeTest(unittest.TestCase):
    def setUp(self):
        _stub.ON_LAND = False

    def test_row_matches_the_declared_column_order(self):
        parser = ais_parse.StreamParser(position_interval_sec=0)
        parser.feed(
            '{"MessageType":"ShipStaticData",'
            '"MetaData":{"MMSI":123456789},'
            '"Message":{"ShipStaticData":{"Name":"TEST SHIP ","Type":70,'
            '"Destination":"JEBEL ALI","MaximumStaticDraught":12.3,'
            '"Dimension":{"A":100,"B":50,"C":10,"D":12}}}}'
        )
        row = parser.feed(position())
        self.assertIsNotNone(row)
        self.assertEqual(len(row), len(ais_parse.COLUMNS))
        got = dict(zip(ais_parse.COLUMNS, row))
        self.assertEqual(got["mmsi"], 123456789)
        self.assertEqual(got["latitude"], 26.0)
        self.assertEqual(got["longitude"], 56.0)
        self.assertEqual(got["speed"], 12.5)
        self.assertEqual(got["course"], 270.0)
        self.assertEqual(got["heading"], 271)
        self.assertEqual(got["ship_type"], 70)
        self.assertEqual(got["length"], 150)   # A + B
        self.assertEqual(got["width"], 22)     # C + D
        self.assertEqual(got["draught"], 12.3)
        self.assertEqual(got["timestamp"], "2026-09-16T06:57:51.594510")

    def test_on_land_positions_are_dropped(self):
        _stub.ON_LAND = True
        parser = ais_parse.StreamParser(position_interval_sec=0)
        self.assertIsNone(parser.feed(position()))
        self.assertEqual(parser.dropped_on_land, 1)

    def test_a_position_without_coordinates_is_not_a_row(self):
        parser = ais_parse.StreamParser(position_interval_sec=0)
        self.assertIsNone(parser.feed(
            '{"MessageType":"PositionReport","MetaData":{"MMSI":1},'
            '"Message":{"PositionReport":{"Sog":1.0}}}'
        ))

    def test_summary_counts_what_the_window_saw(self):
        parser = ais_parse.StreamParser(position_interval_sec=0)
        parser.feed(position(mmsi=111111111))
        parser.feed(position(mmsi=222222222))
        s = parser.summary()
        self.assertEqual(s["frames"], 2)
        self.assertEqual(s["position_reports"], 2)
        self.assertEqual(s["distinct_mmsi_seen"], 2)


class TimestampTest(unittest.TestCase):
    def test_aisstream_format(self):
        self.assertEqual(
            ais_parse.normalize_timestamp("2026-03-14 06:57:51.594510977 +0000 UTC"),
            "2026-03-14T06:57:51.594510",
        )

    def test_empty(self):
        self.assertEqual(ais_parse.normalize_timestamp(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
