"""Which scenes a run takes.

The first real run found five scenes, took the newest four, and the fifth aged
out of the window before the next run: a gap nothing would ever fill. These
pin the order that prevents it.
"""

import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
