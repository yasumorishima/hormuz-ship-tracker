"""The detector, on sea it cannot have been tuned to.

The easy version of this test — scatter very bright dots on a flat field and
check they are found — stays green after the local background is replaced by
one number for the whole image, which is the change that would break the
detector on a real scene. Sentinel-1 brightness falls across the swath, so the
field here runs from 40 to 120 DN and every target is the same multiple of the
sea beneath it. Measured: with the local background all six are found; with a
single global one, three are, because the threshold is then set by the bright
end and the dim end goes deaf. The false-alarm count does not separate the two
and is asserted on its own terms.
"""

import os
import sys
import unittest

import numpy as np
from affine import Affine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sar_detect  # noqa: E402

PIXEL_M = (22.2, 19.9)   # a degree grid's cells are not square
SHAPE = (1200, 1200)
LOOKS = 4          # GRD is multi-looked; intensity is roughly Gamma(L)
TRANSFORM = Affine(0.0002, 0.0, 55.0, 0.0, -0.0002, 26.0)
# (row, col, half-size in pixels, brightness as a multiple of the local sea)
# Same gain everywhere on purpose: a target that is bright in absolute terms
# would be found by any threshold and would prove nothing about the background.
GAIN = 5.0
TARGETS = [(200, 200, 1, GAIN), (300, 900, 1, GAIN), (600, 300, 2, GAIN),
           (700, 1000, 2, GAIN), (900, 150, 3, GAIN), (1000, 800, 3, GAIN)]


def sea(seed=20260917):
    """Speckle with a left-to-right ramp, as amplitude in DN."""
    rng = np.random.default_rng(seed)
    ramp = np.linspace(40.0, 120.0, SHAPE[1], dtype=np.float32)[None, :]
    intensity = rng.gamma(LOOKS, 1.0 / LOOKS, size=SHAPE).astype(np.float32)
    return (ramp * np.sqrt(intensity)), ramp


def scene_with_targets(seed=20260917):
    image, ramp = sea(seed)
    truth = []
    for row, col, half, gain in TARGETS:
        image[row - half:row + half + 1, col - half:col + half + 1] = ramp[0, col] * gain
        truth.append((row, col))
    return np.rint(image).astype(np.uint16), truth


def run(image, land=None, **kwargs):
    if land is None:
        land = np.zeros(SHAPE, dtype=bool)
    return sar_detect.detect(image, land, TRANSFORM, PIXEL_M, **kwargs)


def near(rows, row, col, tol=3):
    return [r for r in rows
            if abs(r["grid_row"] - row) <= tol and abs(r["grid_col"] - col) <= tol]


class DetectorFindsShipsAndNotSea(unittest.TestCase):
    def setUp(self):
        self.image, self.truth = scene_with_targets()

    def test_every_planted_target_is_found(self):
        """Recall is what a global background costs: measured 6/6 against 3/6."""
        rows, _ = run(self.image, coast_buffer_m=0.0)
        # Accepted ones only: a target found and then rejected on shape is not
        # found, and counting the rejects would hide exactly that.
        accepted = [r for r in rows if r["vessel_sized"]]
        found = [t for t in self.truth if near(accepted, *t)]
        self.assertEqual(len(found), len(self.truth),
                         f"missed {set(self.truth) - set(found)}")

    def test_false_alarms_stay_rare_across_the_ramp(self):
        """Speckle alone must not be reported, on either end of the ramp.

        This one does not distinguish a local background from a global one —
        measured, both give zero here — so it is not evidence for that. It is
        evidence that K_SIGMA is not so low that sea becomes ships.
        """
        rows, stats = run(self.image, coast_buffer_m=0.0)
        spurious = [r for r in rows if not any(near([r], *t) for t in self.truth)]
        left = sum(r["grid_col"] < SHAPE[1] // 2 for r in spurious)
        right = len(spurious) - left
        self.assertLessEqual(len(spurious), 20,
                             f"{len(spurious)} false alarms ({left} dim side, "
                             f"{right} bright side) in {SHAPE[0] * SHAPE[1]} pixels")
        self.assertLessEqual(right, 15, f"{right} false alarms on the bright side")

    def test_the_background_follows_the_ramp(self):
        """Not a metric, the mechanism: the estimate has to move with the sea."""
        image, ramp = sea()
        valid = np.ones(SHAPE, dtype=bool)
        bg, sd = sar_detect.background(image, valid)
        for col in (100, 600, 1100):
            self.assertAlmostEqual(float(bg[:, col].mean()) / ramp[0, col], 0.94,
                                   delta=0.06, msg=f"background off at column {col}")
        self.assertGreater(sd[:, 1100].mean(), 1.8 * sd[:, 100].mean(),
                           "spread should scale with the sea, not be constant")

    def test_land_is_not_a_ship(self):
        image, _ = scene_with_targets()
        land = np.zeros(SHAPE, dtype=bool)
        land[100:400, 400:700] = True
        image[100:400, 400:700] = 4000        # a bright ridge
        rows, stats = run(image, land, coast_buffer_m=200.0)
        inside = [r for r in rows if 100 <= r["grid_row"] < 400 and 400 <= r["grid_col"] < 700]
        self.assertEqual(inside, [], "detections were reported on masked land")
        self.assertLess(stats["scored_water_km2"],
                        SHAPE[0] * SHAPE[1] * PIXEL_M[0] * PIXEL_M[1] / 1e6)

    def test_rejects_are_kept_with_a_reason(self):
        """A table that only holds what passed cannot be re-judged later."""
        image, _ = scene_with_targets()
        image[500:500 + 40, 500:500 + 2] = 2000        # 800 m long: not a ship
        rows, stats = run(image, coast_buffer_m=0.0)
        long_ones = [r for r in rows if r["length_m"] > sar_detect.MAX_LENGTH_M]
        self.assertTrue(long_ones, "the 800 m bar should have been a candidate")
        self.assertTrue(all(not r["vessel_sized"] and r["reject_reason"] == "too_long"
                            for r in long_ones))
        self.assertEqual(stats["n_vessel_sized"], sum(r["vessel_sized"] for r in rows))
        self.assertGreater(stats["n_candidates"], stats["n_vessel_sized"])

    def test_coordinates_come_back_on_the_given_grid(self):
        rows, _ = run(self.image, coast_buffer_m=0.0)
        for r in rows:
            lon, lat = TRANSFORM * (r["grid_col"], r["grid_row"])
            self.assertAlmostEqual(r["longitude"], lon, delta=0.0002)
            self.assertAlmostEqual(r["latitude"], lat, delta=0.0002)

    def test_nothing_is_reported_where_nothing_was_measured(self):
        image, _ = scene_with_targets()
        image[:, :600] = 0                       # outside the swath
        rows, stats = run(image, coast_buffer_m=0.0)
        self.assertEqual([r for r in rows if r["grid_col"] < 600], [])
        self.assertAlmostEqual(stats["scored_water_km2"],
                               SHAPE[0] * 600 * PIXEL_M[0] * PIXEL_M[1] / 1e6, delta=1.0)


if __name__ == "__main__":
    unittest.main()
