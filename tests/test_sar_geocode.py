"""The read path has to put a target where the target is.

This is the failure mode that does not announce itself. A Sentinel-1 GRD
carries GCPs and no CRS, so `rasterio.open()` reports an identity transform;
code that trusts it gets pixel indices dressed up as degrees, and code that
hands `WarpedVRT` its own destination grid alongside GCPs gets a constant
image back with no exception at all. Both were hit while writing this. A
synthetic scene whose answer is known catches them offline, with no network.
"""

import os
import sys
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sar_scene  # noqa: E402

# A small patch of sea somewhere the real AOI does not reach, so nothing here
# can accidentally pass by matching the production constants.
WEST, SOUTH, EAST, NORTH = 10.0, 40.0, 10.4, 40.4
RES = 0.0002
# Where the bright target sits, in the synthetic scene's own pixel space.
TARGET_ROW, TARGET_COL = 600, 900


def _synthetic_scene(path, rotate=True):
    """A GCP-referenced image with one bright square, and its true position."""
    height, width = 1000, 1500
    image = np.full((height, width), 50, dtype=np.uint16)
    image[TARGET_ROW - 2:TARGET_ROW + 3, TARGET_COL - 2:TARGET_COL + 3] = 9000

    # A deliberately non-axis-aligned mapping: a scene that happens to be
    # north-up would let a transposed or identity transform look right.
    def to_lonlat(row, col):
        u, v = col / width, row / height
        lon = WEST + 0.32 * u + (0.05 * v if rotate else 0.0)
        lat = NORTH - 0.32 * v + (0.04 * u if rotate else 0.0)
        return lon, lat

    gcps = [GroundControlPoint(row=r, col=c, x=to_lonlat(r, c)[0], y=to_lonlat(r, c)[1])
            for r in np.linspace(0, height - 1, 11)
            for c in np.linspace(0, width - 1, 11)]
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height,
                       count=1, dtype="uint16", gcps=gcps, crs="EPSG:4326",
                       nodata=0) as dst:
        dst.write(image, 1)
    return to_lonlat(TARGET_ROW, TARGET_COL)


class ReadPlacesTargetsCorrectly(unittest.TestCase):
    def setUp(self):
        self._saved = (sar_scene.AOI_WEST, sar_scene.AOI_SOUTH,
                       sar_scene.AOI_EAST, sar_scene.AOI_NORTH, sar_scene.AOI_RES_DEG)
        (sar_scene.AOI_WEST, sar_scene.AOI_SOUTH, sar_scene.AOI_EAST,
         sar_scene.AOI_NORTH, sar_scene.AOI_RES_DEG) = (WEST, SOUTH, EAST, NORTH, RES)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "scene.tif")

    def tearDown(self):
        (sar_scene.AOI_WEST, sar_scene.AOI_SOUTH, sar_scene.AOI_EAST,
         sar_scene.AOI_NORTH, sar_scene.AOI_RES_DEG) = self._saved
        self.tmp.cleanup()

    def _read(self):
        image, covered = sar_scene.read_aoi(self.path)
        transform, width, height = sar_scene.aoi_grid()
        self.assertEqual(image.shape, (height, width))
        return image, covered, transform

    def test_the_target_lands_where_the_gcps_say_it_does(self):
        true_lon, true_lat = _synthetic_scene(self.path)
        image, covered, transform = self._read()
        self.assertGreater(covered, 0.5, "the synthetic scene should fill the AOI")

        row, col = np.unravel_index(int(np.argmax(image)), image.shape)
        lon, lat = transform * (col + 0.5, row + 0.5)
        # One grid pixel is 0.0002 deg; allow two for the resampling.
        self.assertAlmostEqual(lon, true_lon, delta=2 * RES,
                               msg=f"target at {lon:.5f} not {true_lon:.5f}")
        self.assertAlmostEqual(lat, true_lat, delta=2 * RES,
                               msg=f"target at {lat:.5f} not {true_lat:.5f}")

    def test_the_image_is_not_flat(self):
        # The observed failure was not a wrong position but a constant image:
        # every pixel between 42 and 45 where the scene ran from 0 to 27,000.
        # A position assertion alone can pass vacuously if argmax picks noise,
        # so the contrast is checked on its own.
        _synthetic_scene(self.path)
        image, _, _ = self._read()
        sea = image[image > 0]
        self.assertGreater(image.max(), 10 * float(np.median(sea)),
                           "no contrast survived the warp")

    def test_only_the_overlap_is_read(self):
        """A scene is 700 MB; the AOI is a corner of it.

        Nothing else here fails if the window is dropped and the whole warped
        scene is read instead — the numbers come out the same, only slowly and
        at a hundred times the bytes. So the window is asserted on its own.
        """
        _synthetic_scene(self.path)
        # An AOI a twentieth of the scene across, inside it.
        sar_scene.AOI_WEST, sar_scene.AOI_EAST = 10.10, 10.12
        sar_scene.AOI_NORTH, sar_scene.AOI_SOUTH = 40.30, 40.28
        with rasterio.open(self.path) as src:
            gcps, gcp_crs = src.gcps
            with WarpedVRT(src, src_crs=gcp_crs, crs="EPSG:4326",
                           resampling=Resampling.average) as vrt:
                window = sar_scene.aoi_window(vrt, sar_scene.aoi_bounds())
                self.assertIsNotNone(window)
                share = (window.width * window.height) / (vrt.width * vrt.height)
                self.assertLess(share, 0.05,
                                f"the window is {share:.1%} of the scene; "
                                f"the read is not being cut down")
                for value in (window.col_off, window.row_off,
                              window.width, window.height):
                    self.assertEqual(value, int(value),
                                     "a fractional window and the transform "
                                     "built from it will disagree")

    def test_a_scene_that_misses_the_aoi_reads_as_empty(self):
        _synthetic_scene(self.path)
        sar_scene.AOI_WEST, sar_scene.AOI_EAST = 100.0, 100.4
        image, covered, _ = self._read()
        self.assertEqual(covered, 0.0)
        self.assertEqual(int(image.max()), 0)


if __name__ == "__main__":
    unittest.main()
