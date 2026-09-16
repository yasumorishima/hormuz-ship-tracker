"""Storage on the Hugging Face Hub.

A scheduled CI run has no disk that outlives it, so the Hub dataset is the
record of truth and SQLite becomes a throwaway working copy rebuilt per run.

Layout:
  raw/<YYYY-MM-DD>/<HHMMSS>.parquet   one immutable shard per collection window
  daily/<YYYY-MM-DD>.parquet          a finished day, merged from its shards
  positions.parquet                   the 2026-03..05 archive from the Pi era
"""

import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

from ais_parse import COLUMNS
from enrich import backfill_static

logger = logging.getLogger(__name__)

REPO_ID = os.environ.get("HF_DATASET_REPO", "yasumorishima/hormuz-ais")
REPO_TYPE = "dataset"
LEGACY_FILE = "positions.parquet"

# Matches the legacy positions.parquet exactly, so every file in the dataset
# can be read as one table. ship_type / heading are float64 there because they
# are nullable, and changing that now would split the schema.
SCHEMA = pa.schema([
    ("mmsi", pa.int64()),
    ("timestamp", pa.string()),
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
    ("speed", pa.float64()),
    ("course", pa.float64()),
    ("heading", pa.float64()),
    ("ship_name", pa.string()),
    ("ship_type", pa.float64()),
    ("destination", pa.string()),
    ("draught", pa.float64()),
    ("length", pa.float64()),
    ("width", pa.float64()),
    ("flag", pa.string()),
    ("received_at", pa.string()),
])

# The schema here and the row tuple the parser emits must not drift apart.
assert [f.name for f in SCHEMA] == COLUMNS, "SCHEMA does not match ais_parse.COLUMNS"


def _api(token: str | None = None) -> HfApi:
    return HfApi(token=token or os.environ.get("HF_TOKEN"))


def shard_path(when: datetime) -> str:
    return f"raw/{when:%Y-%m-%d}/{when:%H%M%S}.parquet"


def daily_path(day) -> str:
    return f"daily/{day:%Y-%m-%d}.parquet"


def rows_to_table(rows: list[tuple]) -> pa.Table:
    """Column-orient the row tuples the parser produces."""
    cols = {name: [] for name in COLUMNS}
    for row in rows:
        for name, value in zip(COLUMNS, row):
            cols[name].append(value)
    return pa.table(cols, schema=SCHEMA)


def upload_rows(rows: list[tuple], when: datetime, token: str | None = None) -> str:
    """Write one window's rows as an immutable shard. Returns the path in repo."""
    path = shard_path(when)
    table = rows_to_table(rows)
    fd, local = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        pq.write_table(table, local, compression="zstd")
        _api(token).upload_file(
            path_or_fileobj=local,
            path_in_repo=path,
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
            commit_message=f"window {when:%Y-%m-%d %H:%M}Z: {len(rows)} rows",
        )
    finally:
        os.remove(local)
    logger.info("uploaded %s (%d rows)", path, len(rows))
    return path


def list_data_files(token: str | None = None) -> list[str]:
    files = _api(token).list_repo_files(repo_id=REPO_ID, repo_type=REPO_TYPE)
    return sorted(f for f in files if f.endswith(".parquet"))


def files_since(hours: int, files: list[str] | None = None,
                now: datetime | None = None) -> list[str]:
    """Shards and daily files whose day could hold data from the last `hours`.

    Selection is by day, not by exact timestamp: a file is cheap to read and a
    missed one is not, so the window is rounded outwards.
    """
    now = now or datetime.now(timezone.utc)
    files = files if files is not None else list_data_files()
    days = set()
    day = (now - timedelta(hours=hours)).date()
    while day <= now.date():
        days.add(day.isoformat())
        day += timedelta(days=1)
    keep = []
    for f in files:
        if f.startswith("raw/") and f.split("/")[1] in days:
            keep.append(f)
        elif f.startswith("daily/") and f[len("daily/"):-len(".parquet")] in days:
            keep.append(f)
    return sorted(keep)


def build_sqlite(paths: list[str], db_path: str, token: str | None = None) -> int:
    """Download the given files and rebuild a `positions` table from them.

    The analysis scripts all read SQLite, so this is what lets them run
    unchanged on a runner. Returns the number of rows written.
    """
    from huggingface_hub import hf_hub_download

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        DROP TABLE IF EXISTS positions;
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            speed REAL, course REAL, heading REAL,
            ship_name TEXT, ship_type INTEGER, destination TEXT,
            draught REAL, length REAL, width REAL, flag TEXT,
            received_at TEXT NOT NULL
        );
    """)
    insert = ("INSERT INTO positions (" + ", ".join(COLUMNS) + ") VALUES ("
              + ", ".join("?" * len(COLUMNS)) + ")")

    total = 0
    for path in paths:
        local = hf_hub_download(repo_id=REPO_ID, filename=path,
                                repo_type=REPO_TYPE, token=token or os.environ.get("HF_TOKEN"))
        table = pq.read_table(local, columns=COLUMNS)
        batch = list(zip(*[table.column(c).to_pylist() for c in COLUMNS]))
        conn.executemany(insert, batch)
        total += len(batch)
        logger.info("loaded %s (%d rows)", path, len(batch))

    conn.execute("CREATE INDEX idx_positions_timestamp ON positions(timestamp)")
    conn.execute("CREATE INDEX idx_positions_mmsi ON positions(mmsi)")
    conn.commit()
    # A single window rarely carries both a vessel's position and its static
    # description; this is what the Pi's months-long cache used to do.
    backfill_static(conn)
    conn.close()
    return total


def merge_day(day: str, token: str | None = None, dry_run: bool = False) -> dict:
    """Merge one day's shards into daily/<day>.parquet and drop the shards.

    The add and the deletes are a single commit, so shards are never removed
    without their replacement already being in the same tree.
    """
    from huggingface_hub import hf_hub_download

    files = list_data_files(token)
    shards = [f for f in files if f.startswith(f"raw/{day}/")]
    if not shards:
        return {"day": day, "shards": 0, "rows": 0, "merged": False}

    target = daily_path(datetime.strptime(day, "%Y-%m-%d"))
    tables = []
    if target in files:
        # Re-merging a day that was already merged: keep what is there.
        tables.append(pq.read_table(hf_hub_download(
            repo_id=REPO_ID, filename=target, repo_type=REPO_TYPE,
            token=token or os.environ.get("HF_TOKEN")), columns=COLUMNS))
    for shard in shards:
        tables.append(pq.read_table(hf_hub_download(
            repo_id=REPO_ID, filename=shard, repo_type=REPO_TYPE,
            token=token or os.environ.get("HF_TOKEN")), columns=COLUMNS))

    merged = pa.concat_tables(tables).cast(SCHEMA).sort_by([("timestamp", "ascending")])
    result = {"day": day, "shards": len(shards), "rows": merged.num_rows,
              "target": target, "merged": not dry_run}

    fd, local = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        pq.write_table(merged, local, compression="zstd")
        if dry_run:
            return result
        ops = [CommitOperationAdd(path_in_repo=target, path_or_fileobj=local)]
        ops += [CommitOperationDelete(path_in_repo=shard) for shard in shards]
        _api(token).create_commit(
            repo_id=REPO_ID, repo_type=REPO_TYPE, operations=ops,
            commit_message=f"compact {day}: {len(shards)} shards -> {merged.num_rows} rows",
        )
    finally:
        os.remove(local)
    logger.info("merged %s: %d shards -> %d rows", day, len(shards), merged.num_rows)
    return result
