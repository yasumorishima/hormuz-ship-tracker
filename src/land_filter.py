"""Land mask filter using Natural Earth 10m data.

Provides a fast point-in-polygon check to determine if a coordinate
falls on land. Used to filter out AIS positions from land-based
sources (GPS drift, AIS repeaters on buildings, etc.).

Data source: Natural Earth 10m land polygons (public domain).
Cropped to the Persian Gulf / Gulf of Oman region and simplified.
"""

import json
import logging
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep

logger = logging.getLogger(__name__)

# Resolve path relative to this file (works both locally and in Docker)
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEOJSON_PATH = _DATA_DIR / "land_mask.geojson"

# Lazy-loaded singleton
_prepared_land = None


def _load_land_geometry():
    """Load and prepare the land geometry for fast lookups."""
    global _prepared_land
    if _prepared_land is not None:
        return _prepared_land

    try:
        with open(_GEOJSON_PATH) as f:
            data = json.load(f)

        geoms = [shape(feature["geometry"]) for feature in data["features"]]
        land = unary_union(geoms)
        _prepared_land = prep(land)
        logger.info(
            "Land mask loaded: %s polygons from %s",
            len(land.geoms) if land.geom_type == "MultiPolygon" else 1,
            _GEOJSON_PATH,
        )
    except FileNotFoundError:
        logger.error("Land mask not found: %s — land filtering disabled", _GEOJSON_PATH)
        _prepared_land = None
    except Exception as e:
        logger.error("Failed to load land mask: %s — land filtering disabled", e)
        _prepared_land = None

    return _prepared_land


def is_on_land(lat: float, lon: float) -> bool:
    """Check if a coordinate falls on land.

    Returns True if the point is on land, False if at sea.
    Returns False if the land mask is unavailable (fail-open for data collection).
    """
    prepared = _load_land_geometry()
    if prepared is None:
        return False
    return prepared.contains(Point(lon, lat))
