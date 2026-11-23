"""Optical look at the places strong radar detections recur: build the chips.

    HF_HUB_DISABLE_XET=1 python scripts/sar_recur_s2.py --work DIR

Implements docs/sar_recur_s2.md (committed before this was run). Writes to
DIR: `sites.json` (sites and controls with no label), `pass1/<code>.png` and
`pass2/<code>.png` (montages under random codes), `key.json` (code -> point;
not to be read until both label passes are saved), and `labels_pass1.json` /
`labels_pass2.json` are written by hand afterwards. `--score` then joins them.
Needs requirements-test.txt plus pandas and Pillow; nothing secret.
"""

import argparse
import json
import logging
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sar_scene  # noqa: E402
import sar_survey as sv  # noqa: E402
from sar_columns import DETECTOR_VERSION  # noqa: E402

N_SITES = 15
MIN_PASSES = 3
LINK_KM = 0.1
CELL_KM = 5.0
CONTROL_CLEAR_KM = 1.0
SEED = 20261123
PERIOD = "2026-09-01T00:00:00Z/2026-11-22T23:59:59Z"
CHIP_M = 800
DATES = 4
MAX_BAD = 0.10
BAD_SCL = (0, 3, 8, 9, 10)        # nodata, cloud shadow, cloud med/high, cirrus
SCALE = 4
STRETCH = (1000.0, 3000.0)        # L2A DN, incl. the +1000 BOA offset


def link(xy, radius):
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    pairs = cKDTree(xy).query_pairs(radius, output_type="ndarray")
    from scipy.sparse import coo_matrix
    n = len(xy)
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    return connected_components(g, directed=False)[1]


def pick_points(dest):
    sv._download(dest)
    scenes = pd.concat([pd.read_parquet(f) for f in
                        sorted((dest / "sar" / "scenes" / DETECTOR_VERSION).glob("*.parquet"))])
    det = pd.concat([pd.read_parquet(f) for f in
                     sorted((dest / "sar" / "det" / DETECTOR_VERSION).glob("*.parquet"))])
    ok = set(scenes[scenes.status == "ok"].scene_id)
    acq = dict(zip(scenes.scene_id, scenes.acq_time.astype(str)))
    det = det[det.scene_id.isin(ok)]
    vs_all = det[det.vessel_sized]
    strong = vs_all[(vs_all.snr > 20) & (vs_all.dist_to_land_km >= 3)].reset_index(drop=True)
    comp = link(sv.xy_km(strong.longitude, strong.latitude), LINK_KM)
    strong["place"] = comp
    strong["pass"] = strong.scene_id.map(sv.pass_of)
    strong["slot"] = strong.scene_id.map(lambda s: sv.geometry_of(acq[s]))
    g = strong.groupby("place").agg(passes=("pass", "nunique"), snr=("snr", "mean"),
                                    lon=("longitude", "median"), lat=("latitude", "median"),
                                    slots=("slot", lambda s: sorted(set(s))),
                                    dist=("dist_to_land_km", "median"))
    g = g[g.passes >= MIN_PASSES].sort_values(["passes", "snr"], ascending=False).head(N_SITES)

    land = sar_scene.load_land_mask()
    from scipy import ndimage
    from scipy.spatial import cKDTree
    dist_km = ndimage.distance_transform_edt(~land, sampling=sar_scene.pixel_metres()) / 1000
    transform = sar_scene.aoi_grid()[0]
    tree = cKDTree(sv.xy_km(vs_all.longitude, vs_all.latitude))
    rng = np.random.default_rng(SEED)
    points = []
    for r in g.itertuples():
        points.append({"kind": "site", "lon": r.lon, "lat": r.lat, "passes": int(r.passes),
                       "slots": r.slots, "snr_mean": round(r.snr, 1),
                       "dist_to_land_km": round(r.dist, 2)})
        cx, cy = sv.xy_km(r.lon, r.lat)[0]
        x0 = math.floor(cx / CELL_KM) * CELL_KM
        y0 = math.floor(cy / CELL_KM) * CELL_KM
        for _ in range(10000):
            x, y = x0 + rng.uniform(0, CELL_KM), y0 + rng.uniform(0, CELL_KM)
            lon, lat = x / sv.KM_PER_DEG_LON, y / sv.KM_PER_DEG_LAT
            col, row = ~transform * (lon, lat)
            row, col = int(row), int(col)
            if not (0 <= row < dist_km.shape[0] and 0 <= col < dist_km.shape[1]):
                continue
            if dist_km[row, col] < 3:
                continue
            if tree.query([x, y])[0] < CONTROL_CLEAR_KM:
                continue
            points.append({"kind": "control", "lon": lon, "lat": lat,
                           "dist_to_land_km": round(float(dist_km[row, col]), 2)})
            break
        else:
            points.append({"kind": "control", "lon": None, "lat": None,
                           "note": "no clear water in the cell"})
    return points


def s2_items(lon, lat):
    import time
    import urllib.error
    for attempt in range(6):
        try:
            return _s2_items(lon, lat)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 5:
                raise
            time.sleep(10 * 2 ** attempt)


def _s2_items(lon, lat):
    body = {"collections": ["sentinel-2-l2a"],
            "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": PERIOD, "limit": 100,
            "query": {"eo:cloud_cover": {"lt": 60}}}
    return sar_scene._get_json(sar_scene.STAC_SEARCH, body).get("features", [])


_TOKEN = {}


def signed(href):
    """One SAS token per collection, renewed every 30 minutes, with backoff on
    429: signing every href separately got rate-limited on the first run."""
    import time
    import urllib.error
    import urllib.request
    now = time.time()
    if "s2" not in _TOKEN or now - _TOKEN["s2"][1] > 1800:
        for attempt in range(6):
            try:
                with urllib.request.urlopen("https://planetarycomputer.microsoft.com/api/"
                                            "sas/v1/token/sentinel-2-l2a", timeout=30) as r:
                    _TOKEN["s2"] = (json.load(r)["token"], now)
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 5:
                    raise
                time.sleep(10 * 2 ** attempt)
    return href + "?" + _TOKEN["s2"][0]


def read_chip(href, lon, lat, size_m, out_px):
    import rasterio
    from rasterio.warp import transform as warp
    from rasterio.windows import from_bounds
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), \
            rasterio.open("/vsicurl/" + signed(href)) as src:
        xs, ys = warp("EPSG:4326", src.crs, [lon], [lat])
        h = size_m / 2
        win = from_bounds(xs[0] - h, ys[0] - h, xs[0] + h, ys[0] + h, src.transform)
        return src.read(1, window=win, out_shape=(out_px, out_px), boundless=True, fill_value=0)


def chips_for(pt):
    px = CHIP_M // 10
    cands = []
    for f in sorted(s2_items(pt["lon"], pt["lat"]), key=lambda f: f["properties"]["eo:cloud_cover"]):
        try:
            scl = read_chip(f["assets"]["SCL"]["href"], pt["lon"], pt["lat"], CHIP_M, px)
        except Exception as exc:          # one bad item should not end the run
            logging.warning("SCL %s: %s", f["id"], exc)
            continue
        bad = float(np.isin(scl, BAD_SCL).mean())
        if bad < MAX_BAD:
            cands.append((f, bad))
        if len(cands) >= DATES:
            break
    out = []
    for f, bad in sorted(cands, key=lambda c: c[0]["properties"]["datetime"]):
        rgb = np.dstack([read_chip(f["assets"][b]["href"], pt["lon"], pt["lat"], CHIP_M, px)
                         for b in ("B04", "B03", "B02")]).astype(np.float32)
        out.append({"date": f["properties"]["datetime"][:10], "bad": round(bad, 3), "rgb": rgb})
    return out


def montage(chips, mirror):
    from PIL import Image, ImageDraw
    lo, hi = STRETCH
    panels = []
    for c in chips:
        a = np.clip((c["rgb"] - lo) / (hi - lo), 0, 1)
        a = (a ** 0.7 * 255).astype(np.uint8)
        im = Image.fromarray(a).resize((a.shape[1] * SCALE, a.shape[0] * SCALE), Image.NEAREST)
        if mirror:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        d = ImageDraw.Draw(im)
        w = im.size[0]
        half = 150 / 10 * SCALE
        d.rectangle([w / 2 - half, w / 2 - half, w / 2 + half, w / 2 + half], outline=(255, 0, 255))
        panels.append(im)
    w = sum(p.size[0] for p in panels) + 6 * (len(panels) - 1)
    out = Image.new("RGB", (w, panels[0].size[1]), (40, 40, 40))
    x = 0
    for p in panels:
        out.paste(p, (x, 0))
        x += p.size[0] + 6
    return out


def build(work: Path, data: Path):
    points = pick_points(data)
    (work / "sites.json").write_text(json.dumps(points, indent=1))
    rnd = random.Random(SEED)
    keys = {}
    for p in ("pass1", "pass2"):
        (work / p).mkdir(parents=True, exist_ok=True)
    codes1 = rnd.sample(range(100, 1000), len(points))
    codes2 = rnd.sample(range(100, 1000), len(points))
    for i, pt in enumerate(points):
        entry = {"point": i, "n_dates": 0}
        if pt["lon"] is not None:
            chips = chips_for(pt)
            entry.update(n_dates=len(chips), dates=[c["date"] for c in chips],
                         bad=[c["bad"] for c in chips])
            if len(chips) >= 2:
                montage(chips, False).save(work / "pass1" / f"{codes1[i]}.png")
                montage(chips, True).save(work / "pass2" / f"{codes2[i]}.png")
        keys[f"pass1/{codes1[i]}"] = entry
        keys[f"pass2/{codes2[i]}"] = entry
        logging.info("point %d/%d: %d usable dates", i + 1, len(points), entry["n_dates"])
    (work / "key.json").write_text(json.dumps(keys, indent=1))


def kappa(a, b, cats):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def score(work: Path):
    from scipy.stats import beta
    points = json.loads((work / "sites.json").read_text())
    keys = json.loads((work / "key.json").read_text())
    l1 = json.loads((work / "labels_pass1.json").read_text())
    l2 = json.loads((work / "labels_pass2.json").read_text())
    lab = {}
    for code, label in l1.items():
        lab.setdefault(keys[f"pass1/{code}"]["point"], {})["p1"] = label
    for code, label in l2.items():
        lab.setdefault(keys[f"pass2/{code}"]["point"], {})["p2"] = label
    cats = ["fixed", "intermittent", "nothing"]

    def cp(k, n):
        lo = 0.0 if k == 0 else float(beta.ppf(0.025, k, n - k + 1))
        hi = 1.0 if k == n else float(beta.ppf(0.975, k + 1, n - k))
        return [round(lo, 3), round(hi, 3)]

    rows = []
    for i, pt in enumerate(points):
        e = next(v for k, v in keys.items() if v["point"] == i)
        rows.append({**pt, "n_dates": e["n_dates"], "dates": e.get("dates"),
                     "label": lab.get(i, {}).get("p1", "no imagery"),
                     "label_pass2": lab.get(i, {}).get("p2", "no imagery")})
    out = {"plan": "docs/sar_recur_s2.md", "points": rows}
    for kind in ("site", "control"):
        sel = [r for r in rows if r["kind"] == kind and r["label"] != "no imagery"]
        out[kind] = {c: {"k": sum(r["label"] == c for r in sel), "n": len(sel),
                         "ci95": cp(sum(r["label"] == c for r in sel), len(sel))}
                     for c in cats}
        out[kind]["no_imagery"] = sum(r["kind"] == kind and r["label"] == "no imagery"
                                      for r in rows)
    both = [r for r in rows if r["label"] != "no imagery"]
    a, b = [r["label"] for r in both], [r["label_pass2"] for r in both]
    out["agreement"] = {"n": len(both), "raw": round(sum(x == y for x, y in zip(a, b)) / len(both), 3),
                        "kappa": kappa(a, b, cats),
                        "disagreements": [{"kind": r["kind"], "lon": r["lon"], "lat": r["lat"],
                                           "p1": r["label"], "p2": r["label_pass2"]}
                                          for r in both if r["label"] != r["label_pass2"]]}
    print(json.dumps({k: out[k] for k in ("site", "control", "agreement")}, indent=1))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", required=True)
    ap.add_argument("--data", default="sar-survey-data")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "sar_recur_s2.json"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    if args.score:
        Path(args.out).write_text(json.dumps(score(work), indent=1) + "\n")
    else:
        build(work, Path(args.data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
