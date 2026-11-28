"""The diff between two detector runs: it must be empty when nothing changed,
and when something did, it must say which step lost the detection.

The scenes are the synthetic ones of test_sar_detect (a ramp of speckle with
planted targets), so every expected difference is known in advance.
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import sar_detect  # noqa: E402
import sar_diff  # noqa: E402
from test_sar_detect import PIXEL_M, SHAPE, TRANSFORM, scene_with_targets  # noqa: E402


def run(image, land, **params):
    stage_keys = ("k_sigma", "coast_buffer_m")
    st = sar_detect.stages(image, land, PIXEL_M,
                           **{k: v for k, v in params.items() if k in stage_keys})
    rows, _ = sar_detect.measure(st, TRANSFORM, PIXEL_M,
                                 **{k: v for k, v in params.items() if k not in stage_keys})
    return st, rows


class StagesAreTheDetector(unittest.TestCase):
    def test_stages_then_measure_is_detect(self):
        """The diff reads the arrays the shipped detector used, not a copy."""
        image, _ = scene_with_targets()
        land = np.zeros(SHAPE, bool)
        land[:, :40] = True
        want = sar_detect.detect(image, land, TRANSFORM, PIXEL_M)
        got = sar_detect.measure(sar_detect.stages(image, land, PIXEL_M),
                                 TRANSFORM, PIXEL_M)
        self.assertEqual(want, got)


class DiffSaysWhatChanged(unittest.TestCase):
    def setUp(self):
        self.image, self.truth = scene_with_targets()
        self.land = np.zeros(SHAPE, bool)
        self.land[:, :100] = True      # a coast on the left

    def test_same_settings_no_differences(self):
        st_a, a = run(self.image, self.land)
        st_b, b = run(self.image, self.land)
        self.assertEqual(sar_diff.diff_rows(a, b, st_a, st_b), [])

    def test_a_higher_threshold_loses_at_the_threshold(self):
        """The planted targets stand ~5x over the sea; k=40 is above that."""
        st_a, a = run(self.image, self.land)
        st_b, b = run(self.image, self.land, k_sigma=40.0)
        recs = sar_diff.diff_rows(a, b, st_a, st_b)
        lost = {t for t in self.truth for r in recs
                if abs(r["grid_row"] - t[0]) <= 2 and abs(r["grid_col"] - t[1]) <= 2
                and (r["kind"], r["stage"]) == ("only_a", "threshold")}
        self.assertEqual(lost, set(self.truth), recs)
        self.assertEqual({r["kind"] for r in recs}, {"only_a"})

    def test_a_wider_coast_buffer_loses_at_water(self):
        """A target 100 px (about 2 km) off the coast, behind a 3 km buffer."""
        image = self.image.copy()
        ramp = np.linspace(40.0, 120.0, SHAPE[1])[200]
        image[799:802, 199:202] = int(ramp * 5)
        st_a, a = run(image, self.land)
        st_b, b = run(image, self.land, coast_buffer_m=3000.0)
        recs = sar_diff.diff_rows(a, b, st_a, st_b)
        near = [r for r in recs if abs(r["grid_row"] - 800) <= 2
                and abs(r["grid_col"] - 200) <= 2]
        self.assertEqual([(r["kind"], r["stage"], r["band"]) for r in near],
                         [("only_a", "water", "1-3")])

    def test_a_shape_rule_flips_rather_than_drops(self):
        st_a, a = run(self.image, self.land)
        st_b, b = run(self.image, self.land, min_area_px=20)
        recs = sar_diff.diff_rows(a, b, st_a, st_b)
        self.assertTrue(recs)
        self.assertEqual({r["kind"] for r in recs}, {"flipped"})
        self.assertEqual({r["stage"] for r in recs}, {"ok->too_small"})

    def test_a_moved_field_is_named(self):
        _, a = run(self.image, self.land)
        b = [dict(r) for r in a]
        b[0]["snr"] += 1.0
        recs = sar_diff.diff_rows(a, b)
        self.assertEqual([(r["kind"], r["stage"]) for r in recs], [("changed", "snr")])

    def test_float32_storage_is_not_a_change(self):
        """What the Hub's float32 columns do to a far-offshore distance."""
        _, a = run(self.image, self.land)
        b = [dict(r) for r in a]
        for r in b:
            for name, _ in sar_diff.FIELDS:
                r[name] = float(np.float32(r[name]))
            r["dist_to_land_km"] = float(np.float32(47.378465060301394))
            a[b.index(r)]["dist_to_land_km"] = 47.378465060301394
        self.assertEqual(sar_diff.diff_rows(a, b), [])

    def test_stored_table_with_a_missing_row(self):
        st, rows = run(self.image, self.land)
        stored = rows[1:]
        recs = sar_diff.compare_to_stored(stored, rows, st)
        self.assertEqual([r["kind"] for r in recs], ["only_b"])
        self.assertEqual(recs[0]["stage"], "unknown")


class MatchAndSummary(unittest.TestCase):
    def row(self, r, c, **kw):
        return {"grid_row": r, "grid_col": c, "latitude": 0.0, "longitude": 0.0,
                "dist_to_land_km": 5.0, "snr": 30.0, "vessel_sized": True,
                "reject_reason": "", **kw}

    def test_match_is_one_to_one_and_nearest_first(self):
        a = [self.row(10, 10), self.row(10, 11)]
        b = [self.row(10, 11)]
        pairs, only_a, only_b = sar_diff.match(a, b)
        self.assertEqual((pairs, only_a, only_b), ([(1, 0)], [0], []))

    def test_beyond_tolerance_is_not_a_match(self):
        pairs, only_a, only_b = sar_diff.match([self.row(10, 10)], [self.row(10, 12)])
        self.assertEqual((pairs, only_a, only_b), ([], [0], [0]))

    def test_one_cause_is_one_fingerprint(self):
        a = [self.row(10, i * 10) for i in range(5)]
        recs = sar_diff.diff_rows(a, [])
        for r in recs:
            r["scene_id"], r["slot"] = "S1C_x", "02:1"
        s = sar_diff.summarise(recs, 1)
        self.assertEqual(len(s["fingerprints"]), 1)
        fp = s["fingerprints"][0]
        self.assertEqual((fp["kind"], fp["band_km"], fp["snr"], fp["count"]),
                         ("only_a", "3-10", ">20", 5))
        self.assertEqual(len(fp["examples"]), sar_diff.EXAMPLES)

    def test_bands_are_the_survey_bands(self):
        self.assertEqual([sar_diff.band_label(d) for d in (0.1, 0.5, 2, 5, 20)],
                         ["<0.2", "0.2-1", "1-3", "3-10", "10+"])


if __name__ == "__main__":
    unittest.main()
