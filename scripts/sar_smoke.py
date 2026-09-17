"""Run the SAR pipeline against one real scene, with no credentials.

The unit tests build their own scenes, so they cannot notice if Planetary
Computer changes what it serves, if the scene's georeferencing moves, or if
the shipped mask stops lining up with the imagery. This does, for the same
reason `publish-smoke` renders the real archive: the data is public, so the
one part that cannot be checked by reading it can be checked before it runs
unattended.

    python scripts/sar_smoke.py            # assert the bands below
    python scripts/sar_smoke.py --report   # just print what it measured

The bands come from measurements on this scene, recorded in docs/PIPELINE.md.
They are wide enough for a different day's sea and narrow enough that an
empty read, a misplaced mask or a broken threshold falls outside.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
from rasterio.features import rasterize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import sar_detect  # noqa: E402
import sar_scene  # noqa: E402

SCENE_ID = "S1C_IW_GRDH_1SDV_20260916T020601_20260916T020631_009466_012D3D"
ITEM_URL = ("https://planetarycomputer.microsoft.com/api/stac/v1/collections/"
            f"sentinel-1-grd/items/{SCENE_ID}")
# A bite of the production grid over the strait itself, small enough for CI.
BOUNDS = (56.0, 26.2, 56.8, 26.9)

# name -> (low, high). The scene is pinned, so these are deterministic given
# the code; the width is for a different GDAL's resampling, not for a
# different sea. Measured on 2026-09-17 with rasterio 1.4:
#   covered 0.611, sea median 49.0 DN, scored water 3,539 km2,
#   84 candidates of which 43 vessels, longest 526.0 m.
BANDS = {
    "aoi_covered_frac": (0.50, 0.75),
    "sea_median_dn": (25.0, 100.0),
    "scored_water_km2": (3000.0, 4600.0),
    "candidates": (20, 2000),
    "vessels": (10, 400),
    "longest_m": (100.0, 600.0),
}


def ais_mask_on(bounds, shape, transform):
    """The AIS side's Natural Earth outline, burnt onto the same grid.

    Only used as a control: the point of the second mask is that the first one
    is not good enough here, and a number saying so is better than a claim.
    """
    path = Path(__file__).resolve().parent.parent / "data" / "land_mask.geojson"
    with open(path) as handle:
        shapes = [(f["geometry"], 1) for f in json.load(handle)["features"]]
    burnt = rasterize(shapes, out_shape=shape, transform=transform,
                      fill=0, dtype="uint8")
    return burnt.astype(bool)


def measure():
    with urllib.request.urlopen(ITEM_URL, timeout=60) as response:
        item = json.load(response)
    href = item["assets"]["vv"]["href"]
    image, covered = sar_scene.read_scene(href, bounds=BOUNDS)
    transform, width, height = sar_scene.aoi_grid(BOUNDS)
    land = sar_scene.load_land_mask(bounds=BOUNDS)

    pixel_m = sar_scene.pixel_metres(BOUNDS)
    rows, stats = sar_detect.detect(image, land, transform, pixel_m)
    ships = [r for r in rows if r["is_vessel"]]
    control_land = ais_mask_on(BOUNDS, (height, width), transform)
    control_rows, _ = sar_detect.detect(image, control_land, transform, pixel_m)
    control_ships = [r for r in control_rows if r["is_vessel"]]

    on_land = sum(bool(land[r["grid_row"], r["grid_col"]]) for r in rows)
    return {
        "scene_id": SCENE_ID,
        "acq_time": item["properties"]["datetime"],
        "aoi_covered_frac": round(covered, 4),
        "sea_median_dn": round(stats["sea_median_dn"], 1),
        "scored_water_km2": round(stats["scored_water_km2"], 1),
        "vessels": len(ships),
        "candidates": stats["n_candidates"],
        "detections_on_masked_land": on_land,
        "vessels_with_the_ais_mask": len(control_ships),
        "median_dist_to_land_km": round(
            float(np.median([r["dist_to_land_km"] for r in ships])), 2) if ships else None,
        "longest_m": round(max((r["length_m"] for r in ships), default=0.0), 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true",
                    help="print the measurements without asserting anything")
    args = ap.parse_args(argv)

    got = measure()
    print(json.dumps(got, indent=2))
    if args.report:
        return 0

    problems = []
    for name, (low, high) in BANDS.items():
        value = got[name]
        if value is None or not (low <= value <= high):
            problems.append(f"{name} is {value}, outside {low}..{high}")
    if got["detections_on_masked_land"]:
        problems.append(f"{got['detections_on_masked_land']} detections sit on "
                        f"pixels the mask calls land")
    # Measured 125 against 43 on this scene: the coarse outline leaves nearly
    # three times as many objects standing. If that gap closes, either the
    # shipped mask stopped resolving the fjords or the two files became the
    # same, and in both cases the second file has stopped earning its place.
    if got["vessels_with_the_ais_mask"] < 1.3 * got["vessels"]:
        problems.append(
            f"the AIS mask gives {got['vessels_with_the_ais_mask']} vessels "
            f"against {got['vessels']} (measured 125 against 43); the two masks "
            f"are no longer meaningfully different here")

    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
