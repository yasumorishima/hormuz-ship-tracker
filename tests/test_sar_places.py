"""Tests for the places table behind the map's "recurs here" flag.

The rule is docs/sar_recur_flag.md: the map's default layer, linked at 100 m,
flagged at 10 passes from 2 or more slots.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    import pandas as pd

    import sar_places
except ImportError:          # pragma: no cover - the runner treats a skip as red
    pd = None


def scene_id(day: int, hh: str, platform: str = "S1C") -> str:
    # Same layout as a Planetary Computer item id: start time at [17:32].
    return f"{platform}_IW_GRDH_1SDV_202610{day:02d}T{hh}0000_202610{day:02d}T{hh}0030_000000_000000"


def tables(points):
    """points: (day, slot 'HH', lon, lat, snr, dist_km, vessel_sized)."""
    det, scenes = [], {}
    for day, hh, lon, lat, snr, dist, vs in points:
        sid = scene_id(day, hh)
        scenes[sid] = {"scene_id": sid, "status": "ok",
                       "acq_time": f"2026-10-{day:02d}T{hh}:{'14' if hh == '02' else '24'}:00Z"}
        det.append({"scene_id": sid, "longitude": lon, "latitude": lat, "snr": snr,
                    "dist_to_land_km": dist, "vessel_sized": vs})
    return pd.DataFrame(det), pd.DataFrame(scenes.values())


@unittest.skipIf(pd is None, "pandas not installed")
class PlacesTest(unittest.TestCase):
    LON, LAT = 56.0, 26.3

    def test_a_spot_seen_in_ten_passes_from_two_slots_is_flagged(self):
        pts = [(d, "02" if d % 2 else "14", self.LON + 0.0002 * (d % 3), self.LAT, 50, 20, True)
               for d in range(1, 11)]
        places = sar_places.build(*tables(pts))
        self.assertEqual(len(places), 1)
        self.assertEqual(int(places.n_passes[0]), 10)
        self.assertEqual(int(places.n_slots[0]), 2)
        self.assertTrue(bool(places.recurring[0]))

    def test_nine_passes_is_not_enough(self):
        pts = [(d, "02" if d % 2 else "14", self.LON, self.LAT, 50, 20, True) for d in range(1, 10)]
        self.assertFalse(bool(sar_places.build(*tables(pts)).recurring[0]))

    def test_one_slot_only_is_never_flagged_however_often(self):
        """Slot-bound recurrence has no known cause; it must not be called a
        structure (docs/sar_recur_flag.md)."""
        pts = [(d, "02", self.LON, self.LAT, 50, 20, True) for d in range(1, 21)]
        places = sar_places.build(*tables(pts))
        self.assertEqual(int(places.n_passes[0]), 20)
        self.assertFalse(bool(places.recurring[0]))

    def test_only_the_maps_default_layer_counts(self):
        weak = [(d, "02" if d % 2 else "14", self.LON, self.LAT, 15, 20, True) for d in range(1, 12)]
        near = [(d, "02" if d % 2 else "14", self.LON, self.LAT, 50, 2.0, True) for d in range(1, 12)]
        odd = [(d, "02" if d % 2 else "14", self.LON, self.LAT, 50, 20, False) for d in range(1, 12)]
        for pts in (weak, near, odd):
            self.assertEqual(len(sar_places.build(*tables(pts))), 0)

    def test_two_detections_in_one_pass_count_as_one_pass(self):
        pts = [(1, "02", self.LON, self.LAT, 50, 20, True),
                (1, "02", self.LON + 0.0003, self.LAT, 50, 20, True),
                (3, "14", self.LON, self.LAT, 50, 20, True)]
        places = sar_places.build(*tables(pts))
        self.assertEqual(int(places.n_passes[0]), 2)
        self.assertEqual(int(places.n_detections[0]), 3)

    def test_spots_further_apart_than_the_link_are_separate_places(self):
        far = 0.3 / sar_places.KM_PER_DEG_LON        # 300 m east
        pts = [(d, "02" if d % 2 else "14", self.LON + (far if d > 5 else 0), self.LAT, 50, 20, True)
               for d in range(1, 11)]
        self.assertEqual(len(sar_places.build(*tables(pts))), 2)

    def test_a_scene_that_did_not_look_contributes_nothing(self):
        det, scenes = tables([(d, "02" if d % 2 else "14", self.LON, self.LAT, 50, 20, True)
                              for d in range(1, 11)])
        scenes.loc[0, "status"] = "no_overlap"
        self.assertEqual(int(sar_places.build(det, scenes).n_passes[0]), 9)

    def test_the_chance_check_scatters_a_fixed_spot(self):
        pts = [(d, "02" if d % 2 else "14", self.LON, self.LAT, 50, 20, True) for d in range(1, 21)]
        det, scenes = tables(pts)
        self.assertTrue(bool(sar_places.build(det, scenes).recurring[0]))
        ctrl = sar_places.chance(det, scenes)
        self.assertFalse(bool(ctrl.recurring.any()) if len(ctrl) else False)


if __name__ == "__main__":
    unittest.main()
