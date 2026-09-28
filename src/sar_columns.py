"""The two SAR tables, their column order, and the AOI they are defined on.

This has no dependencies on purpose, for the same reason `columns.py` has
none: the job that merges parquet should not have to install a raster stack
to know what the columns are called.

Two tables, not one. `SCENE_COLUMNS` records that a scene was looked at at
all, so "no vessels were found" and "this scene was never processed" are
different rows rather than the same absence. The AIS side learned that the
hard way: transit_events was structurally empty and the published text said
nothing about it.
"""

# The grid every detection is reported on. Fixed, because the pixel indices
# stored with each detection are only an anchor while the grid does not move.
# Changing any of these is a new AOI_VERSION and a new detector run.
AOI_WEST, AOI_SOUTH, AOI_EAST, AOI_NORTH = 55.6, 25.5, 57.4, 27.3
AOI_RES_DEG = 0.0002          # ~20 m, two native pixels (10 m) averaged
AOI_VERSION = "v1"

# Bumped whenever the detector's output could change for the same input.
# It is part of the path in the dataset, so a new version does not overwrite
# the old one and the two can be compared.
DETECTOR_VERSION = "v1"

# What the map draws by default; the other bands are there but switched off.
# Measured over the 17 scenes on the Hub on 2026-09-28 (src/sar_survey.py,
# docs/PIPELINE.md "What the map shows"):
# - per 100 km^2 of scored water, vessel-sized detections run 4.1 beyond 10 km,
#   5.0 at 3-10 km, 12.0 at 1-3 km and 94.5 at 0.2-1 km. From 3 km out the
#   rate is the open-sea rate.
# - beyond 3 km, 89% of those above SNR 20 also stand out in VH (control 0.6%);
#   at or below 20, 6%.
# docs/map.js keeps its own copies, held equal by tests/test_site.py.
MAP_MIN_SHORE_KM = 3.0
MAP_MIN_SNR = 20.0

# Source and licence of the committed water mask, carried into the dataset
# card so the attribution travels with the data.
MASK_SOURCE = "ESA WorldCover 10m v200 (2021), CC BY 4.0"

DETECTION_COLUMNS = [
    # what was looked at
    "scene_id", "acq_time", "platform", "orbit_state", "polarization",
    # where the target is
    "latitude", "longitude", "grid_row", "grid_col", "dist_to_land_km",
    # what was measured there, so a later detector can re-decide without
    # fetching the 700 MB scene again
    "area_px", "length_m", "width_m", "orientation_deg",
    "peak_dn", "mean_dn", "bg_median_dn", "bg_mad_dn", "snr",
    # how it was decided. `vessel_sized` is exactly what it says: the object
    # is the size and shape of a vessel. Whether it is one is not measured
    # here — recall was checked against AIS, precision was not, and near the
    # shore real hulls and displaced rock are mixed.
    "vessel_sized", "reject_reason", "detector_version", "aoi_version",
]

SCENE_COLUMNS = [
    "scene_id", "acq_time", "platform", "orbit_state", "polarization",
    "aoi_covered_frac", "scored_water_km2", "sea_median_dn",
    "n_candidates", "n_vessel_sized", "detector_version", "aoi_version",
    "mask_source", "processed_at", "runtime_s", "status",
]
