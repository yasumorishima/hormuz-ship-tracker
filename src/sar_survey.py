"""What the radar detections are worth, measured over every scene on the Hub.

    python src/sar_survey.py              # distance bands and recurrence
    python src/sar_survey.py --vh         # also re-read each scene's VH band

Three measurements, none of which needs AIS, because the feed has no
receivers in the strait:

1. **Distance bands.** `vessel_sized` detections per 100 km^2 of scored water,
   split by `dist_to_land_km`, with the SNR spread in each band. The area is
   the scene footprint (from the STAC item) intersected with the water mask,
   so the rate is per water actually looked at, not per AOI.

2. **Recurrence.** A hull under way is somewhere else two days later; a
   platform, a rock or the layover of a ridge is not. For every detection,
   each *other* pass whose footprint covers it is asked whether it has a
   detection within `radius_km`. The same question asked 500 m away, in the
   same distance band, is the chance level: near the shore detections are so
   dense that some coincidence is guaranteed, and the control says how much.

3. **Cross-polarisation** (`--vh`). The detector runs on VV. A real scatterer
   — hull, platform, rock — usually stands out in VH as well; speckle and sea
   clutter mostly do not, and VH noise is independent of VV noise. So the
   fraction of detections with VH `> bg + k*sd` within two pixels, minus the
   same fraction at control points, bounds from below how many are real
   scatterers: f = p*q + (1-p)*c <= p + (1-p)*c, so p >= (f - c) / (1 - c)
   whatever the VH detectability q is. It does not say a scatterer is a ship.

None of this is precision against ships. It separates "fixed" from "not
fixed" and "real scatterer" from "noise"; a transient real scatterer in open
water is the best candidate for a vessel there is without AIS.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

logger = logging.getLogger("sar_survey")

# Edges in km. The first edge is the detector's coast buffer: nothing nearer
# than 200 m is scored, so there is no water to divide by below it.
BAND_EDGES = (0.2, 1.0, 3.0, 10.0, float("inf"))
BAND_LABELS = ("0.2-1", "1-3", "3-10", "10+")

# Degrees to kilometres at the middle of the AOI. Good to well under 1% across
# it, which is far below the radii used here.
MID_LAT = 26.4
KM_PER_DEG_LAT = 110.95
KM_PER_DEG_LON = 111.32 * float(np.cos(np.deg2rad(MID_LAT)))

CONTROL_OFFSET_KM = 0.5
VH_WINDOW_PX = 5          # +-2 px, about +-40 m: the centroid of a VV blob
                          # and the brightest VH pixel need not coincide


def band_of(dist_km) -> np.ndarray:
    """Index into BAND_LABELS for each distance, -1 where it is in none.

    A centroid can sit a few metres inside the buffer even though every pixel
    of its blob is outside it, so -1 does happen, a handful of times in 20,000.
    """
    d = np.asarray(dist_km, dtype=float)
    idx = np.searchsorted(BAND_EDGES, d, side="left") - 1
    idx[(d <= BAND_EDGES[0]) | ~np.isfinite(d)] = -1
    return idx


def pass_of(scene_id: str) -> str:
    """Platform and date: the slices of one pass are one look, not several.

    Adjacent slices of the same datatake overlap by a few kilometres, and a
    detection seen in both is one observation. Recurrence must only count
    other passes.
    """
    return scene_id[:3] + scene_id[17:25]


def geometry_of(acq_time: str) -> str:
    """The acquisition slot, as HH:M. Passes in the same slot share the
    relative orbit and so the incidence angle: terrain layover lands in the
    same place in each of them, and nowhere near it in the other slots."""
    return acq_time[11:15]


def xy_km(lon, lat) -> np.ndarray:
    return np.c_[np.asarray(lon) * KM_PER_DEG_LON, np.asarray(lat) * KM_PER_DEG_LAT]


def band_rates(dist_km, vessel_sized, snr, band_area_km2) -> list[dict]:
    """Per band: vessel-sized count, rate per 100 km^2, SNR quartiles."""
    dist_km = np.asarray(dist_km, dtype=float)
    vs = np.asarray(vessel_sized, dtype=bool)
    snr = np.asarray(snr, dtype=float)
    idx = band_of(dist_km)
    total_vs = int(vs.sum())
    out = []
    for i, label in enumerate(BAND_LABELS):
        here = idx == i
        v = here & vs
        s = snr[v]
        area = float(band_area_km2[i])
        out.append({
            "band_km": label,
            "water_km2": round(area),
            "candidates": int(here.sum()),
            "vessel_sized": int(v.sum()),
            "per_100km2": round(v.sum() / area * 100, 2) if area else None,
            "share_pct": round(v.sum() / total_vs * 100, 1) if total_vs else None,
            "snr_p10": round(float(np.quantile(s, 0.1)), 1) if s.size else None,
            "snr_median": round(float(np.median(s)), 1) if s.size else None,
            "snr_p90": round(float(np.quantile(s, 0.9)), 1) if s.size else None,
            "snr_gt20_pct": round(float((s > 20).mean() * 100), 1) if s.size else None,
        })
    return out


def recurrence(lon, lat, passes, covers, radius_km: float,
               query=None) -> tuple[np.ndarray, np.ndarray]:
    """For each detection: how many other passes covered it, and how many of
    those had a detection within `radius_km`.

    `passes` is the pass of each detection. `covers(pass_id, lon, lat)` says
    which points fall inside that pass's footprint. A pass that covered a
    point but found nothing there counts; one that never looked does not.

    `query`, a (lon, lat) pair of arrays as long as the detections, asks the
    question somewhere else on each detection's behalf — the chance control —
    still excluding the detection's own pass.
    """
    from scipy.spatial import cKDTree

    passes = np.asarray(passes)
    pts = xy_km(lon, lat)
    qlon, qlat = query if query is not None else (lon, lat)
    qpts = xy_km(qlon, qlat)
    covered = np.zeros(len(pts), dtype=int)
    hits = np.zeros(len(pts), dtype=int)
    for p in np.unique(passes):
        mine = passes == p
        tree = cKDTree(pts[mine])
        other = ~mine & covers(p, qlon, qlat)
        nearest, _ = tree.query(qpts, k=1)
        covered += other
        hits += other & (nearest <= radius_km)
    return covered, hits


def offset_points(lon, lat, km: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Each point moved `km` in a random direction, reproducibly."""
    ang = np.random.default_rng(seed).uniform(0, 2 * np.pi, len(lon))
    return (np.asarray(lon) + km * np.cos(ang) / KM_PER_DEG_LON,
            np.asarray(lat) + km * np.sin(ang) / KM_PER_DEG_LAT)


def recurrence_table(band_idx, covered, hits, control_band_idx, control_covered,
                     control_hits, min_covered: int = 2) -> list[dict]:
    """Share of detections seen again by another pass, against chance.

    Only detections at least `min_covered` other passes looked at are scored,
    so one unlucky look does not decide it. The control rows are the same
    detections' offset points, kept only where the offset point is in the same
    band — the local density is what sets the chance of a coincidence.
    """
    out = []
    for i, label in enumerate(BAND_LABELS):
        x = (band_idx == i) & (covered >= min_covered)
        y = (control_band_idx == i) & (control_covered >= min_covered)
        out.append({
            "band_km": label,
            "n": int(x.sum()),
            "seen_again_pct": _pct(hits[x] > 0),
            "control_n": int(y.sum()),
            "control_seen_again_pct": _pct(control_hits[y] > 0),
        })
    return out


def vh_scores(vh: np.ndarray, water: np.ndarray, rows, cols) -> np.ndarray:
    """VH standing of each point: (max within +-2 px - background) / spread.

    The background and spread are the detector's own, computed on VH over the
    same water, so the scale is the one the VV threshold is expressed in.
    """
    from scipy import ndimage

    import sar_detect

    img = vh.astype(np.float32)
    bg, mad = sar_detect.background(img, water)
    sd = sar_detect.spread(mad)
    peak = ndimage.maximum_filter(np.where(water, img, 0), size=VH_WINDOW_PX)
    rows = np.asarray(rows)
    cols = np.asarray(cols)
    return (peak[rows, cols] - bg[rows, cols]) / sd[rows, cols]


def real_lower_bound(confirmed_frac: float, control_frac: float) -> float | None:
    """p >= (f - c) / (1 - c); see the module docstring."""
    if control_frac is None or confirmed_frac is None or control_frac >= 1:
        return None
    return max(0.0, (confirmed_frac - control_frac) / (1 - control_frac))


def _pct(mask) -> float | None:
    mask = np.asarray(mask)
    return round(float(mask.mean() * 100), 1) if mask.size else None


# --------------------------------------------------------------------- I/O --

def _download(dest: Path) -> list[str]:
    from huggingface_hub import HfApi, hf_hub_download

    import sar_store

    files = [f for f in HfApi().list_repo_files(sar_store.REPO_ID, repo_type="dataset")
             if f.startswith(("sar/det/", "sar/scenes/")) and f.endswith(".parquet")]
    for f in files:
        hf_hub_download(sar_store.REPO_ID, f, repo_type="dataset", local_dir=dest)
    return files


def _footprints(scene_ids: list[str]) -> dict:
    import sar_scene

    feats = sar_scene._get_json(sar_scene.STAC_SEARCH, {
        "collections": [sar_scene.COLLECTION], "ids": scene_ids,
        "limit": max(len(scene_ids), 1)}).get("features", [])
    return {f["id"]: f for f in feats}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="sar-survey-data",
                    help="where to keep the downloaded tables")
    ap.add_argument("--version", default=None, help="detector version (default: current)")
    ap.add_argument("--radius-km", type=float, default=0.1)
    ap.add_argument("--vh", action="store_true",
                    help="re-read VV and VH of every scene (about a minute each)")
    ap.add_argument("--vh-k", type=float, default=5.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    import pandas as pd
    from rasterio.features import rasterize
    from scipy import ndimage
    from shapely import prepared
    from shapely.geometry import Point, shape
    from shapely.ops import unary_union

    import sar_scene
    from sar_columns import DETECTOR_VERSION

    version = args.version or DETECTOR_VERSION
    dest = Path(args.data)
    _download(dest)
    scenes = pd.concat([pd.read_parquet(f) for f in
                        sorted((dest / "sar" / "scenes" / version).glob("*.parquet"))])
    det = pd.concat([pd.read_parquet(f) for f in
                     sorted((dest / "sar" / "det" / version).glob("*.parquet"))])
    scenes = scenes[scenes.status == "ok"]
    feats = _footprints(list(scenes.scene_id))
    missing = set(scenes.scene_id) - set(feats)
    if missing:
        raise SystemExit(f"no footprint for {sorted(missing)}")

    # Scored water per band, per scene footprint, on a grid five times coarser
    # than the detector's: 100 m cells are plenty for an area.
    land = sar_scene.load_land_mask()
    pixel_m = sar_scene.pixel_metres()
    dist_km = ndimage.distance_transform_edt(~land, sampling=pixel_m) / 1000.0
    transform = sar_scene.aoi_grid()[0]
    step = 5
    coarse = dist_km[step // 2::step, step // 2::step]
    coarse_tf = transform * transform.scale(step)
    cell_km2 = pixel_m[0] * pixel_m[1] * step * step / 1e6
    coarse_band = band_of(coarse.ravel()).reshape(coarse.shape)
    band_area = np.zeros(len(BAND_LABELS))
    for sid in scenes.scene_id:
        inside = rasterize([(feats[sid]["geometry"], 1)], out_shape=coarse.shape,
                           transform=coarse_tf, dtype="uint8").astype(bool)
        for i in range(len(BAND_LABELS)):
            band_area[i] += (inside & (coarse_band == i)).sum() * cell_km2

    report = {"version": version, "scenes": len(scenes),
              "passes": int(det.scene_id.map(pass_of).nunique()),
              "bands": band_rates(det.dist_to_land_km, det.vessel_sized, det.snr, band_area)}

    # Recurrence, vessel-sized only.
    vs = det[det.vessel_sized].reset_index(drop=True)
    by_pass = {}
    for sid, f in feats.items():
        by_pass.setdefault(pass_of(sid), []).append(shape(f["geometry"]))
    prepped = {p: prepared.prep(unary_union(g)) for p, g in by_pass.items()}

    def covers(p, lon, lat):
        return np.array([prepped[p].contains(Point(x, y)) for x, y in zip(lon, lat)])

    vs_pass = vs.scene_id.map(pass_of).to_numpy()
    cov, hit = recurrence(vs.longitude, vs.latitude, vs_pass, covers, args.radius_km)
    clon, clat = offset_points(vs.longitude, vs.latitude, CONTROL_OFFSET_KM)
    ccov, chit = recurrence(vs.longitude, vs.latitude, vs_pass, covers, args.radius_km,
                            query=(clon, clat))
    col, row = ~transform * (clon, clat)
    col = np.clip(col.astype(int), 0, dist_km.shape[1] - 1)
    row = np.clip(row.astype(int), 0, dist_km.shape[0] - 1)
    cband = band_of(dist_km[row, col])     # on land the distance is 0: band -1
    report["recurrence"] = {"radius_km": args.radius_km,
                            "rows": recurrence_table(band_of(vs.dist_to_land_km), cov, hit,
                                                     cband, ccov, chit)}

    if args.vh:
        report["vh"] = _vh_report(vs, feats, land, dist_km, args.vh_k)

    print(json.dumps(report, indent=2))
    return 0


def _vh_report(vs, feats, land, dist_km, k: float) -> dict:
    """Re-read each scene's VV and VH, check the stored detections are what
    the shipped detector gives on this VV, then score them in VH."""
    import pandas as pd

    import sar_detect
    import sar_scene

    transform = sar_scene.aoi_grid()[0]
    pixel_m = sar_scene.pixel_metres()
    scored = []
    not_reproduced = []
    for sid, group in vs.groupby("scene_id"):
        assets = feats[sid]["assets"]
        vv, _ = sar_scene.read_scene(assets["vv"]["href"])
        vh, _ = sar_scene.read_scene(assets["vh"]["href"])
        _, stats = sar_detect.detect(vv, land, transform, pixel_m)
        if stats["n_vessel_sized"] != len(group):
            not_reproduced.append(sid)
        water = (vv > 0) & (vh > 0) & (dist_km > sar_detect.COAST_BUFFER_M / 1000)
        z = vh_scores(vh, water, group.grid_row, group.grid_col)
        lon, lat = offset_points(group.longitude, group.latitude, CONTROL_OFFSET_KM, seed=1)
        col, row = ~transform * (lon, lat)
        col = np.clip(col.astype(int), 0, vh.shape[1] - 1)
        row = np.clip(row.astype(int), 0, vh.shape[0] - 1)
        cz = np.where(water[row, col], vh_scores(vh, water, row, col), np.nan)
        scored.append(pd.DataFrame({
            "band": band_of(group.dist_to_land_km), "snr": group.snr.to_numpy(),
            "z": z, "cband": np.where(water[row, col], band_of(dist_km[row, col]), -1),
            "cz": cz}))
        del vv, vh, water
    s = pd.concat(scored, ignore_index=True)
    rows = []
    strata = [("all", lambda x: np.ones(len(x), bool)),
              ("snr<=20", lambda x: x.snr <= 20), ("snr>20", lambda x: x.snr > 20)]
    for i, label in enumerate(BAND_LABELS):
        ctl = s[(s.cband == i)]
        c = float((ctl.cz > k).mean()) if len(ctl) else None
        for name, pick in strata:
            x = s[(s.band == i) & pick(s)]
            f = float((x.z > k).mean()) if len(x) else None
            lb = real_lower_bound(f, c) if f is not None else None
            rows.append({"band_km": label, "stratum": name, "n": len(x),
                         "vh_confirmed_pct": None if f is None else round(f * 100, 1),
                         "control_pct": None if c is None else round(c * 100, 1),
                         "real_scatterer_lower_bound_pct":
                             None if lb is None else round(lb * 100, 1)})
    return {"k": k, "not_reproduced": not_reproduced, "rows": rows}


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
