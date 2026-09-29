"""Tests for the survey that decides what the map shows of the radar layer.

Its numbers went into docs and into a threshold, so the arithmetic under them
is pinned here on inputs whose answer is known.
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sar_survey as sv  # noqa: E402


class BandTest(unittest.TestCase):
    def test_edges_belong_to_the_band_below(self):
        idx = sv.band_of([0.5, 1.0, 1.0001, 3.0, 9.99, 10.0, 10.01, 49.0])
        self.assertEqual(list(idx), [0, 0, 1, 1, 2, 2, 3, 3])

    def test_inside_the_coast_buffer_is_in_no_band(self):
        """There is no scored water nearer than 200 m to divide by."""
        self.assertEqual(list(sv.band_of([0.0, 0.1, 0.2, float("nan")])), [-1, -1, -1, -1])

    def test_rates_are_per_water_in_that_band(self):
        rows = sv.band_rates(dist_km=[0.5, 0.5, 5.0, 20.0, 20.0],
                             vessel_sized=[True, False, True, True, True],
                             snr=[11, 50, 12, 13, 30],
                             band_area_km2=[100, 100, 50, 400])
        by = {r["band_km"]: r for r in rows}
        self.assertEqual(by["0.2-1"]["candidates"], 2)
        self.assertEqual(by["0.2-1"]["vessel_sized"], 1)
        self.assertEqual(by["0.2-1"]["per_100km2"], 1.0)
        self.assertEqual(by["1-3"]["vessel_sized"], 0)
        self.assertIsNone(by["1-3"]["snr_median"])
        self.assertEqual(by["3-10"]["per_100km2"], 2.0)
        self.assertEqual(by["10+"]["per_100km2"], 0.5)
        self.assertEqual(by["10+"]["snr_gt20_pct"], 50.0)
        # the rejected candidate's SNR of 50 is not a vessel-sized SNR
        self.assertEqual(by["0.2-1"]["snr_median"], 11.0)


class PassTest(unittest.TestCase):
    def test_slices_of_one_datatake_are_one_pass(self):
        a = "S1D_IW_GRDH_1SDV_20260927T021405_20260927T021430_004757_008E90"
        b = "S1D_IW_GRDH_1SDV_20260927T021430_20260927T021455_004757_008E90"
        c = "S1C_IW_GRDH_1SDV_20260927T021405_20260927T021430_009999_000000"
        self.assertEqual(sv.pass_of(a), sv.pass_of(b))
        self.assertNotEqual(sv.pass_of(a), sv.pass_of(c))

    def test_the_geometry_slot_separates_the_two_descending_orbits(self):
        self.assertEqual(sv.geometry_of("2026-09-15T02:14:43Z"),
                         sv.geometry_of("2026-09-27T02:14:18Z"))
        self.assertNotEqual(sv.geometry_of("2026-09-15T02:14:43Z"),
                            sv.geometry_of("2026-09-16T02:06:16Z"))


def everywhere(_pass, lon, _lat):
    return np.ones(len(lon), dtype=bool)


class RecurrenceTest(unittest.TestCase):
    def test_a_fixed_object_recurs_and_a_moving_one_does_not(self):
        # pass A, B, C each see the platform at the same place (a few metres
        # of jitter); a ship is seen once, by A only, far from anything.
        lon = np.array([56.0, 56.00001, 55.99999, 56.5])
        lat = np.array([26.0, 26.00001, 26.0, 26.5])
        passes = np.array(["A", "B", "C", "A"])
        cov, hit = sv.recurrence(lon, lat, passes, everywhere, radius_km=0.1)
        self.assertEqual(list(cov), [2, 2, 2, 2])
        self.assertEqual(list(hit), [2, 2, 2, 0])

    def test_the_own_pass_is_never_a_second_look(self):
        """Two detections of one pass next to each other are one look, and
        adjacent slices of a datatake overlap."""
        lon = np.array([56.0, 56.0001])
        lat = np.array([26.0, 26.0])
        cov, hit = sv.recurrence(lon, lat, np.array(["A", "A"]), everywhere, 0.1)
        self.assertEqual(list(cov), [0, 0])
        self.assertEqual(list(hit), [0, 0])

    def test_a_pass_that_did_not_look_there_is_not_counted(self):
        lon = np.array([56.0, 57.0])
        lat = np.array([26.0, 27.0])

        def only_east(p, lo, la):
            return np.asarray(lo) > 56.5 if p == "B" else np.ones(len(lo), bool)

        cov, hit = sv.recurrence(lon, lat, np.array(["A", "B"]), only_east, 0.1)
        self.assertEqual(list(cov), [0, 1])   # B never covered the first point
        self.assertEqual(list(hit), [0, 0])

    def test_the_control_asks_the_same_question_elsewhere(self):
        lon = np.array([56.0, 56.0])
        lat = np.array([26.0, 26.0])
        passes = np.array(["A", "B"])
        away = sv.offset_points(lon, lat, 0.5)
        cov, hit = sv.recurrence(lon, lat, passes, everywhere, 0.1, query=away)
        self.assertEqual(list(cov), [1, 1])
        self.assertEqual(list(hit), [0, 0])

    def test_offsets_are_the_distance_asked_for(self):
        lon = np.full(200, 56.4)
        lat = np.full(200, 26.4)
        olon, olat = sv.offset_points(lon, lat, 0.5)
        d = np.hypot(*(sv.xy_km(olon, olat) - sv.xy_km(lon, lat)).T)
        np.testing.assert_allclose(d, 0.5, rtol=1e-6)

    def test_the_table_keeps_the_control_to_its_own_band(self):
        rows = sv.recurrence_table(
            band_idx=np.array([3, 3, 3, 0]), covered=np.array([2, 3, 1, 5]),
            hits=np.array([0, 1, 1, 4]),
            control_band_idx=np.array([3, 0, 3, 0]), control_covered=np.array([2, 2, 2, 2]),
            control_hits=np.array([0, 2, 1, 0]))
        by = {r["band_km"]: r for r in rows}
        self.assertEqual(by["10+"]["n"], 2)                 # covered once is not scored
        self.assertEqual(by["10+"]["seen_again_pct"], 50.0)
        self.assertEqual(by["10+"]["control_n"], 2)
        self.assertEqual(by["10+"]["control_seen_again_pct"], 50.0)
        self.assertEqual(by["0.2-1"]["control_seen_again_pct"], 50.0)
        self.assertIsNone(by["1-3"]["seen_again_pct"])


class CrossPolTest(unittest.TestCase):
    def test_the_lower_bound(self):
        self.assertAlmostEqual(sv.real_lower_bound(0.5, 0.0), 0.5)
        self.assertAlmostEqual(sv.real_lower_bound(0.55, 0.1), 0.5)
        self.assertEqual(sv.real_lower_bound(0.05, 0.1), 0.0)
        self.assertIsNone(sv.real_lower_bound(0.5, None))

    def test_a_bright_vh_pixel_two_away_still_counts(self):
        rng = np.random.default_rng(0)
        vh = rng.normal(20, 2, (512, 512)).clip(1).astype(np.uint16)
        vh[300, 302] = 400
        water = np.ones_like(vh, dtype=bool)
        z = sv.vh_scores(vh, water, [300, 300, 100], [300, 296, 100])
        self.assertGreater(z[0], 50)         # two pixels from the peak
        self.assertLess(z[1], 10)            # six away: outside the window
        self.assertLess(z[2], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
