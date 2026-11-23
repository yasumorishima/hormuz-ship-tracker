"""Places strong radar detections keep coming back to, for the map's flag.

    python src/sar_places.py --dry-run     # build from the Hub, print, write nothing
    python src/sar_places.py               # build and upload (needs HF_TOKEN)

The rule is docs/sar_recur_flag.md. In short:
- the detections that count are the map's default layer
  (`vessel_sized`, SNR > MAP_MIN_SNR, `dist_to_land_km` >= MAP_MIN_SHORE_KM,
  scenes with status `ok`)
- they are single-linked at LINK_KM into places
- a place seen in at least MIN_PASSES passes from at least MIN_SLOTS
  acquisition slots is flagged

What the flag means is "a structure or an anchorage", never "not a ship": the
optical check (docs/sar_recur_s2.md) found anchorages among the most recurring
places.

One file, `sar/places/v1/places.parquet`, rebuilt whole each time, so the map
reads one small table instead of every pass ever made.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sar_columns import DETECTOR_VERSION, MAP_MIN_SHORE_KM, MAP_MIN_SNR  # noqa: E402

logger = logging.getLogger("sar_places")

LINK_KM = 0.1
MIN_PASSES = 10
MIN_SLOTS = 2
PLACES_PATH = f"sar/places/{DETECTOR_VERSION}/places.parquet"
COLUMNS = ["longitude", "latitude", "n_passes", "n_slots", "slots",
           "n_detections", "snr_median", "first_seen", "last_seen", "recurring"]

# Same ground scale as sar_survey, so 100 m means the same thing in both.
MID_LAT = 26.4
KM_PER_DEG_LAT = 110.95
KM_PER_DEG_LON = 111.32 * float(np.cos(np.deg2rad(MID_LAT)))


def pass_of(scene_id: str) -> str:
    return scene_id[:3] + scene_id[17:25]


def slot_of(acq_time: str) -> str:
    return str(acq_time)[11:15]


def xy_km(lon, lat) -> np.ndarray:
    return np.c_[np.asarray(lon, float) * KM_PER_DEG_LON,
                 np.asarray(lat, float) * KM_PER_DEG_LAT]


def link(xy: np.ndarray, radius_km: float) -> np.ndarray:
    """Single-linkage component label of each point."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    n = len(xy)
    if n == 0:
        return np.zeros(0, dtype=int)
    pairs = cKDTree(xy).query_pairs(radius_km, output_type="ndarray")
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    return connected_components(g, directed=False)[1]


def strong(det, scenes):
    """The detections that count: the map's default layer, in `ok` scenes."""
    ok = scenes[scenes.status == "ok"]
    acq = dict(zip(ok.scene_id, ok.acq_time.astype(str)))
    d = det[det.scene_id.isin(acq) & det.vessel_sized.astype(bool)
            & (det.snr > MAP_MIN_SNR) & (det.dist_to_land_km >= MAP_MIN_SHORE_KM)].copy()
    d["pass"] = d.scene_id.map(pass_of)
    d["slot"] = d.scene_id.map(lambda s: slot_of(acq[s]))
    d["acq"] = d.scene_id.map(acq)
    return d.reset_index(drop=True)


def build(det, scenes, lon=None, lat=None):
    """One row per place. `lon`/`lat` replace the detections' positions (the
    chance check moves them); everything else is the detections' own."""
    import pandas as pd
    d = strong(det, scenes)
    if lon is not None:
        d["longitude"], d["latitude"] = lon(d), lat(d)
    if d.empty:
        return pd.DataFrame(columns=COLUMNS)
    d["place"] = link(xy_km(d.longitude, d.latitude), LINK_KM)
    g = d.groupby("place")
    out = pd.DataFrame({
        "longitude": g.longitude.median(),
        "latitude": g.latitude.median(),
        "n_passes": g["pass"].nunique(),
        "n_slots": g.slot.nunique(),
        "slots": g.slot.agg(lambda s: ",".join(sorted(set(s)))),
        "n_detections": g.size(),
        "snr_median": g.snr.median().round(1),
        "first_seen": g.acq.min(),
        "last_seen": g.acq.max(),
    })
    out["recurring"] = (out.n_passes >= MIN_PASSES) & (out.n_slots >= MIN_SLOTS)
    # Only places seen more than once are worth a row; a single detection is
    # not a place anyone needs to look up.
    out = out[out.n_passes >= 2].sort_values("n_passes", ascending=False)
    return out[COLUMNS].reset_index(drop=True)


def chance(det, scenes, km: float = 0.5, seed: int = 20261123):
    """The same rule with every detection moved `km` in a random direction."""
    def moved(d):
        ang = np.random.default_rng(seed).uniform(0, 2 * np.pi, len(d))
        return (d.longitude + km * np.cos(ang) / KM_PER_DEG_LON,
                d.latitude + km * np.sin(ang) / KM_PER_DEG_LAT)
    return build(det, scenes, lon=lambda d: moved(d)[0], lat=lambda d: moved(d)[1])


def read_hub(dest: Path):
    """Every detection and scene row on the Hub, by plain download."""
    import pandas as pd
    from huggingface_hub import HfApi, hf_hub_download

    import sar_store
    files = [f for f in HfApi().list_repo_files(sar_store.REPO_ID, repo_type="dataset")
             if f.startswith((f"sar/det/{DETECTOR_VERSION}/", f"sar/scenes/{DETECTOR_VERSION}/"))
             and f.endswith(".parquet")]
    for f in files:
        hf_hub_download(sar_store.REPO_ID, f, repo_type="dataset", local_dir=dest)
    det = pd.concat([pd.read_parquet(p) for p in
                     sorted((dest / "sar" / "det" / DETECTOR_VERSION).glob("*.parquet"))])
    scenes = pd.concat([pd.read_parquet(p) for p in
                        sorted((dest / "sar" / "scenes" / DETECTOR_VERSION).glob("*.parquet"))])
    return det, scenes


def upload(places, token=None) -> None:
    from huggingface_hub import CommitOperationAdd, HfApi

    import sar_store
    fd, local = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        places.to_parquet(local, index=False, compression="zstd")
        HfApi(token=token).create_commit(
            repo_id=sar_store.REPO_ID, repo_type="dataset",
            operations=[CommitOperationAdd(path_in_repo=PLACES_PATH, path_or_fileobj=local)],
            commit_message=(f"sar places {DETECTOR_VERSION}: {int(places.recurring.sum())} "
                            f"recurring of {len(places)} places"))
    finally:
        os.remove(local)


def refresh(token=None, dry_run=False, data_dir=None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        det, scenes = read_hub(Path(data_dir or tmp))
        places = build(det, scenes)
        if not dry_run:
            upload(places, token=token)
    summary = {"places": len(places), "recurring": int(places.recurring.sum()),
               "passes": int(scenes[scenes.status == "ok"].scene_id.map(pass_of).nunique())}
    logger.info("places: %s", summary)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data", default=None, help="keep the downloaded tables here")
    ap.add_argument("--chance", action="store_true", help="also run the 500 m chance check")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if args.chance:
        det, scenes = read_hub(Path(args.data or tempfile.mkdtemp()))
        real, ctrl = build(det, scenes), chance(det, scenes)
        print(json.dumps({"recurring": int(real.recurring.sum()),
                          "chance_recurring": int(ctrl.recurring.sum()),
                          "chance_max_passes": int(ctrl.n_passes.max()) if len(ctrl) else 0,
                          "places": len(real)}, indent=1))
        return 0
    print(json.dumps(refresh(os.environ.get("HF_TOKEN"), args.dry_run, args.data), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
