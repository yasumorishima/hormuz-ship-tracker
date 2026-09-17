"""The SAR half of the Hub dataset.

Layout, beside the AIS files that were already there:

  sar/det/<detector>/<scene_id>.parquet      one row per candidate
  sar/scenes/<detector>/<scene_id>.parquet   one row for the scene itself

There is no ledger of what has been processed, because the output is the
ledger: a scene has been done when its file exists. A separate list of scene
ids could disagree with the files; this cannot. The detector version sits in
the path so that re-running a fixed detector adds a set of results beside the
old one instead of overwriting it, and the two can be compared.

The scene table is not optional. Without it "no vessels were found" and "this
scene was never looked at" are the same absence, which is how the AIS side
ended up publishing a transit count that was structurally zero.
"""

import logging
import os
import tempfile
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import CommitOperationAdd, HfApi

from sar_columns import (DETECTION_COLUMNS, DETECTOR_VERSION, SCENE_COLUMNS)

logger = logging.getLogger(__name__)

# The same dataset the AIS side writes to; src/hf_store.py has its own copy of
# this default and the two have to stay equal. They are kept separate so that a
# job which only merges parquet does not have to import the raster stack.
REPO_ID = os.environ.get("HF_DATASET_REPO", "yasumorishima/hormuz-ais")
REPO_TYPE = "dataset"

DETECTION_SCHEMA = pa.schema([
    ("scene_id", pa.string()), ("acq_time", pa.string()), ("platform", pa.string()),
    ("orbit_state", pa.string()), ("polarization", pa.string()),
    ("latitude", pa.float64()), ("longitude", pa.float64()),
    ("grid_row", pa.int32()), ("grid_col", pa.int32()),
    ("dist_to_land_km", pa.float32()),
    ("area_px", pa.int32()), ("length_m", pa.float32()), ("width_m", pa.float32()),
    ("orientation_deg", pa.float32()),
    ("peak_dn", pa.float32()), ("mean_dn", pa.float32()),
    ("bg_median_dn", pa.float32()), ("bg_mad_dn", pa.float32()), ("snr", pa.float32()),
    ("is_vessel", pa.bool_()), ("reject_reason", pa.string()),
    ("detector_version", pa.string()), ("aoi_version", pa.string()),
])

SCENE_SCHEMA = pa.schema([
    ("scene_id", pa.string()), ("acq_time", pa.string()), ("platform", pa.string()),
    ("orbit_state", pa.string()), ("polarization", pa.string()),
    ("aoi_covered_frac", pa.float32()), ("scored_water_km2", pa.float32()),
    ("sea_median_dn", pa.float32()),
    ("n_candidates", pa.int32()), ("n_vessels", pa.int32()),
    ("detector_version", pa.string()), ("aoi_version", pa.string()),
    ("mask_source", pa.string()), ("processed_at", pa.string()),
    ("runtime_s", pa.float32()), ("status", pa.string()),
])

# The column list and the schema are two statements of the same thing, so they
# are checked against each other rather than kept in step by hand.
assert [f.name for f in DETECTION_SCHEMA] == DETECTION_COLUMNS
assert [f.name for f in SCENE_SCHEMA] == SCENE_COLUMNS


def det_path(scene_id: str, version: str = DETECTOR_VERSION) -> str:
    return f"sar/det/{version}/{scene_id}.parquet"


def scene_path(scene_id: str, version: str = DETECTOR_VERSION) -> str:
    return f"sar/scenes/{version}/{scene_id}.parquet"


def _api(token: str | None = None) -> HfApi:
    return HfApi(token=token or os.environ.get("HF_TOKEN"))


def processed(files: list[str], version: str = DETECTOR_VERSION) -> set[str]:
    """Scene ids this detector version has already written a scene row for.

    Keyed on the scene table, not the detection table: a scene with no
    candidates still gets a scene row, and keying on detections would make it
    look unprocessed forever.
    """
    prefix = f"sar/scenes/{version}/"
    return {f[len(prefix):-len(".parquet")] for f in files
            if f.startswith(prefix) and f.endswith(".parquet")}


def list_files(token: str | None = None) -> list[str]:
    return _api(token).list_repo_files(repo_id=REPO_ID, repo_type=REPO_TYPE)


def _table(rows: list[dict], schema: pa.Schema) -> pa.Table:
    """Column-orient the rows, refusing to invent any of them.

    `row.get(name)` would turn a field the detector stopped producing into a
    column of nulls that reads as "measured, and empty". A KeyError here is
    the difference between a bug and a dataset.
    """
    names = [f.name for f in schema]
    missing = {name for row in rows for name in names if name not in row}
    if missing:
        raise KeyError(f"rows are missing {sorted(missing)}")
    return pa.table({name: [row[name] for row in rows] for name in names},
                    schema=schema)


def scene_row(scene: dict, stats: dict, covered: float, runtime_s: float,
              status: str = "ok", mask_source: str = "") -> dict:
    from sar_columns import AOI_VERSION
    return {
        "scene_id": scene["scene_id"], "acq_time": scene["acq_time"],
        "platform": scene["platform"], "orbit_state": scene["orbit_state"],
        "polarization": scene["polarization"],
        "aoi_covered_frac": covered,
        "scored_water_km2": stats.get("scored_water_km2", 0.0),
        "sea_median_dn": stats.get("sea_median_dn", float("nan")),
        "n_candidates": stats.get("n_candidates", 0),
        "n_vessels": stats.get("n_vessels", 0),
        "detector_version": DETECTOR_VERSION, "aoi_version": AOI_VERSION,
        "mask_source": mask_source,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runtime_s": runtime_s, "status": status,
    }


def decorate(rows: list[dict], scene: dict) -> list[dict]:
    """Stamp each detection with the scene it came from."""
    from sar_columns import AOI_VERSION
    out = []
    for row in rows:
        row = dict(row)
        row.update({
            "scene_id": scene["scene_id"], "acq_time": scene["acq_time"],
            "platform": scene["platform"], "orbit_state": scene["orbit_state"],
            "polarization": scene["polarization"],
            "detector_version": DETECTOR_VERSION, "aoi_version": AOI_VERSION,
        })
        out.append(row)
    return out


def upload(scene_id: str, detections: list[dict], scene: dict,
           token: str | None = None, version: str = DETECTOR_VERSION) -> list[str]:
    """Write both tables for one scene in a single commit.

    One commit, so the dataset never holds a scene row whose detections are
    missing — the state that would make "processed" mean two different things.
    """
    paths = [det_path(scene_id, version), scene_path(scene_id, version)]
    # The version in the path and the version in the rows are the same claim,
    # so they are made once here rather than trusted to agree.
    detections = [dict(row, detector_version=version) for row in detections]
    scene = dict(scene, detector_version=version)
    tables = [_table(detections, DETECTION_SCHEMA), _table([scene], SCENE_SCHEMA)]
    operations, temps = [], []
    try:
        for path, table in zip(paths, tables):
            fd, local = tempfile.mkstemp(suffix=".parquet")
            os.close(fd)
            temps.append(local)
            pq.write_table(table, local, compression="zstd")
            operations.append(CommitOperationAdd(path_in_repo=path, path_or_fileobj=local))
        _api(token).create_commit(
            repo_id=REPO_ID, repo_type=REPO_TYPE, operations=operations,
            commit_message=(f"sar {version} {scene_id}: "
                            f"{scene['n_vessels']} vessels of "
                            f"{scene['n_candidates']} candidates"))
    finally:
        for local in temps:
            os.remove(local)
    logger.info("uploaded %s", ", ".join(paths))
    return paths
