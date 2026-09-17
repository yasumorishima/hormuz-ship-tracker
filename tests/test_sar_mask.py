"""The committed coastline, checked where it is known to be hard.

The file exists because the mask the AIS side uses is too coarse for imagery:
Natural Earth 10m generalises the Musandam fjords away, so their water reads
as land and the ridges beside them read as sea. Measured on one scene, within
a kilometre of the shore, that difference is 163 vessel-sized objects per
100 km² against 36. The point at the head of Khawr ash Shamm below is where the
two masks disagree, and it is asserted in both directions so that swapping the
file back would be caught.
"""

import os
import sys
import unittest

import numpy as np
import rasterio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sar_scene  # noqa: E402
from sar_columns import AOI_EAST, AOI_NORTH, AOI_SOUTH, AOI_WEST  # noqa: E402

# lon, lat. Each was read off the mask before being written down here.
#
# The first group is deep inside its own kind: those points say the mask is
# the right mask. The second group sits 250 to 450 m from the coastline, and
# those are the ones that say it is in the right place — a mask shifted by a
# kilometre keeps every deep point correct and moves every edge point across.
LAND = [("Musandam ridge", 56.28, 26.18), ("Qeshm island", 55.85, 26.85),
        ("Hormuz island", 56.46, 27.06), ("Larak island", 56.36, 26.87),
        ("Khasab town", 56.24, 26.20), ("Bandar Abbas shore", 56.28, 27.18),
        # 389 m and 255 m inside their own coasts
        ("north of Hormuz island", 56.4525, 27.1947),
        ("west Musandam shore", 56.3911, 25.9057)]
WATER = [("strait centre", 56.40, 26.55), ("inbound lane", 56.55, 26.62),
         ("Gulf of Oman", 57.20, 25.80),
         # 410 m and 292 m off their nearest land
         ("off the Musandam coast", 56.3315, 26.2837),
         ("inshore of Ras al Khaimah", 56.3157, 25.7591)]
# Water, about 130 m from the rock on either side, inside a fjord.
FJORD = ("Khawr ash Shamm", 56.35, 26.20)


class TheMaskCoversTheAoi(unittest.TestCase):
    def test_the_file_is_the_aoi_box(self):
        with rasterio.open(sar_scene.MASK_PATH) as src:
            for got, want, name in ((src.bounds.left, AOI_WEST, "west"),
                                    (src.bounds.right, AOI_EAST, "east"),
                                    (src.bounds.top, AOI_NORTH, "north"),
                                    (src.bounds.bottom, AOI_SOUTH, "south")):
                self.assertAlmostEqual(got, want, places=9, msg=name)
            _, width, height = sar_scene.aoi_grid()
            self.assertEqual(src.width % width, 0)
            self.assertEqual(src.height % height, 0)
            self.assertEqual(str(src.crs), "EPSG:4326")

    def test_a_mask_of_everything_or_nothing_is_caught(self):
        land = sar_scene.load_land_mask()
        # Measured 0.3693 on the shipped file. The band is wide enough for a
        # coastline revision and narrow enough that an inverted mask (0.63)
        # or an empty one fails.
        self.assertGreater(land.mean(), 0.30)
        self.assertLess(land.mean(), 0.45)


class TheMaskAgreesWithTheMapWhereItMatters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.land = sar_scene.load_land_mask()
        cls.transform = sar_scene.aoi_grid()[0]

    def at(self, lon, lat):
        col, row = ~self.transform * (lon, lat)
        return bool(self.land[int(row), int(col)])

    def test_the_islands_and_the_shore_are_land(self):
        for name, lon, lat in LAND:
            self.assertTrue(self.at(lon, lat), f"{name} came back as water")

    def test_the_lanes_are_water(self):
        for name, lon, lat in WATER:
            self.assertFalse(self.at(lon, lat), f"{name} came back as land")

    def test_the_fjord_is_water_here_and_land_on_the_ais_mask(self):
        """Both halves matter.

        The first says the new mask resolves the inlet. The second says the
        old one does not, which is the whole reason for a second file; if the
        AIS mask were ever quietly good enough, this half would fail and the
        file could be dropped.
        """
        # The AIS mask is read straight from its GeoJSON rather than through
        # src/land_filter.py: tests/test_ais_parse.py replaces that module in
        # sys.modules with a stub that answers False to everything, and under
        # discovery the stub is what any later import gets. Asking the stub
        # would make this assertion pass for the wrong reason, or fail for it.
        import json

        from shapely.geometry import Point, shape
        from shapely.ops import unary_union

        geojson = os.path.join(os.path.dirname(__file__), "..", "data",
                               "land_mask.geojson")
        with open(geojson) as handle:
            ais_land = unary_union([shape(f["geometry"])
                                    for f in json.load(handle)["features"]])

        name, lon, lat = FJORD
        self.assertFalse(self.at(lon, lat), f"{name} should be water here")
        self.assertTrue(ais_land.contains(Point(lon, lat)),
                        f"{name} is no longer land on the AIS mask; the two "
                        f"masks now agree and this file may be redundant")


class ReducingToTheGridKeepsTheRock(unittest.TestCase):
    def test_a_single_fine_pixel_of_land_survives(self):
        """Nearest-neighbour would drop it; the block maximum must not.

        A rock small enough to vanish is exactly the thing that then shows up
        as a permanent vessel sitting in the same spot on every pass.
        """
        import tempfile
        from affine import Affine

        fine = np.zeros((40, 40), dtype=np.uint8)
        fine[7, 13] = 1                      # one fine pixel, off both centres
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mask.tif")
            with rasterio.open(path, "w", driver="GTiff", width=40, height=40,
                               count=1, dtype="uint8", crs="EPSG:4326",
                               transform=Affine(0.0001, 0, AOI_WEST,
                                                0, -0.0001, AOI_NORTH)) as dst:
                dst.write(fine, 1)
            saved = (sar_scene.AOI_EAST, sar_scene.AOI_SOUTH, sar_scene.AOI_RES_DEG)
            try:
                sar_scene.AOI_EAST = AOI_WEST + 40 * 0.0001
                sar_scene.AOI_SOUTH = AOI_NORTH - 40 * 0.0001
                sar_scene.AOI_RES_DEG = 0.0002
                coarse = sar_scene.load_land_mask(path)
            finally:
                (sar_scene.AOI_EAST, sar_scene.AOI_SOUTH,
                 sar_scene.AOI_RES_DEG) = saved
        self.assertEqual(coarse.shape, (20, 20))
        self.assertEqual(int(coarse.sum()), 1, "the rock was lost in the reduction")
        self.assertTrue(coarse[3, 6])


if __name__ == "__main__":
    unittest.main()
