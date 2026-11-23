"""Which scenes a run takes.

The first real run found five scenes, took the newest four, and the fifth aged
out of the window before the next run: a gap nothing would ever fill. These
pin the order that prevents it.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sar_collect  # noqa: E402


def scene(day, hhmm):
    return {"scene_id": f"S1D_{day}{hhmm}", "acq_time": f"2026-09-{day}T{hhmm[:2]}:{hhmm[2:]}:00Z"}


class SelectTest(unittest.TestCase):
    def setUp(self):
        # newest first, as the search returns them
        self.scenes = [scene("16", "0206"), scene("16", "0207"),
                       scene("15", "0215"), scene("15", "0214"), scene("15", "0213")]

    def test_the_oldest_is_taken_first(self):
        """The oldest is the one about to leave the window."""
        todo = sar_collect.select(self.scenes, set(), 4)
        self.assertEqual([s["scene_id"] for s in todo],
                         ["S1D_150213", "S1D_150214", "S1D_150215", "S1D_160206"])

    def test_what_is_done_is_skipped_before_the_cap(self):
        done = {"S1D_150213", "S1D_150214"}
        todo = sar_collect.select(self.scenes, done, 2)
        self.assertEqual([s["scene_id"] for s in todo], ["S1D_150215", "S1D_160206"])

    def test_the_window_outlasts_a_long_outage(self):
        """Two weeks down must still heal by itself."""
        self.assertGreaterEqual(sar_collect.DEFAULT_HOURS, 14 * 24)
        # about 1.4 scenes a day reach the AOI; a run must take more than a day's worth
        self.assertGreater(sar_collect.DEFAULT_MAX_SCENES, 3)


class PlacesRefreshTest(unittest.TestCase):
    """The map's "recurs here" table is rebuilt after a run that wrote a scene;
    a failed rebuild is a failed run, because a stale table flags old places."""

    def run_main(self, refresh, argv=()):
        row = {k: 0 for k in ("scene_id", "acq_time", "orbit_state", "aoi_covered_frac",
                              "scored_water_km2", "sea_median_dn", "n_candidates",
                              "n_vessel_sized", "runtime_s", "status")}
        with mock.patch.object(sar_collect.sar_scene, "search", return_value=[scene("16", "0206")]), \
                mock.patch.object(sar_collect.sar_store, "list_files", return_value=[]), \
                mock.patch.object(sar_collect.sar_scene, "load_land_mask", return_value=None), \
                mock.patch.object(sar_collect, "process", return_value=row), \
                mock.patch.object(sar_collect.sar_places, "refresh", refresh), \
                mock.patch("builtins.print"):
            return sar_collect.main(list(argv))

    def test_a_run_that_wrote_a_scene_rebuilds_the_table(self):
        refresh = mock.Mock(return_value={"places": 1, "recurring": 0, "passes": 1})
        self.assertEqual(self.run_main(refresh), 0)
        refresh.assert_called_once()

    def test_a_failed_rebuild_turns_the_run_red(self):
        self.assertEqual(self.run_main(mock.Mock(side_effect=OSError("hub down"))), 1)

    def test_a_dry_run_writes_no_table(self):
        refresh = mock.Mock()
        self.assertEqual(self.run_main(refresh, ["--dry-run"]), 0)
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
