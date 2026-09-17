"""Finding Sentinel-1 scenes over the strait and reading the AOI out of them.

Nothing here needs an account. Planetary Computer answers both the STAC
search and the SAS signing anonymously, which is the whole reason this is a
usable replacement for a feed that went dark: there is no key to lose.

The scenes are 700 MB COGs referenced by GCPs rather than by a geotransform.
Two consequences drive the code below:

  * `rasterio.open()` reports `crs is None` and an identity transform. Any
    code that reaches for `src.xy()` or `windows.from_bounds()` on the raw
    dataset gets pixel indices dressed up as degrees. The GCPs have to be
    handed to a WarpedVRT explicitly.
  * A scene is a slice, not a map sheet. It usually covers part of the AOI
    and nothing says which part, so every read reports how much it covered.
"""

import json
import logging
import time
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError, WindowError
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds

from sar_columns import (AOI_EAST, AOI_NORTH, AOI_RES_DEG, AOI_SOUTH, AOI_WEST)

logger = logging.getLogger(__name__)

STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
# The per-collection token endpoint works for sentinel-1-grd but not for
# every collection; the sign endpoint works for all of them, so there is one
# way in rather than two.
SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href="
COLLECTION = "sentinel-1-grd"

# /vsicurl settings. GDAL_DISABLE_READDIR_ON_OPEN stops GDAL probing for
# sidecar files it will never find; without it the blob store answers 403 for
# each probe. Keep it scoped to the read: exported globally it also makes the
# ENVI driver write a 2-byte file and still exit 0.
GDAL_OPTS = {
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tiff",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MAX_RETRY": "8",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}


def aoi_bounds() -> tuple[float, float, float, float]:
    return AOI_WEST, AOI_SOUTH, AOI_EAST, AOI_NORTH


def aoi_grid(bounds=None, res: float | None = None) -> tuple[Affine, int, int]:
    """The grid a scene is resampled onto.

    The default is fixed on purpose: `grid_row` / `grid_col` on a detection
    are only an anchor for as long as the grid does not move under them. The
    argument exists so that a smoke run can take a small bite of the same
    grid without redefining what the production one is.
    """
    west, south, east, north = bounds or aoi_bounds()
    res = res or AOI_RES_DEG
    width = round((east - west) / res)
    height = round((north - south) / res)
    return Affine(res, 0.0, west, 0.0, -res, north), width, height


def _get_json(url: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def search(start: str, end: str, limit: int = 100) -> list[dict]:
    """Scenes whose footprint touches the AOI, newest first.

    `id` is Planetary Computer's, which drops the four-character suffix the
    SAFE name carries. It is still one row per acquisition slice, and it is
    what the output path is keyed on, so the two must not be mixed.
    """
    body = {
        "collections": [COLLECTION],
        "bbox": [AOI_WEST, AOI_SOUTH, AOI_EAST, AOI_NORTH],
        "datetime": f"{start}/{end}",
        "limit": limit,
    }
    features = _get_json(STAC_SEARCH, body).get("features", [])
    scenes = []
    for f in features:
        props = f.get("properties", {})
        vv = f.get("assets", {}).get("vv", {}).get("href")
        if not vv:
            logger.warning("%s has no vv asset, skipping", f.get("id"))
            continue
        scenes.append({
            "scene_id": f["id"],
            "acq_time": props.get("datetime"),
            "platform": props.get("platform", ""),
            "orbit_state": props.get("sat:orbit_state", ""),
            "polarization": "VV",
            "href": vv,
        })
    scenes.sort(key=lambda s: s["acq_time"] or "", reverse=True)
    return scenes


def sign(href: str, timeout: int = 30) -> str:
    """A read-only SAS URL. These expire in well under an hour, so sign late."""
    return _get_json(SIGN_URL + href, timeout=timeout)["href"]


def _aoi_window(vrt, bounds) -> Window | None:
    """The part of the warped scene that overlaps the AOI, or None."""
    want = from_bounds(*bounds, transform=vrt.transform)
    try:
        return want.intersection(Window(0, 0, vrt.width, vrt.height))
    except WindowError:
        return None


def read_aoi(source: str, bounds=None, res: float | None = None
              ) -> tuple[np.ndarray, float]:
    """Resample one scene onto the AOI grid.

    Returns the amplitude image and the fraction of the AOI the scene
    actually carried. Outside the swath the array is 0, which is also the
    file's nodata: a zero means "not measured here", never "dark sea", and
    every statistic downstream has to exclude it.

    The warp is done in two steps on purpose. Handing `WarpedVRT` an explicit
    destination grid alongside GCPs produces a constant image with no error
    raised at all — a 5000x5000 read of a scene over Dubai came back with
    every pixel between 42 and 45. So the VRT is built the way it wants to be
    built, its own window is read, and the alignment onto the fixed AOI grid
    is a second, ordinary same-CRS resample. `tests/test_sar_geocode.py`
    pins this down with a synthetic scene whose answer is known.
    """
    bounds = bounds or aoi_bounds()
    res = res or AOI_RES_DEG
    transform, width, height = aoi_grid(bounds, res)
    dst = np.zeros((height, width), dtype=np.uint16)
    with rasterio.Env(**GDAL_OPTS), rasterio.open(source) as src:
        gcps, gcp_crs = src.gcps
        if src.crs is None and not gcps:
            raise ValueError("scene has neither a CRS nor GCPs; cannot place it")
        kwargs = {"crs": "EPSG:4326", "resampling": Resampling.average,
                  "src_nodata": 0, "nodata": 0}
        if gcps:
            kwargs["src_crs"] = gcp_crs
        with WarpedVRT(src, **kwargs) as vrt:
            window = _aoi_window(vrt, bounds)
            if window is None or window.width < 1 or window.height < 1:
                logger.info("scene does not reach the AOI")
                return dst, 0.0
            # Read straight down to roughly the AOI posting rather than at the
            # VRT's native one: a quarter of the memory for the same numbers,
            # since the second step would average it down anyway.
            scale = vrt.res[0] / res
            out_h = max(1, round(window.height * scale))
            out_w = max(1, round(window.width * scale))
            patch = vrt.read(1, window=window, out_shape=(out_h, out_w),
                             resampling=Resampling.average)
            patch_transform = vrt.window_transform(window) * Affine.scale(
                window.width / out_w, window.height / out_h)
        reproject(source=patch, destination=dst,
                  src_transform=patch_transform, src_crs="EPSG:4326", src_nodata=0,
                  dst_transform=transform, dst_crs="EPSG:4326", dst_nodata=0,
                  resampling=Resampling.average)
    covered = float((dst > 0).mean())
    logger.info("read %d x %d, scene covers %.1f%% of the AOI",
                width, height, 100 * covered)
    return dst, covered


MASK_PATH = (Path(__file__).resolve().parent.parent / "data" / "sar_water_mask.tif")


def load_land_mask(path: str | None = None, bounds=None,
                   res: float | None = None) -> np.ndarray:
    """The committed coastline, on the requested grid, True where there is land.

    Stored finer than the detection grid and reduced with a maximum, so a
    coastal cell that is part land stays land. Erring the other way would put
    the brightest thing in the image — the shore itself — into the sea, and a
    rock too small to survive a nearest-neighbour read is exactly the thing
    that then appears as a vessel moored in the same spot on every pass.
    """
    west, south, east, north = bounds or aoi_bounds()
    res = res or AOI_RES_DEG
    _, width, height = aoi_grid((west, south, east, north), res)
    with rasterio.open(path or MASK_PATH) as src:
        fine_res = src.transform.a
        if abs(fine_res) < 1e-12 or abs(res / fine_res - round(res / fine_res)) > 1e-6:
            raise ValueError(f"grid of {res} deg is not a whole number of "
                             f"{fine_res} deg mask pixels")
        factor = int(round(res / fine_res))
        window = from_bounds(west, south, east, north, transform=src.transform)
        for name, value in (("col_off", window.col_off), ("row_off", window.row_off),
                            ("width", window.width), ("height", window.height)):
            if abs(value - round(value)) > 1e-6:
                raise ValueError(f"requested box is not aligned to the mask "
                                 f"({name}={value}); it has to start and end on "
                                 f"a mask pixel")
        window = Window(round(window.col_off), round(window.row_off),
                        round(window.width), round(window.height))
        if (window.col_off < 0 or window.row_off < 0
                or window.col_off + window.width > src.width
                or window.row_off + window.height > src.height):
            raise ValueError(f"requested box {(west, south, east, north)} reaches "
                             f"outside the mask, which covers {tuple(src.bounds)}")
        land = src.read(1, window=window) > 0
    if factor > 1:
        land = land.reshape(height, factor, width, factor).max(axis=(1, 3))
    return land


def read_scene(href: str, bounds=None, res: float | None = None,
               attempts: int = 3, pause: float = 20.0) -> tuple[np.ndarray, float]:
    """Sign and read one scene, signing again on each attempt.

    The blob store drops reads occasionally — a partial-content response GDAL
    reports as a failure, a `RasterioIOError` part way through a 40-second
    read. Retrying is not just for that: a SAS URL is good for about
    three quarters of an hour, so a retry that reused the first signature
    would eventually fail for a second reason. Each attempt gets its own.
    """
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return read_aoi("/vsicurl/" + sign(href), bounds=bounds, res=res)
        except (RasterioIOError, OSError) as exc:
            last = exc
            logger.warning("read failed (attempt %d of %d): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(pause)
    raise RuntimeError(f"could not read the scene after {attempts} attempts") from last
