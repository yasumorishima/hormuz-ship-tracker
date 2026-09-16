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
SCRIPT = ROOT / "docs" / "map.js"
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
        js = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("fetch('land_mask.geojson')", js)
        for text in (js, PAGE.read_text(encoding="utf-8")):
            self.assertNotIn("../data/", text,
                             "docs/ is the site root; ../ escapes it")


class PageTest(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        self.js = SCRIPT.read_text(encoding="utf-8")
        self.both = self.html + chr(10) + self.js

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
            self.assertNotIn(host, self.both)

    def test_third_party_code_is_pinned_to_an_exact_version(self):
        found = 0
        for host in (r"https://cdn\.jsdelivr\.net/npm/", r"https://unpkg\.com/"):
            for url in re.findall(host + r"([^/'\"]+)", self.both):
                self.assertRegex(url, r"@\d+\.\d+\.\d+$", f"{url} is not pinned")
                found += 1
        self.assertGreaterEqual(found, 3, "the imports vanished; this checked nothing")

    def test_the_bounding_box_matches_the_collector(self):
        """Read the collector's box out of its source rather than importing it.

        Importing `ais_parse` here would need a `land_filter` stub, and a stub
        left in `sys.modules` is exactly the leak that made the window-loop
        tests depend on a neighbouring module's global. A regex over one
        literal is cheaper than that, and cannot leak.
        """
        source = (ROOT / "src" / "ais_parse.py").read_text(encoding="utf-8")
        m = re.search(r"^BBOX = (\[\[.+\]\])$", source, re.M)
        self.assertIsNotNone(m, "BBOX is no longer a literal in ais_parse.py")
        self.assertIn(m.group(1), self.js,
                      "the page's bounding box has drifted from the collector's")


    def test_no_script_runs_inline(self):
        """The policy forbids inline script on purpose.

        An inline module would need 'unsafe-inline', which is exactly what
        would let an injected <img onerror=...> run — the thing the policy is
        here to stop. So the module lives in map.js.
        """
        self.assertIn('<script type="module" src="map.js">', self.html)
        self.assertNotIn('<script type="module">', self.html)

    def policy(self):
        """The directives themselves, not the prose around them. The first
        version of this test read the whole document and tripped over the word
        in its own explanatory comment."""
        m = re.search(
            r'http-equiv="Content-Security-Policy"\s+content="([^"]*)"', self.html)
        self.assertIsNotNone(m, "the page declares no Content-Security-Policy")
        return " ".join(m.group(1).split())

    def test_the_policy_allows_webassembly_but_not_eval(self):
        """hyparquet-compressors decodes through a WebAssembly module, which a
        policy without this directive blocks outright — measured, the page hung
        at "Loading positions" until it was added."""
        policy = self.policy()
        self.assertIn("'wasm-unsafe-eval'", policy)
        self.assertNotIn("'unsafe-eval'", policy.replace("'wasm-unsafe-eval'", ""))
        self.assertIn("default-src 'none'", policy)

    def test_the_policy_forbids_inline_script(self):
        """'unsafe-inline' in script-src would undo the reason for the policy."""
        script_src = [d for d in self.policy().split(";")
                      if d.strip().startswith("script-src")]
        self.assertEqual(len(script_src), 1, self.policy())
        self.assertNotIn("'unsafe-inline'", script_src[0])

    def test_values_from_the_dataset_are_escaped_before_they_become_markup(self):
        """A file name in the listing reaches the status line. Nothing outside
        this page may arrive as markup."""
        self.assertIn("const esc =", self.js)
        for expr in ("esc(source.path)", "esc(e.message)", "esc(v.ship_name",
                     "esc(val)"):
            self.assertIn(expr, self.js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
