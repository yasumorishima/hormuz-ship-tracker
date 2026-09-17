"""The half of the design that has no imagery in it.

"A scene is done when its scene row exists" is the whole idempotency story,
and it is three string operations wide. It needs a test for the same reason
the AIS side's store has one: nothing else in the suite would notice if
`processed()` started answering about the wrong version, or if a column the
detector stopped producing turned into a column of nulls that reads as
"measured, and empty".

Nothing here reaches the network.
"""

import os
import sys
import unittest

import pyarrow as pa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sar_store  # noqa: E402
from sar_columns import DETECTION_COLUMNS, DETECTOR_VERSION, SCENE_COLUMNS  # noqa: E402

SCENE = {
    "scene_id": "S1C_IW_GRDH_1SDV_20260916T020601_20260916T020631_009466_012D3D",
    "acq_time": "2026-09-16T02:06:16.487168Z", "platform": "SENTINEL-1C",
    "orbit_state": "descending", "polarization": "VV",
}
DETECTION = {
    "latitude": 26.55, "longitude": 56.40, "grid_row": 3750, "grid_col": 4000,
    "dist_to_land_km": 11.9, "area_px": 42, "length_m": 210.0, "width_m": 33.0,
    "orientation_deg": 12.5, "peak_dn": 4100.0, "mean_dn": 900.0,
    "bg_median_dn": 49.0, "bg_mad_dn": 8.0, "snr": 341.0,
    "is_vessel": True, "reject_reason": "",
}


class PathsAreTheLedger(unittest.TestCase):
    def test_a_scene_is_done_when_its_scene_row_exists(self):
        files = ["positions.parquet",
                 f"sar/scenes/{DETECTOR_VERSION}/alpha.parquet",
                 f"sar/det/{DETECTOR_VERSION}/alpha.parquet",
                 f"sar/det/{DETECTOR_VERSION}/beta.parquet"]
        # beta has detections but no scene row: the upload did not finish, so
        # it is not done. Keying on the detection table would call it done and
        # never come back to it.
        self.assertEqual(sar_store.processed(files), {"alpha"})

    def test_another_detector_version_is_not_this_one(self):
        files = ["sar/scenes/v1/alpha.parquet", "sar/scenes/v2/beta.parquet"]
        self.assertEqual(sar_store.processed(files, version="v1"), {"alpha"})
        self.assertEqual(sar_store.processed(files, version="v2"), {"beta"})

    def test_the_paths_carry_the_version(self):
        self.assertEqual(sar_store.det_path("x", "v3"), "sar/det/v3/x.parquet")
        self.assertEqual(sar_store.scene_path("x", "v3"), "sar/scenes/v3/x.parquet")

    def test_nothing_of_the_ais_side_is_claimed(self):
        # The AIS jobs select by prefix; if these two ever started with raw/ or
        # daily/ the SAR rows would be read as vessel positions.
        for path in (sar_store.det_path("x"), sar_store.scene_path("x")):
            self.assertTrue(path.startswith("sar/"))


class RowsBecomeTablesWithoutInventingAnything(unittest.TestCase):
    def scene_row(self, **over):
        row = sar_store.scene_row(
            SCENE, {"scored_water_km2": 3538.9, "sea_median_dn": 49.0,
                    "n_candidates": 84, "n_vessels": 43},
            covered=0.611, runtime_s=61.0, mask_source="ESA WorldCover")
        row.update(over)
        return row

    def test_a_detection_carries_the_scene_it_came_from(self):
        rows = sar_store.decorate([dict(DETECTION)], SCENE)
        self.assertEqual(sorted(rows[0]), sorted(DETECTION_COLUMNS))
        self.assertEqual(rows[0]["scene_id"], SCENE["scene_id"])
        self.assertEqual(rows[0]["detector_version"], DETECTOR_VERSION)

    def test_a_scene_row_is_complete(self):
        self.assertEqual(sorted(self.scene_row()), sorted(SCENE_COLUMNS))

    def test_a_dropped_column_is_refused_rather_than_nulled(self):
        """The failure this replaces: a quiet column of nulls.

        `row.get(name)` would make a field the detector stopped producing
        into a column that reads as measured and empty, and every consumer
        downstream would believe it.
        """
        rows = sar_store.decorate([dict(DETECTION)], SCENE)
        del rows[0]["snr"]
        with self.assertRaises(KeyError) as caught:
            sar_store._table(rows, sar_store.DETECTION_SCHEMA)
        self.assertIn("snr", str(caught.exception))

    def test_a_scene_with_nothing_in_it_still_makes_a_table(self):
        # "No vessels" and "never looked" have to stay different, so an empty
        # detection table is a normal outcome and must not raise.
        table = sar_store._table([], sar_store.DETECTION_SCHEMA)
        self.assertEqual(table.num_rows, 0)
        self.assertEqual(table.schema, sar_store.DETECTION_SCHEMA)

    def test_a_row_survives_parquet_unchanged(self):
        import tempfile

        import pyarrow.parquet as pq

        rows = sar_store.decorate([dict(DETECTION)], SCENE)
        table = sar_store._table(rows, sar_store.DETECTION_SCHEMA)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "det.parquet")
            pq.write_table(table, path, compression="zstd")
            back = pq.read_table(path)
        self.assertEqual(back.schema, sar_store.DETECTION_SCHEMA)
        got = back.to_pylist()[0]
        for name in ("scene_id", "grid_row", "area_px", "is_vessel", "reject_reason"):
            self.assertEqual(got[name], rows[0][name], name)
        self.assertAlmostEqual(got["length_m"], rows[0]["length_m"], places=3)

    def test_the_schema_and_the_column_list_are_one_statement(self):
        self.assertEqual([f.name for f in sar_store.DETECTION_SCHEMA], DETECTION_COLUMNS)
        self.assertEqual([f.name for f in sar_store.SCENE_SCHEMA], SCENE_COLUMNS)
        self.assertIsInstance(sar_store.DETECTION_SCHEMA, pa.Schema)


if __name__ == "__main__":
    unittest.main()
