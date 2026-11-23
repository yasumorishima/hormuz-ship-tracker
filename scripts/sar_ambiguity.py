"""Are the strong 3-10 km detections azimuth ghosts of something brighter?

    HF_HUB_DISABLE_XET=1 python scripts/sar_ambiguity.py [--out docs/sar_ambiguity.json]

The plan (what is sampled, the controls, the thresholds and what would count
as what) is docs/sar_ambiguity.md, committed before this was run. Needs the
test requirements plus pandas; nothing secret.
"""

import argparse
import json
import logging
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sar_scene  # noqa: E402
import sar_survey as sv  # noqa: E402
from sar_columns import DETECTOR_VERSION  # noqa: E402

PASSES_PER_SLOT = 3
D_KM = np.round(np.arange(2.0, 9.0001, 0.1), 1)
BOX_LONG_M, BOX_WIDE_M, STEP_M = 100.0, 300.0, 20.0
RATIO = 10.0
SENSITIVITY = (5.0, 20.0)
WINDOWS = {"along_4.5-6.5": ("along", 4.5, 6.5),
           "across_4.5-6.5": ("across", 4.5, 6.5),
           "along_2.0-4.0": ("along", 2.0, 4.0),
           "along_7.0-9.0": ("along", 7.0, 9.0)}
# Sun-synchronous, inclination 98.18 deg: the ground track at 26.4 N runs
# about 10 deg west of north going up and 10 deg west of south coming down.
# Only used to pick which side of the footprint rectangle is azimuth.
PRIOR_HEADING = {"ascending": -10.0, "descending": 190.0}


def cp(k, n):
    if n == 0:
        return [None, None]
    lo = 0.0 if k == 0 else float(beta.ppf(0.025, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(0.975, k + 1, n - k))
    return [round(lo, 3), round(hi, 3)]


def share(mask):
    k, n = int(np.sum(mask)), int(np.size(mask))
    return {"k": k, "n": n, "share": round(k / n, 3) if n else None, "ci95": cp(k, n)}


def azimuth_deg(geometry, orbit_state):
    """Azimuth direction of a slice, degrees clockwise from north, from the
    side of its footprint's minimum rotated rectangle nearest the orbit prior."""
    from shapely.geometry import shape
    g = shape(geometry)
    x, y = sv.xy_km(*np.asarray(g.exterior.coords).T[:2]).T
    from shapely.geometry import Polygon
    rect = Polygon(np.c_[x, y]).minimum_rotated_rectangle
    c = np.asarray(rect.exterior.coords)
    edges = [c[1] - c[0], c[2] - c[1]]
    prior = PRIOR_HEADING[orbit_state]
    best = None
    for e in edges:
        h = math.degrees(math.atan2(e[0], e[1]))       # from north, clockwise
        for cand in (h, h + 180):
            diff = abs((cand - prior + 180) % 360 - 180)
            if best is None or diff < best[0]:
                best = (diff, cand % 360)
    return best[1], best[0]


def box_offsets(pixel_m):
    """Pixel offsets (rows, cols) of a box centred on 0, long axis along
    `u`, for unit vectors u given as (east, north)."""
    s = np.arange(-BOX_LONG_M / 2, BOX_LONG_M / 2 + 1e-9, STEP_M)
    t = np.arange(-BOX_WIDE_M / 2, BOX_WIDE_M / 2 + 1e-9, STEP_M)
    ss, tt = np.meshgrid(s, t)
    return ss.ravel(), tt.ravel()


def profiles(image, rows, cols, az_deg, pixel_m):
    """Max amplitude per detection, direction, side and D: shape (n, 2, 2, len(D))."""
    row_m, col_m = pixel_m
    h, w = image.shape
    ss, tt = box_offsets(pixel_m)
    a = math.radians(az_deg)
    along = np.array([math.sin(a), math.cos(a)])        # east, north
    across = np.array([math.cos(a), -math.sin(a)])
    out = np.zeros((len(rows), 2, 2, len(D_KM)), dtype=np.float32)
    for di, (u, v) in enumerate(((along, across), (across, along))):
        for si, sign in enumerate((1, -1)):
            for k, d in enumerate(D_KM):
                east = sign * d * 1000 * u[0] + ss * u[0] + tt * v[0]
                north = sign * d * 1000 * u[1] + ss * u[1] + tt * v[1]
                rr = np.rint(rows[:, None] - north[None, :] / row_m).astype(int)
                cc = np.rint(cols[:, None] + east[None, :] / col_m).astype(int)
                ok = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
                vals = np.where(ok, image[np.clip(rr, 0, h - 1), np.clip(cc, 0, w - 1)], 0)
                out[:, di, si, k] = vals.max(axis=1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="sar-survey-data")
    ap.add_argument("--out", default=str(ROOT / "docs" / "sar_ambiguity.json"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

    import shapely
    from shapely.geometry import shape
    from shapely.ops import unary_union

    dest = Path(args.data)
    sv._download(dest)
    scenes = pd.concat([pd.read_parquet(f) for f in
                        sorted((dest / "sar" / "scenes" / DETECTOR_VERSION).glob("*.parquet"))])
    det = pd.concat([pd.read_parquet(f) for f in
                     sorted((dest / "sar" / "det" / DETECTOR_VERSION).glob("*.parquet"))])
    scenes = scenes[scenes.status == "ok"]
    feats = sv._footprints(list(scenes.scene_id))

    # Same-slot recurrence over all passes, exactly as sar_survey does it.
    vs = det[det.vessel_sized & det.scene_id.isin(scenes.scene_id)].reset_index(drop=True)
    by_pass = {}
    for sid, f in feats.items():
        by_pass.setdefault(sv.pass_of(sid), []).append(shape(f["geometry"]))
    unions = {p: unary_union(g) for p, g in by_pass.items()}
    for g in unions.values():
        shapely.prepare(g)

    def covers(p, lon, lat):
        return shapely.contains_xy(unions[p], np.asarray(lon), np.asarray(lat))

    acq = dict(zip(scenes.scene_id, scenes.acq_time.astype(str)))
    pass_slot = {}
    for sid in sorted(acq):
        pass_slot.setdefault(sv.pass_of(sid), sv.geometry_of(acq[sid]))
    vs_pass = vs.scene_id.map(sv.pass_of).to_numpy()
    vs_slot = np.array([pass_slot[p] for p in vs_pass])
    cov, hit = sv.recurrence(vs.longitude, vs.latitude, vs_pass,
                             sv.by_slot(covers, vs_slot, pass_slot, True), 0.1)
    vs["same_slot_covered"] = cov
    vs["same_slot_hits"] = hit

    # Twelve passes: the newest three in each slot, chosen by date only.
    pass_date = {}
    for sid in acq:
        p = sv.pass_of(sid)
        pass_date[p] = max(pass_date.get(p, ""), acq[sid])
    chosen = []
    for slot in sorted(set(pass_slot.values())):
        ps = sorted((p for p in pass_slot if pass_slot[p] == slot),
                    key=lambda p: pass_date[p], reverse=True)[:PASSES_PER_SLOT]
        chosen += ps
    chosen_scenes = sorted(s for s in acq if sv.pass_of(s) in chosen)
    logging.info("passes %s -> %d scenes", chosen, len(chosen_scenes))

    pixel_m = sar_scene.pixel_metres()
    rows_out = []
    scene_info = []
    for sid in chosen_scenes:
        f = feats[sid]
        az, off = azimuth_deg(f["geometry"], f["properties"]["sat:orbit_state"])
        mine = vs[(vs.scene_id == sid) & (vs.snr > 20) & (vs.dist_to_land_km >= 3.0)]
        scene_info.append({"scene_id": sid, "slot": pass_slot[sv.pass_of(sid)],
                           "azimuth_deg": round(az, 1), "deg_from_prior": round(off, 1),
                           "detections": int(len(mine))})
        if mine.empty:
            continue
        image, _ = sar_scene.read_scene(f["assets"]["vv"]["href"])
        prof = profiles(image.astype(np.float32), mine.grid_row.to_numpy(),
                        mine.grid_col.to_numpy(), az, pixel_m)
        del image
        for i, r in enumerate(mine.itertuples()):
            rows_out.append({"scene_id": sid, "slot": pass_slot[sv.pass_of(sid)],
                             "dist_to_land_km": float(r.dist_to_land_km),
                             "snr": float(r.snr), "peak_dn": float(r.peak_dn),
                             "same_slot_covered": int(r.same_slot_covered),
                             "same_slot_hits": int(r.same_slot_hits),
                             "profile": prof[i]})
        logging.info("%s az %.1f: %d detections", sid, az, len(mine))

    def window_max(p, direction, lo, hi):
        di = 0 if direction == "along" else 1
        k = (D_KM >= lo - 1e-9) & (D_KM <= hi + 1e-9)
        return p[di][:, k].max()

    def summarise(sel, ratio):
        out = {}
        for name, (direction, lo, hi) in WINDOWS.items():
            out[name] = share([window_max(r["profile"], direction, lo, hi) >= ratio * r["peak_dn"]
                               for r in sel])
        return out

    def classes(rows):
        scored = [r for r in rows if r["same_slot_covered"] >= 2]
        return {"all": rows,
                "same_slot_recurring": [r for r in scored if r["same_slot_hits"] > 0],
                "not_recurring": [r for r in scored if r["same_slot_hits"] == 0]}

    band = {"3-10": [r for r in rows_out if r["dist_to_land_km"] < 10],
            "10+": [r for r in rows_out if r["dist_to_land_km"] >= 10]}
    report = {"plan": "docs/sar_ambiguity.md", "passes": chosen, "scenes": scene_info,
              "settings": {"d_km": [float(D_KM[0]), float(D_KM[-1]), 0.1], "box_m":
                           [BOX_LONG_M, BOX_WIDE_M, STEP_M], "ratio": RATIO,
                           "sensitivity": SENSITIVITY, "recurrence_radius_km": 0.1}}
    for bname, rows in band.items():
        report[bname] = {}
        for cname, sel in classes(rows).items():
            entry = {"n": len(sel), f"x{RATIO:g}": summarise(sel, RATIO)}
            for s in SENSITIVITY:
                entry[f"x{s:g}"] = summarise(sel, s)
            # Where the brightest along-azimuth source sits, for those with one.
            ds = []
            for r in sel:
                along = r["profile"][0].max(axis=0)       # both sides
                if along.max() >= RATIO * r["peak_dn"]:
                    ds.append(float(D_KM[int(along.argmax())]))
            entry["d_of_brightest_along_source_km"] = {
                f"{lo:.1f}-{lo + 0.5:.1f}": int(sum(lo <= d < lo + 0.5 for d in ds))
                for lo in np.arange(2.0, 9.0, 0.5)}
            report[bname][cname] = entry
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("3-10", "10+")}, indent=1))
    print(json.dumps(scene_info, indent=0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
