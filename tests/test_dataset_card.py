"""The dataset card is the only thing a stranger reads before using the data.

These check that it stays true rather than that it exists: the globs it
advertises have to match the paths the collector actually writes, and the
round trip from a parser row to parquet and back has to preserve every column
in order. A silent transposition there would corrupt the permanent record and
nothing downstream would notice.
"""

import fnmatch
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CARD = ROOT / "docs" / "DATASET_CARD.md"

try:
    import pyarrow.parquet as pq
    import yaml
    import hf_store
except ImportError as exc:  # pragma: no cover
    hf_store = None
    _why = str(exc)


def front_matter() -> dict:
    text = CARD.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "the Hub needs YAML front matter"
    return yaml.safe_load(text.split("---\n", 2)[1])


@unittest.skipIf(hf_store is None, "pyarrow/yaml not installed")
class CardTest(unittest.TestCase):
    def test_front_matter_declares_the_configs_the_viewer_needs(self):
        meta = front_matter()
        names = {c["config_name"] for c in meta["configs"]}
        self.assertIn("default", names)
        self.assertIn("license", meta)

    def test_the_advertised_globs_match_what_the_collector_writes(self):
        meta = front_matter()
        default = next(c for c in meta["configs"] if c["config_name"] == "default")
        globs = [entry["path"] for entry in default["data_files"]]

        written = [
            hf_store.shard_path(datetime(2026, 9, 16, 7, 22, 0, tzinfo=timezone.utc)),
            hf_store.daily_path(datetime(2026, 9, 16)),
            hf_store.LEGACY_FILE,
        ]
        for path in written:
            self.assertTrue(
                any(fnmatch.fnmatch(path, g) for g in globs),
                f"{path} is written but no split in the card matches it: {globs}",
            )

    def test_every_advertised_glob_can_match_something(self):
        meta = front_matter()
        default = next(c for c in meta["configs"] if c["config_name"] == "default")
        written = [
            hf_store.shard_path(datetime(2026, 9, 16, 7, 22, 0, tzinfo=timezone.utc)),
            hf_store.daily_path(datetime(2026, 9, 16)),
            hf_store.LEGACY_FILE,
        ]
        for entry in default["data_files"]:
            self.assertTrue(
                any(fnmatch.fnmatch(p, entry["path"]) for p in written),
                f"the card advertises {entry['path']}, which nothing writes",
            )

    def test_the_columns_the_card_documents_are_the_columns_stored(self):
        body = CARD.read_text(encoding="utf-8")
        for column in hf_store.COLUMNS:
            self.assertIn(f"`{column}`", body, f"{column} is stored but undocumented")


@unittest.skipIf(hf_store is None, "pyarrow not installed")
class ParquetRoundTripTest(unittest.TestCase):
    """The rows written here are the permanent record; nothing re-reads the
    stream to check them."""

    def row(self, mmsi=123456789):
        return (
            mmsi, "2026-09-16T06:57:51.594510", 26.5, 56.25,
            12.5, 270.0, 271, "TEST SHIP", 70, "JEBEL ALI",
            12.3, 150, 22, "PA", "2026-09-16T06:58:00+00:00",
        )

    def test_a_row_survives_parquet_unchanged_and_in_order(self):
        rows = [self.row(111111111), self.row(222222222)]
        table = hf_store.rows_to_table(rows)
        self.assertEqual(table.schema, hf_store.SCHEMA)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shard.parquet"
            pq.write_table(table, path, compression="zstd")
            back = pq.read_table(path, columns=hf_store.COLUMNS)

        got = list(zip(*[back.column(c).to_pylist() for c in hf_store.COLUMNS]))
        # Floats because the schema says float64; the values must be equal.
        for original, restored in zip(rows, got):
            self.assertEqual(len(original), len(restored))
            for name, a, b in zip(hf_store.COLUMNS, original, restored):
                self.assertEqual(a, b, f"column {name} changed in the round trip")

    def test_missing_static_fields_survive_as_null(self):
        row = list(self.row())
        for name in ("ship_name", "ship_type", "destination", "draught",
                     "length", "width"):
            row[hf_store.COLUMNS.index(name)] = None
        table = hf_store.rows_to_table([tuple(row)])
        self.assertIsNone(table.column("ship_type").to_pylist()[0])
        self.assertIsNone(table.column("ship_name").to_pylist()[0])

    def test_the_schema_still_matches_the_archive_it_appends_to(self):
        """Measured from positions.parquet on the Hub: ship_type is float64
        because it is nullable, and changing it would split the dataset."""
        expected = [
            ("mmsi", "int64"), ("timestamp", "string"), ("latitude", "double"),
            ("longitude", "double"), ("speed", "double"), ("course", "double"),
            ("heading", "double"), ("ship_name", "string"), ("ship_type", "double"),
            ("destination", "string"), ("draught", "double"), ("length", "double"),
            ("width", "double"), ("flag", "string"), ("received_at", "string"),
        ]
        got = [(f.name, str(f.type)) for f in hf_store.SCHEMA]
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
