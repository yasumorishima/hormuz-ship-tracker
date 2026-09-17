"""Build data/sar_water_mask.tif from ESA WorldCover 10m.

    python scripts/generate_sar_water_mask.py

Why a second mask, when data/land_mask.geojson already exists: that one is
Natural Earth 10m simplified to about 100 m, which is fine for deciding
whether an AIS report came from a building, and not fine for imagery. It
generalises the Musandam fjords away, so their water reads as land and the
ridges beside them read as sea. Measured on one Sentinel-1 scene with one
threshold, 500 m off the coast: 533 vessel-sized objects standing on the rocks
with that outline, 30 with this one.

Source: ESA WorldCover v200 (2021), 10 m, CC BY 4.0, read anonymously from
Planetary Computer. Class 80 is permanent water; class 0 is nodata. Nodata is
treated as water, which was checked separately rather than assumed: over the
part of the box one Sentinel-1 scene covered, GSHHG's full-resolution
coastline calls 0.0 km2 of the nodata land. The known points at the bottom of
this file are what the build asserts.

Requirements: rasterio, numpy (see requirements-sar.txt).
"""

import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sar_columns import AOI_EAST, AOI_NORTH, AOI_SOUTH, AOI_WEST  # noqa: E402

RES = 0.0001            # 10 m, the source's own posting
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href="
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
WATER_CLASSES = (0, 80)   # nodata and permanent water

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "sar_water_mask.tif"

# lon, lat, is_land. Read off a map, then checked against the result.
CHECKS = [
    ("strait centre", 56.40, 26.55, False),
    ("Khawr ash Shamm (fjord water)", 56.35, 26.20, False),
    ("Gulf of Oman", 57.20, 25.80, False),
    ("Musandam ridge", 56.28, 26.18, True),
    ("Khasab town", 56.24, 26.20, True),
    ("Qeshm island", 55.85, 26.85, True),
    ("Hormuz island", 56.46, 27.06, True),
    ("Larak island", 56.36, 26.87, True),
    ("Bandar Abbas shore", 56.28, 27.18, True),
]


def worldcover_tiles():
    body = {"collections": ["esa-worldcover"],
            "bbox": [AOI_WEST, AOI_SOUTH, AOI_EAST, AOI_NORTH], "limit": 50}
    request = urllib.request.Request(
        STAC, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        features = json.load(response)["features"]
    # Several years are published under the same collection; take the newest.
    newest = max(f["properties"]["start_datetime"] for f in features)
    tiles = [f["assets"]["map"]["href"] for f in features
             if f["properties"]["start_datetime"] == newest]
    print(f"{len(tiles)} WorldCover tiles from {newest[:10]}")
    return sorted(tiles)


def sign(href):
    with urllib.request.urlopen(SIGN + href, timeout=30) as response:
        return json.load(response)["href"]


def main():
    width = round((AOI_EAST - AOI_WEST) / RES)
    height = round((AOI_NORTH - AOI_SOUTH) / RES)
    transform = Affine(RES, 0.0, AOI_WEST, 0.0, -RES, AOI_NORTH)
    classes = np.zeros((height, width), dtype=np.uint8)

    env = rasterio.Env(CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                       GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                       GDAL_HTTP_MAX_RETRY="8", GDAL_HTTP_RETRY_DELAY="2",
                       VSI_CACHE="TRUE")
    for href in worldcover_tiles():
        print("  reading", href.rsplit("/", 1)[-1])
        with env, rasterio.open("/vsicurl/" + sign(href)) as src:
            # init_dest_nodata=False leaves what earlier tiles wrote alone, so
            # the tiles accumulate into one array instead of each needing a
            # full-size buffer of its own to be merged from afterwards.
            reproject(source=rasterio.band(src, 1), destination=classes,
                      src_transform=src.transform, src_crs=src.crs,
                      src_nodata=0, dst_nodata=0, init_dest_nodata=False,
                      dst_transform=transform, dst_crs="EPSG:4326",
                      resampling=Resampling.nearest)

    land = (~np.isin(classes, WATER_CLASSES)).astype(np.uint8)
    print(f"land {100 * land.mean():.2f}% of the box, "
          f"{int((classes == 0).sum())} pixels of nodata treated as water")

    ok = True
    for name, lon, lat, expect_land in CHECKS:
        col, row = ~transform * (lon, lat)
        got = bool(land[int(row), int(col)])
        if got != expect_land:
            ok = False
        print(f"  {'PASS' if got == expect_land else 'FAIL'} | {name}: "
              f"land={got} (expected {expect_land})")
    if not ok:
        print("::error::the mask disagrees with the map; not writing it")
        return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(OUTPUT, "w", driver="GTiff", width=width, height=height,
                       count=1, dtype="uint8", crs="EPSG:4326",
                       transform=transform, compress="deflate", zlevel=9,
                       tiled=True, blockxsize=512, blockysize=512, nbits=1) as dst:
        dst.write(land, 1)
        dst.update_tags(source="ESA WorldCover 10m v200 (2021)",
                        license="CC BY 4.0",
                        note="1 = land, 0 = water; nodata treated as water")
    print(f"\nwrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
