"""Generate the cropped land mask GeoJSON from Natural Earth 10m data.

Downloads Natural Earth 10m land polygons, crops to the Hormuz region,
filters out tiny islands, simplifies for performance, and saves as GeoJSON.

Usage:
    python scripts/generate_land_mask.py

Requirements:
    pip install shapely requests
"""

import json
import tempfile
from pathlib import Path
from urllib.request import urlretrieve

from shapely.geometry import Point, box, mapping, shape
from shapely.ops import unary_union

NE_10M_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_10m_land.geojson"
)

# Crop region: wider than the collector BBOX for full coverage
CROP_BBOX = box(47.0, 21.0, 61.0, 31.5)

# Minimum polygon area to keep (filters out tiny rock islets)
MIN_AREA = 0.00005  # ~500m x 1km in degrees

# Simplification tolerance (~100m)
SIMPLIFY_TOLERANCE = 0.001

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "land_mask.geojson"

# Known test points for validation
TEST_POINTS = [
    ("Strait of Hormuz", 56.5, 26.5, False),
    ("Persian Gulf center", 55.0, 26.0, False),
    ("Dubai city", 55.30, 25.20, True),
    ("Iran inland", 56.0, 27.4, True),
    ("UAE desert", 55.5, 24.5, True),
    ("Hormuz Island", 56.46, 27.06, True),
]


def main():
    print("Downloading Natural Earth 10m land data...")
    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
        urlretrieve(NE_10M_URL, tmp.name)
        tmp_path = tmp.name

    print("Loading and cropping to Hormuz region...")
    with open(tmp_path) as f:
        data = json.load(f)

    cropped_geoms = []
    for feature in data["features"]:
        geom = shape(feature["geometry"])
        if geom.intersects(CROP_BBOX):
            cropped = geom.intersection(CROP_BBOX)
            if not cropped.is_empty:
                cropped_geoms.append(cropped)

    merged = unary_union(cropped_geoms)

    # Filter tiny islands
    if merged.geom_type == "MultiPolygon":
        significant = [g for g in merged.geoms if g.area >= MIN_AREA]
        dropped = len(merged.geoms) - len(significant)
        print(f"Keeping {len(significant)} polygons, dropping {dropped} tiny islands")
        merged = unary_union(significant)

    # Simplify for performance
    simplified = merged.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)

    n_polys = len(simplified.geoms) if simplified.geom_type == "MultiPolygon" else 1
    total_coords = (
        sum(len(g.exterior.coords) for g in simplified.geoms)
        if simplified.geom_type == "MultiPolygon"
        else len(simplified.exterior.coords)
    )
    print(f"Result: {n_polys} polygons, {total_coords} total coordinates")

    # Validate
    print("\nValidation:")
    all_ok = True
    for name, lon, lat, expected in TEST_POINTS:
        on_land = simplified.contains(Point(lon, lat))
        ok = on_land == expected
        if not ok:
            all_ok = False
        print(f"  {'PASS' if ok else 'FAIL'} | {name}: on_land={on_land} (expected={expected})")

    if not all_ok:
        print("\nWARNING: Some validation tests failed!")

    # Save
    output = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "land", "source": "Natural Earth 10m"},
                "geometry": mapping(simplified),
            }
        ],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f)

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\nSaved: {OUTPUT_PATH} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
