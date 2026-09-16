"""Tests for the published map page.

The page is served from docs/, which GitHub Pages treats as the site root, so
it cannot reach data/ and needs its own copy of the land mask. A copy that
drifts is worse than no copy: the map would draw a coastline that no longer
matches the one the rendered snapshots are drawn on, and nothing would say so.
"""

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "index.html"
MASK = ROOT / "data" / "land_mask.geojson"
MASK_COPY = ROOT / "docs" / "land_mask.geojson"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LandMaskCopyTest(unittest.TestCase):
    def test_the_page_uses_the_same_coastline_as_the_snapshots(self):
        self.assertTrue(MASK_COPY.exists(), "docs/land_mask.geojson is missing")
        self.assertEqual(sha(MASK), sha(MASK_COPY),
                         "docs/land_mask.geojson has drifted from data/")

    def test_the_page_asks_for_it_by_a_path_that_resolves_from_docs(self):
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("fetch('land_mask.geojson')", html)
        self.assertNotIn("../data/", html, "docs/ is the site root; ../ escapes it")


class PageTest(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_the_sampling_caveat_is_on_the_page_itself(self):
        """Not only in the repository. Someone who opens the map and reads
        nothing else still has to be told."""
        for phrase in ("samples, not tracks", "not observed",
                       "unknown, not absent"):
            self.assertIn(phrase, self.html)

    def test_no_tile_provider_is_depended_on(self):
        """Every keyless dark tile service either wants a key now or will.
        The coastline this repository already ships cannot expire."""
        for host in ("basemaps.cartocdn.com", "tile.openstreetmap.org",
                     "stadiamaps.com", "mapbox.com"):
            self.assertNotIn(host, self.html)

    def test_third_party_code_is_pinned_to_an_exact_version(self):
        for url in re.findall(r"https://cdn\.jsdelivr\.net/npm/([^/'\"]+)", self.html):
            self.assertRegex(url, r"@\d+\.\d+\.\d+$", f"{url} is not pinned")
        for url in re.findall(r"https://unpkg\.com/([^/'\"]+)", self.html):
            self.assertRegex(url, r"@\d+\.\d+\.\d+$", f"{url} is not pinned")

    def test_the_bounding_box_matches_the_collector(self):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        import types
        sys.modules.setdefault("land_filter", types.ModuleType("land_filter"))
        sys.modules["land_filter"].is_on_land = lambda lat, lon: False
        import ais_parse
        (lat0, lon0), (lat1, lon1) = ais_parse.BBOX
        self.assertIn(f"[[{lat0}, {lon0}], [{lat1}, {lon1}]]", self.html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
