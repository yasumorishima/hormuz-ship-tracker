"""Recall of the shipped detector against AIS, on scenes nobody tuned on.

    python scripts/sar_recall_holdout.py [--out docs/sar_recall_holdout.json]

The plan — what is scored, how matching works, what is reported and what
would count as what — is in docs/sar_recall_holdout.md and was committed
before this was run. This file implements that plan and nothing looser.

Needs the SAR requirements plus pandas, and nothing secret: the AIS archive
is public on the Hub and the imagery is anonymous on Planetary Computer. The
WorldCover mask for the box is built on first run and cached next to the
output (a few minutes, well under a gigabyte of memory for this box).
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage
from scipy.stats import beta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import sar_detect  # noqa: E402
import sar_scene  # noqa: E402
from sar_columns import MAP_MIN_SHORE_KM, MAP_MIN_SNR  # noqa: E402

BOX = (54.8, 24.9, 55.6, 25.8)                 # west, south, east, north
PERIOD = ("2026-03-14T00:00:00Z", "2026-04-12T00:00:00Z")
# Both slices of the two passes K_SIGMA was chosen on.
SELECTION_PASSES = {"20260315T14", "20260318T02"}
AIS_URL = ("https://huggingface.co/datasets/yasumorishima/hormuz-ais/"
           "resolve/main/positions.parquet")
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"

MAX_REPORT_GAP_S = 120
MATCH_M = 300.0
SHIFT_M = 2000.0
SHIFT_DIRS = range(0, 360, 45)
DIST_BANDS = [(0.2, 1), (1, 3), (3, 10), (10, math.inf)]
LEN_BANDS = [("<50 m", 0, 50), ("50-150 m", 50, 150), (">150 m", 150, math.inf)]
# AIS "not available" values, and a ceiling above which a reported speed is
# treated as unknown rather than dead-reckoned across a kilometre.
SOG_NA, COG_NA, SOG_TRUST_KN = 102.3, 360.0, 40.0
COLOCATED_M = 30.0


def pass_key(scene_id: str) -> str:
    return scene_id[17:28]          # e.g. 20260321T14 — date and hour of the pass


def held_out_scenes():
    body = {"collections": ["sentinel-1-grd"], "bbox": list(BOX),
            "datetime": "/".join(PERIOD), "limit": 100}
    req = urllib.request.Request(STAC, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as response:
        feats = json.load(response)["features"]
    scenes = [{"scene_id": f["id"], "acq_time": f["properties"]["datetime"],
               "href": f["assets"]["vv"]["href"]} for f in feats if "vv" in f["assets"]]
    scenes.sort(key=lambda s: s["acq_time"])
    return [s for s in scenes if pass_key(s["scene_id"]) not in SELECTION_PASSES]


def load_ais(cache: Path) -> pd.DataFrame:
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(AIS_URL, cache)
    d = pd.read_parquet(cache)
    d["t"] = pd.to_datetime(d["received_at"], utc=True, format="ISO8601")
    w, s, e, n = BOX
    pad = 0.05
    return d[d.longitude.between(w - pad, e + pad) & d.latitude.between(s - pad, n + pad)]


def ais_at(ais: pd.DataFrame, when: pd.Timestamp) -> pd.DataFrame:
    """One dead-reckoned position per MMSI, from its report nearest `when`."""
    near = ais[(ais.t - when).abs() <= pd.Timedelta(seconds=MAX_REPORT_GAP_S)].copy()
    near["gap"] = (when - near.t).dt.total_seconds()
    near = near.loc[near.gap.abs().groupby(near.mmsi).idxmin()]
    sog = near.speed.where((near.speed < SOG_NA) & (near.speed <= SOG_TRUST_KN), 0.0)
    cog = near.course.where(near.course < COG_NA, 0.0)
    metres = sog * 1852.0 / 3600.0 * near.gap
    north = metres * np.cos(np.radians(cog))
    east = metres * np.sin(np.radians(cog))
    near["lat"] = near.latitude + north / 111_320.0
    near["lon"] = near.longitude + east / (111_320.0 * np.cos(np.radians(near.latitude)))
    # Post hoc (not in the plan; see the deviations in the .md): three or more
    # hulls within 30 m of each other cannot all be where they say. On
    # 03-19..03-21 323 MMSIs reported one point at 25.5516 N 55.3814 E.
    lat, lon = near.lat.to_numpy(), near.lon.to_numpy()
    others = [int((metres_between(lon[i], lat[i], lon, lat) <= COLOCATED_M).sum()) - 1
              for i in range(len(near))]
    near["colocated"] = np.array(others) >= 2
    return near[["mmsi", "lat", "lon", "speed", "length", "gap", "colocated"]]


def land_mask(cache: Path) -> np.ndarray:
    if not cache.exists():
        import generate_sar_water_mask as gen
        land, transform = gen.build_land(BOX)
        with rasterio.open(cache, "w", driver="GTiff", width=land.shape[1],
                           height=land.shape[0], count=1, dtype="uint8",
                           crs="EPSG:4326", transform=transform,
                           compress="deflate", tiled=True, nbits=1) as dst:
            dst.write(land, 1)
    return sar_scene.load_land_mask(str(cache), bounds=BOX)


def metres_between(lon1, lat1, lon2, lat2):
    k = 111_320.0
    dx = (lon2 - lon1) * k * np.cos(np.radians((lat1 + lat2) / 2))
    dy = (lat2 - lat1) * k
    return np.hypot(dx, dy)


def nearest(lon, lat, dets):
    return nearest_at(lon, lat, dets)[0]


def nearest_at(lon, lat, dets):
    if not len(dets):
        return math.inf, None
    m = metres_between(lon, lat, dets[:, 0], dets[:, 1])
    i = int(m.argmin())
    return float(m[i]), i


def cp_interval(k, n, a=0.05):
    if n == 0:
        return (float("nan"), float("nan"))
    lo = 0.0 if k == 0 else beta.ppf(a / 2, k, n - k + 1)
    hi = 1.0 if k == n else beta.ppf(1 - a / 2, k + 1, n - k)
    return (round(float(lo), 3), round(float(hi), 3))


def rate(rows, key="found"):
    k = sum(bool(r[key]) for r in rows)
    return {"found": k, "scored": len(rows), "recall": round(k / len(rows), 3) if rows else None,
            "ci95": cp_interval(k, len(rows))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "docs" / "sar_recall_holdout.json"))
    ap.add_argument("--cache", default=os.environ.get("SAR_RECALL_CACHE", "/tmp/sar_recall"))
    args = ap.parse_args(argv)
    cache = Path(args.cache)

    ais = load_ais(cache / "positions.parquet")
    land = land_mask(cache / "dubai_water_mask.tif")
    transform, width, height = sar_scene.aoi_grid(BOX)
    pixel_m = sar_scene.pixel_metres(BOX)
    dist_m = ndimage.distance_transform_edt(~land, sampling=pixel_m)
    beyond_buffer = dist_m > sar_detect.COAST_BUFFER_M

    def cell(lon, lat):
        col, row = ~transform * (lon, lat)
        r, c = int(math.floor(row)), int(math.floor(col))
        return (r, c) if 0 <= r < height and 0 <= c < width else None

    per_vessel = {}           # (pass, mmsi) -> row
    chance = {}               # (pass, mmsi, dir) -> found
    matched = {}              # (scene, detection) -> MMSIs it was matched to
    scenes_out, outside = [], {"buffer_or_land": 0, "no_pixel": 0}
    for scene in held_out_scenes():
        when = pd.Timestamp(scene["acq_time"])
        vessels = ais_at(ais, when)
        entry = {"scene_id": scene["scene_id"], "acq_time": scene["acq_time"],
                 "ais_vessels_within_2min": int(len(vessels))}
        scenes_out.append(entry)
        if vessels.empty:
            entry["status"] = "no AIS"
            print(scene["scene_id"], "no AIS within 2 min; not read")
            continue
        image, covered = sar_scene.read_scene(scene["href"], bounds=BOX)
        entry["box_covered_frac"] = round(covered, 3)
        rows, stats = sar_detect.detect(image, land, transform, pixel_m)
        vs = [r for r in rows if r["vessel_sized"]]
        dets = np.array([[r["longitude"], r["latitude"]] for r in vs]) if vs else np.zeros((0, 2))
        cut = np.array([[r["longitude"], r["latitude"]] for r in vs
                        if r["dist_to_land_km"] >= MAP_MIN_SHORE_KM and r["snr"] > MAP_MIN_SNR]
                       ) if vs else np.zeros((0, 2))
        anyd = np.array([[r["longitude"], r["latitude"]] for r in rows]) if rows else np.zeros((0, 2))
        scored_water = (image > 0) & beyond_buffer
        entry.update({"vessel_sized": len(vs), "candidates": len(rows),
                      "scored_water_km2": round(stats["scored_water_km2"], 1)})
        key = pass_key(scene["scene_id"])
        for v in vessels.itertuples():
            rc = cell(v.lon, v.lat)
            if rc is None or image[rc] <= 0:
                outside["no_pixel"] += 1
                continue
            if not scored_water[rc]:
                outside["buffer_or_land"] += 1
                continue
            d, which = nearest_at(v.lon, v.lat, dets)
            if d <= MATCH_M:
                matched.setdefault((scene["scene_id"], which), set()).add(int(v.mmsi))
            row = per_vessel.setdefault((key, int(v.mmsi)), {
                "pass": key, "mmsi": int(v.mmsi), "found": False, "found_cut": False,
                "dist_to_land_km": round(float(dist_m[rc]) / 1000, 2),
                "speed_kn": None if v.speed >= SOG_NA else float(v.speed),
                "length_m": (None if pd.isna(v.length) or v.length <= 0
                             else float(v.length)),
                "colocated": bool(v.colocated),
                "report_gap_s": round(float(v.gap), 1),
                "nearest_vessel_sized_m": math.inf, "nearest_any_candidate_m": math.inf,
                "scenes": []})
            row["scenes"].append(scene["scene_id"])
            row["nearest_vessel_sized_m"] = min(row["nearest_vessel_sized_m"], d)
            row["nearest_any_candidate_m"] = min(row["nearest_any_candidate_m"],
                                                 nearest(v.lon, v.lat, anyd))
            row["found"] |= d <= MATCH_M
            row["found_cut"] |= nearest(v.lon, v.lat, cut) <= MATCH_M
            for bearing in SHIFT_DIRS:
                dy = SHIFT_M * math.cos(math.radians(bearing)) / 111_320.0
                dx = SHIFT_M * math.sin(math.radians(bearing)) / (
                    111_320.0 * math.cos(math.radians(v.lat)))
                src = cell(v.lon + dx, v.lat + dy)
                if src is None or not scored_water[src]:
                    continue
                k = (key, int(v.mmsi), bearing)
                chance[k] = chance.get(k, False) or (
                    nearest(v.lon + dx, v.lat + dy, dets) <= MATCH_M)
        print(scene["scene_id"], entry)

    scored = list(per_vessel.values())
    by_pass = {}
    for r in scored:
        by_pass.setdefault(r["pass"], []).append(r)
    result = {
        "plan": "docs/sar_recall_holdout.md",
        "settings": {"box": BOX, "period": PERIOD, "excluded_passes": sorted(SELECTION_PASSES),
                     "k_sigma": sar_detect.K_SIGMA, "coast_buffer_m": sar_detect.COAST_BUFFER_M,
                     "match_m": MATCH_M, "max_report_gap_s": MAX_REPORT_GAP_S,
                     "shift_m": SHIFT_M, "shift_dirs": list(SHIFT_DIRS),
                     "map_cut": {"min_shore_km": MAP_MIN_SHORE_KM, "min_snr": MAP_MIN_SNR},
                     "sog_trusted_up_to_kn": SOG_TRUST_KN},
        "primary": rate(scored),
        "per_pass": {k: rate(v) for k, v in sorted(by_pass.items())},
        "post_hoc_excluding_colocated": rate([r for r in scored if not r["colocated"]]),
        "post_hoc_colocated_only": rate([r for r in scored if r["colocated"]]),
        "post_hoc_production_cut_excluding_colocated": rate(
            [r for r in scored if not r["colocated"]
             and r["dist_to_land_km"] >= MAP_MIN_SHORE_KM], key="found_cut"),
        "chance_arm": {"found": sum(chance.values()), "points": len(chance),
                       "rate": round(sum(chance.values()) / len(chance), 3) if chance else None,
                       "ci95": cp_interval(sum(chance.values()), len(chance))},
        "post_hoc_chance_arm_excluding_colocated": (lambda c: {
            "found": sum(c), "points": len(c),
            "rate": round(sum(c) / len(c), 3) if c else None,
            "ci95": cp_interval(sum(c), len(c))})(
            [f for (p, m, _), f in chance.items() if not per_vessel[(p, m)]["colocated"]]),
        "production_cut_3km_snr20": rate([r for r in scored
                                          if r["dist_to_land_km"] >= MAP_MIN_SHORE_KM],
                                         key="found_cut"),
        "by_distance_km": {f"{lo}-{hi}": rate([r for r in scored
                                                if lo <= r["dist_to_land_km"] < hi])
                           for lo, hi in DIST_BANDS},
        "by_motion": {"moving >1 kn": rate([r for r in scored if (r["speed_kn"] or 0) > 1]),
                      "not moving": rate([r for r in scored if (r["speed_kn"] or 0) <= 1])},
        "by_length": {**{name: rate([r for r in scored if r["length_m"] and lo <= r["length_m"] < hi])
                         for name, lo, hi in LEN_BANDS},
                      "unknown": rate([r for r in scored if not r["length_m"]])},
        "not_scored": outside,
        "detections_matched_to_more_than_one_vessel": sum(len(m) > 1 for m in matched.values()),
        "misses": [{k: r[k] for k in ("pass", "colocated", "dist_to_land_km", "speed_kn", "length_m",
                                      "report_gap_s", "nearest_vessel_sized_m",
                                      "nearest_any_candidate_m")}
                   for r in scored if not r["found"]],
        "scenes": scenes_out,
    }
    for r in scored:
        r.pop("mmsi")
    Path(args.out).write_text(json.dumps(result, indent=1, default=str) + "\n")
    print(json.dumps({k: result[k] for k in ("primary", "post_hoc_excluding_colocated",
                                             "post_hoc_colocated_only",
                                             "post_hoc_production_cut_excluding_colocated",
                                             "chance_arm",
                                             "post_hoc_chance_arm_excluding_colocated",
                                             "production_cut_3km_snr20",
                                             "per_pass", "by_distance_km", "by_motion",
                                             "by_length", "not_scored", "misses")},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
