"""Finding vessels in one Sentinel-1 amplitude image.

The physics is kind here: at C band a calm sea reflects most of the energy
away from the satellite and a steel hull reflects a lot of it back, so a ship
is a bright compact object on a dark field. The work is in not calling the
land a ship.

Three things decide that, in order of how much they matter (measured on
S1C 2026-09-16T02:06Z over the strait, with the same threshold throughout):

  * the coastline. Ten kilometres from land the two available outlines agree,
    because out there neither is in the picture. Within a kilometre of the
    shore, Natural Earth 10m leaves 163 vessel-sized objects per 100 km²
    standing on the ridges of the Musandam fjords where ESA WorldCover 10m
    leaves 36. It is not a tuning parameter.
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
# Calibrated against AIS: 8 and 10 both find 24 of 24, 12 loses one, and 10
# leaves a sixth fewer candidates than 8. See docs/PIPELINE.md.
K_SIGMA = 10.0
MIN_AREA_PX = 3                # about 1,330 m^2 on this grid
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
            # Against the block's own area, not a full one: the last row and
            # column of blocks are narrower, and measuring them against 256 x
            # 256 would send every edge block off to borrow a neighbour's.
            if cell.size < MIN_BLOCK_FILL * (ys.stop - ys.start) * (xs.stop - xs.start):
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
    """Per-pixel background level and median absolute deviation.

    The MAD is returned as measured. The floor that keeps the threshold
    finite belongs where the threshold is formed, not here: a stored
    `bg_mad_dn` that had silently been raised to the floor would not be the
    measurement the table promises.
    """
    med, mad = _block_stat(image, valid)
    return _fill_and_zoom(med, image.shape), _fill_and_zoom(mad, image.shape)


def spread(mad: np.ndarray) -> np.ndarray:
    """The MAD on the footing of a standard deviation, floored away from zero.

    1.4826 is the normal-distribution conversion. Sea clutter is not normal,
    so K_SIGMA is calibrated against AIS rather than read off a table; the
    scaling only keeps the number in a familiar range.
    """
    return np.maximum(mad * 1.4826, 1.0)


def shape_of(mask_piece: np.ndarray, pixel_m: tuple[float, float]
             ) -> tuple[float, float, float]:
    """Length, width and orientation of one blob, from its second moments.

    A bounding box would call a 300 m ship lying diagonally a 420 m one, and
    length is the field most likely to be compared against something else, so
    it is worth the moments.

    The inversion is the one for a rectangle, not an ellipse. A run of n
    pixels has variance (n^2 - 1) / 12, so n = sqrt(12 v + 1); the ellipse
    form (4 sqrt(v)) reports a hull 18-22% longer than it is, measured on
    known rectangles from 300 m to 900 m. Ships are closer to rectangles than
    to ellipses, and 20% is more than the difference between ship classes.

    The moments are taken in pixels and converted afterwards along the axis
    that was found, because the grid's cells are not square: a 300 m hull lying
    east-west covers a different number of pixels than the same hull lying
    north-south.
    """
    row_m, col_m = pixel_m
    ys, xs = np.nonzero(mask_piece)
    if ys.size < 2:
        return max(row_m, col_m), min(row_m, col_m), 0.0
    coords = np.stack([ys - ys.mean(), xs - xs.mean()]).astype(np.float64)
    cov = coords @ coords.T / coords.shape[1]
    vals, vecs = np.linalg.eigh(cov)
    vals = np.clip(vals, 0.0, None)

    def extent(variance, vector):
        pixels = np.sqrt(12.0 * variance + 1.0)
        metres_per_pixel = np.hypot(vector[0] * row_m, vector[1] * col_m)
        return float(pixels * metres_per_pixel)

    length = extent(vals[1], vecs[:, 1])
    width = extent(vals[0], vecs[:, 0])
    angle = float(np.degrees(np.arctan2(vecs[1, 1], vecs[0, 1])))
    return length, width, angle


def detect(image: np.ndarray, land: np.ndarray, transform,
           pixel_m: tuple[float, float], k_sigma: float = K_SIGMA,
           coast_buffer_m: float = COAST_BUFFER_M) -> tuple[list[dict], dict]:
    """Every compact bright object over water, with what it was measured on.

    `pixel_m` is (north-south, east-west) in metres; see
    `sar_scene.pixel_metres`. Candidates are returned whether or not they pass
    the shape test, with `is_vessel` and `reject_reason` saying which.
    Throwing the rejects away would make a later, looser detector impossible
    to run from the table.
    """
    row_m, col_m = pixel_m
    cell_km2 = row_m * col_m / 1e6
    valid = image > 0
    if land.any():
        dist_m = ndimage.distance_transform_edt(~land, sampling=(row_m, col_m))
    else:
        # distance_transform_edt on an all-True input does not return infinity,
        # it returns the distance to the corner of the array. Nothing would
        # look wrong; every row would just carry a made-up dist_to_land_km.
        dist_m = np.full(image.shape, np.inf, dtype=np.float32)
    water = valid & (dist_m > coast_buffer_m)
    if not water.any():
        return [], {"scored_water_km2": 0.0, "sea_median_dn": float("nan"),
                    "n_candidates": 0, "n_vessels": 0}

    img = image.astype(np.float32)
    bg, mad = background(img, water)
    sd = spread(mad)
    lab, _ = ndimage.label((img > bg + k_sigma * sd) & water)

    rows = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        piece = lab[sl] == i
        area = int(piece.sum())
        length, width, angle = shape_of(piece, pixel_m)
        # The centroid of the component, not the middle of its bounding box:
        # a blob can be concave, and this same point is what indexes the
        # distance field, so the two must not be allowed to disagree.
        ys, xs = np.nonzero(piece)
        cy = sl[0].start + float(ys.mean())
        cx = sl[1].start + float(xs.mean())
        row_i, col_i = int(round(cy)), int(round(cx))
        # The transform's origin is a pixel corner, so the centre of the
        # pixel holding the centroid is half a cell further on.
        lon, lat = transform * (cx + 0.5, cy + 0.5)
        values = img[sl][piece]
        row_bg = float(bg[sl][piece].mean())
        row_mad = float(mad[sl][piece].mean())
        row_sd = float(sd[sl][piece].mean())
        reason = ""
        if area < MIN_AREA_PX:
            reason = "too_small"
        elif length > MAX_LENGTH_M:
            reason = "too_long"
        rows.append({
            "latitude": lat, "longitude": lon,
            "grid_row": row_i, "grid_col": col_i,
            "dist_to_land_km": float(dist_m[row_i, col_i] / 1000.0),
            "area_px": area, "length_m": length, "width_m": width,
            "peak_dn": float(values.max()), "mean_dn": float(values.mean()),
            "bg_median_dn": row_bg, "bg_mad_dn": row_mad,
            "orientation_deg": angle,
            "snr": float((values.max() - row_bg) / row_sd),
            "is_vessel": reason == "", "reject_reason": reason,
        })

    stats = {
        "scored_water_km2": float(water.sum()) * cell_km2,
        "sea_median_dn": float(np.median(img[water])),
        "n_candidates": len(rows),
        "n_vessels": sum(r["is_vessel"] for r in rows),
    }
    logger.info("%d candidates, %d vessels, %.0f km2 of scored water",
                stats["n_candidates"], stats["n_vessels"], stats["scored_water_km2"])
    return rows, stats
