"""Finding vessels in one Sentinel-1 amplitude image.

The physics is kind here: at C band a calm sea reflects most of the energy
away from the satellite and a steel hull reflects a lot of it back, so a ship
is a bright compact object on a dark field. The work is in not calling the
land a ship.

Three things decide that, in order of how much they matter (measured on
S1C 2026-09-16T02:06Z over the strait, with the same threshold throughout):

  * the coastline. Natural Earth 10m leaves 533 vessel-sized objects standing
    on the ridges of the Musandam fjords; ESA WorldCover 10m leaves 30. It is
    not a close call and it is not a tuning parameter.
  * a margin off the coast. Terrain is not corrected in a GRD product, so a
    1,800 m ridge is laid over toward the sensor by roughly h/tan(theta).
    Detections are kept with their distance to land rather than thrown away,
    because within a kilometre of the shore real vessels and displaced rock
    are mixed and only the stored distance lets that be re-decided later.
  * a local background. Brightness falls off across the swath, so one
    threshold for the whole image is either deaf on one side or noisy on the
    other.
"""

import logging

import numpy as np
from scipy import ndimage

logger = logging.getLogger(__name__)

# Background is estimated on blocks this many pixels across and interpolated
# back. A block is ~5 km at 20 m: wide enough that vessels cannot move its
# median, small enough to follow the antenna pattern across the swath.
BLOCK = 256
MIN_BLOCK_FILL = 0.10          # a block with less water than this is borrowed

# A detection has to stand this many robust deviations over its background.
# Calibrated against AIS: see tests and docs/PIPELINE.md.
K_SIGMA = 8.0
MIN_AREA_PX = 3                # 1,200 m^2 at 20 m
MAX_LENGTH_M = 600.0           # longer than any ship afloat
COAST_BUFFER_M = 200.0         # below this the mask's own error dominates


def _block_stat(image: np.ndarray, valid: np.ndarray, block: int = BLOCK
                ) -> tuple[np.ndarray, np.ndarray]:
    """Median and MAD of the valid pixels in each block.

    The median is what makes this safe to run over an image that contains the
    very targets it is measuring the background of: a handful of bright
    pixels cannot move it, where a mean would be dragged up and the threshold
    with it.
    """
    h, w = image.shape
    ny, nx = (h + block - 1) // block, (w + block - 1) // block
    med = np.full((ny, nx), np.nan, dtype=np.float32)
    mad = np.full((ny, nx), np.nan, dtype=np.float32)
    for by in range(ny):
        ys = slice(by * block, min((by + 1) * block, h))
        for bx in range(nx):
            xs = slice(bx * block, min((bx + 1) * block, w))
            cell = image[ys, xs][valid[ys, xs]]
            if cell.size < MIN_BLOCK_FILL * block * block:
                continue
            m = float(np.median(cell))
            med[by, bx] = m
            mad[by, bx] = float(np.median(np.abs(cell - m)))
    return med, mad


def _fill_and_zoom(coarse: np.ndarray, shape: tuple[int, int],
                   block: int = BLOCK) -> np.ndarray:
    """Borrow from the nearest measured block, then stretch back to full size."""
    missing = np.isnan(coarse)
    if missing.all():
        raise ValueError("no block had enough water to measure a background")
    if missing.any():
        idx = ndimage.distance_transform_edt(
            missing, return_distances=False, return_indices=True)
        coarse = coarse[tuple(idx)]
    full = np.repeat(np.repeat(coarse, block, axis=0), block, axis=1)
    return full[:shape[0], :shape[1]]


def background(image: np.ndarray, valid: np.ndarray
               ) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel background level and robust spread, as float32 arrays."""
    med, mad = _block_stat(image, valid)
    bg = _fill_and_zoom(med, image.shape)
    # 1.4826 puts the MAD on the same footing as a standard deviation for a
    # normal distribution. Sea clutter is not normal, so K_SIGMA is calibrated
    # against AIS rather than read off a table; the scaling only keeps the
    # number in a familiar range.
    sd = _fill_and_zoom(mad, image.shape) * 1.4826
    np.maximum(sd, 1.0, out=sd)
    return bg, sd


def shape_of(mask_piece: np.ndarray, res_m: float) -> tuple[float, float, float]:
    """Length, width and orientation of one blob, from its second moments.

    A bounding box would call a 300 m ship lying diagonally a 420 m one, and
    ship length is the field most likely to be compared against something
    else later, so it is worth the moments.
    """
    ys, xs = np.nonzero(mask_piece)
    if ys.size < 2:
        return res_m, res_m, 0.0
    coords = np.stack([ys - ys.mean(), xs - xs.mean()]).astype(np.float64)
    cov = coords @ coords.T / coords.shape[1]
    vals, vecs = np.linalg.eigh(cov)
    vals = np.clip(vals, 0.0, None)
    # 2 sqrt(lambda) is the half-axis of the equivalent uniform ellipse; twice
    # that spans it. Add one pixel so a single-pixel blob is one pixel long.
    length = (4.0 * np.sqrt(vals[1]) + 1.0) * res_m
    width = (4.0 * np.sqrt(vals[0]) + 1.0) * res_m
    angle = float(np.degrees(np.arctan2(vecs[1, 1], vecs[0, 1])))
    return float(length), float(width), angle


def detect(image: np.ndarray, land: np.ndarray, transform, res_m: float,
           k_sigma: float = K_SIGMA, coast_buffer_m: float = COAST_BUFFER_M
           ) -> tuple[list[dict], dict]:
    """Every compact bright object over water, with what it was measured on.

    Candidates are returned whether or not they pass the shape test, with
    `is_vessel` and `reject_reason` saying which. Throwing the rejects away
    would make a later, looser detector impossible to run from the table.
    """
    valid = image > 0
    dist_m = ndimage.distance_transform_edt(~land, sampling=(res_m, res_m))
    water = valid & (dist_m > coast_buffer_m)
    if not water.any():
        return [], {"scored_water_km2": 0.0, "sea_median_dn": float("nan"),
                    "n_candidates": 0, "n_vessels": 0}

    img = image.astype(np.float32)
    bg, sd = background(img, water)
    lab, _ = ndimage.label((img > bg + k_sigma * sd) & water)

    rows = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        piece = lab[sl] == i
        area = int(piece.sum())
        length, width, angle = shape_of(piece, res_m)
        cy = float(sl[0].start + (sl[0].stop - sl[0].start) / 2)
        cx = float(sl[1].start + (sl[1].stop - sl[1].start) / 2)
        lon, lat = transform * (cx, cy)
        values = img[sl][piece]
        row_bg = float(bg[sl][piece].mean())
        row_sd = float(sd[sl][piece].mean())
        reason = ""
        if area < MIN_AREA_PX:
            reason = "too_small"
        elif length > MAX_LENGTH_M:
            reason = "too_long"
        rows.append({
            "latitude": lat, "longitude": lon,
            "grid_row": int(round(cy)), "grid_col": int(round(cx)),
            "dist_to_land_km": float(dist_m[int(cy), int(cx)] / 1000.0),
            "area_px": area, "length_m": length, "width_m": width,
            "peak_dn": float(values.max()), "mean_dn": float(values.mean()),
            "bg_median_dn": row_bg, "bg_mad_dn": row_sd / 1.4826,
            "snr": float((values.max() - row_bg) / row_sd),
            "is_vessel": reason == "", "reject_reason": reason,
        })

    stats = {
        "scored_water_km2": float(water.sum()) * res_m * res_m / 1e6,
        "sea_median_dn": float(np.median(img[water])),
        "n_candidates": len(rows),
        "n_vessels": sum(r["is_vessel"] for r in rows),
    }
    logger.info("%d candidates, %d vessels, %.0f km2 of scored water",
                stats["n_candidates"], stats["n_vessels"], stats["scored_water_km2"])
    return rows, stats
