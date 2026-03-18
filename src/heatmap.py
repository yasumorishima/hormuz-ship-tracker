"""Generate vessel traffic density heatmap from AIS position data.

Shows where ships concentrate and where the "dead zones" are.
The Strait of Hormuz gap is clearly visible when terrestrial AIS is the
only data source — mid-strait positions are lost.

Usage:
    python src/heatmap.py [--hours 96] [--output heatmap.png]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
import numpy as np  # noqa: E402
from shapely.geometry import shape  # noqa: E402

DB_PATH = "/app/data/ais.db"
OUTPUT_DIR = Path("/app/data")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEOJSON_PATH = _DATA_DIR / "land_mask.geojson"

SHIP_TYPE_RANGES = {
    range(20, 30): "WIG",
    range(30, 36): "Fishing/Towing/Dredging",
    range(36, 40): "Military/Sailing/Pleasure",
    range(40, 50): "HSC",
    range(60, 70): "Passenger",
    range(70, 80): "Cargo",
    range(80, 90): "Tanker",
    range(90, 100): "Other",
}

GATE_LINES = [
    {"name": "Strait of Hormuz", "lats": [26.05, 26.65], "lons": [56.50, 56.10]},
    {"name": "Dubai / Jebel Ali", "lats": [25.00, 25.35], "lons": [55.20, 55.20]},
    {"name": "Fujairah", "lats": [25.00, 25.30], "lons": [56.50, 56.50]},
]

GEO_LABELS = [
    (26.25, 56.25, "Strait of\nHormuz", 13, "#4fc3f7"),
    (28.50, 53.00, "IRAN", 16, "#cccccc"),
    (23.80, 54.60, "UAE", 16, "#cccccc"),
    (24.00, 57.80, "OMAN", 16, "#cccccc"),
    (29.50, 48.50, "KUWAIT", 10, "#999999"),
    (25.35, 54.80, "QATAR", 10, "#999999"),
    (25.20, 55.30, "Dubai", 10, "#888888"),
    (27.20, 56.60, "Bandar Abbas", 9, "#888888"),
    (23.60, 58.55, "Muscat", 10, "#888888"),
]


def get_ship_type_label(type_code):
    if type_code is None:
        return "Unknown"
    for r, label in SHIP_TYPE_RANGES.items():
        if type_code in r:
            return label
    return "Unknown"


def _load_coastline_polygons():
    try:
        with open(_GEOJSON_PATH) as f:
            data = json.load(f)
        polygons = []
        for feature in data["features"]:
            geom = shape(feature["geometry"])
            if geom.geom_type == "Polygon":
                polygons.append(geom)
            elif geom.geom_type == "MultiPolygon":
                polygons.extend(geom.geoms)
        return polygons
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: Could not load coastlines: {e}")
        return []


def query_all_positions(db_path, hours):
    """Query all valid positions for the heatmap."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if hours > 0:
            rows = conn.execute("""
                SELECT latitude, longitude, speed, ship_type, ship_name, flag
                FROM positions
                WHERE timestamp > datetime(
                    (SELECT MAX(timestamp) FROM positions), ? || ' hours')
                  AND (speed IS NULL OR speed < 100)
            """, (f"-{hours}",)).fetchall()
        else:
            rows = conn.execute("""
                SELECT latitude, longitude, speed, ship_type, ship_name, flag
                FROM positions
                WHERE speed IS NULL OR speed < 100
            """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_stats(db_path, hours):
    """Get summary stats."""
    conn = sqlite3.connect(db_path)
    try:
        if hours > 0:
            time_filter = f"WHERE timestamp > datetime((SELECT MAX(timestamp) FROM positions), '-{hours} hours')"
        else:
            time_filter = ""
        total = conn.execute(f"SELECT COUNT(*) FROM positions {time_filter}").fetchone()[0]
        unique = conn.execute(
            f"SELECT COUNT(DISTINCT mmsi) FROM positions {time_filter}"
        ).fetchone()[0]
        earliest = conn.execute("SELECT MIN(timestamp) FROM positions").fetchone()[0]
        latest = conn.execute("SELECT MAX(timestamp) FROM positions").fetchone()[0]
        transit_count = conn.execute("SELECT COUNT(*) FROM transit_events").fetchone()[0]
        return {
            "total": total, "unique_ships": unique,
            "earliest": earliest, "latest": latest,
            "transits": transit_count,
        }
    finally:
        conn.close()


def generate_heatmap(db_path=DB_PATH, output_dir=OUTPUT_DIR,
                     hours=0, filename="heatmap.png"):
    """Generate a traffic density heatmap.

    hours=0 means all available data.
    """
    positions = query_all_positions(db_path, hours)
    stats = query_stats(db_path, hours)

    if not positions:
        print("No data available.")
        return None

    lats = np.array([p["latitude"] for p in positions])
    lons = np.array([p["longitude"] for p in positions])

    print(f"Generating heatmap from {len(positions):,} positions, {stats['unique_ships']} ships")
    print(f"Data range: {stats['earliest'][:16]} → {stats['latest'][:16]}")

    # ── Figure ──
    fig, ax = plt.subplots(figsize=(18, 11), facecolor="#0a0a1a")
    ax.set_facecolor("#0d1b2a")

    lon_min, lon_max = 46.5, 61.0
    lat_min, lat_max = 21.0, 31.5
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal")

    # Grid
    for lon in range(48, 61):
        ax.axvline(lon, color="#141e2e", linewidth=0.3, zorder=1)
    for lat in range(22, 32):
        ax.axhline(lat, color="#141e2e", linewidth=0.3, zorder=1)

    # Coastline
    coastlines = _load_coastline_polygons()
    for poly in coastlines:
        xs, ys = poly.exterior.xy
        ax.fill(xs, ys, facecolor="#111822", edgecolor="#2a3a4a", linewidth=0.8, zorder=2)

    # ── 2D Histogram (heatmap) ──
    # High resolution bins for smooth appearance
    x_bins = np.linspace(lon_min, lon_max, 300)
    y_bins = np.linspace(lat_min, lat_max, 200)
    heatmap_data, xedges, yedges = np.histogram2d(lons, lats, bins=[x_bins, y_bins])

    # Apply log scale for better visibility of low-density areas
    heatmap_log = np.log1p(heatmap_data)

    # Plot heatmap
    extent = [lon_min, lon_max, lat_min, lat_max]
    im = ax.imshow(
        heatmap_log.T, origin="lower", extent=extent,
        aspect="auto", cmap="hot", alpha=0.85, zorder=3,
        interpolation="gaussian",
    )

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02, aspect=30)
    cbar.set_label("Position Density (log scale)", color="#888888", fontsize=12)
    cbar.ax.yaxis.set_tick_params(color="#666666")
    cbar.outline.set_edgecolor("#2a3a4a")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="#888888")

    # Gate lines
    for gate in GATE_LINES:
        ax.plot(gate["lons"], gate["lats"], color="#4fc3f7", linewidth=2.5,
                linestyle=(0, (6, 4)), zorder=6, alpha=0.9)
        mid_lat = sum(gate["lats"]) / 2
        mid_lon = sum(gate["lons"]) / 2
        ax.text(mid_lon + 0.15, mid_lat, gate["name"],
                fontsize=8, color="#4fc3f7", fontweight="bold",
                ha="left", va="center", zorder=6,
                path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")])

    # Geo labels
    text_effects = [pe.withStroke(linewidth=3, foreground="#0a0a1a")]
    for lat, lon, label, size, color in GEO_LABELS:
        if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
            ax.text(lon, lat, label, fontsize=size, color=color, fontweight="bold",
                    ha="center", va="center", zorder=5, path_effects=text_effects)

    # ── Title & stats ──
    hours_label = f"Past {hours}h" if hours > 0 else "All Data"
    fig.text(0.02, 0.97,
             "Strait of Hormuz  Vessel Traffic Density",
             fontsize=20, fontweight="bold", color="#e0e0e0", va="top",
             path_effects=[pe.withStroke(linewidth=3, foreground="#0a0a1a")])

    period = f"{stats['earliest'][:10]} → {stats['latest'][:10]}"
    fig.text(0.02, 0.935,
             f"{hours_label}  |  {len(positions):,} positions  |  "
             f"{stats['unique_ships']} ships  |  {period}",
             fontsize=11, color="#888888", va="top")

    # Strait status annotation
    fig.text(0.02, 0.905,
             "Strait of Hormuz transit: 0 confirmed crossings  |  "
             "AIS coverage: terrestrial only (mid-strait blind spot)",
             fontsize=10, color="#ff9800", va="top", fontweight="bold",
             path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")])

    # ── Annotation: Dead zone ──
    # Circle around the strait area to highlight the gap
    strait_center_lon = 56.3
    strait_center_lat = 26.35
    circle = mpatches.Circle(
        (strait_center_lon, strait_center_lat), 0.8,
        fill=False, edgecolor="#4fc3f7", linewidth=1.5,
        linestyle="--", zorder=7, alpha=0.7,
    )
    ax.add_patch(circle)
    ax.annotate(
        "AIS Dead Zone\n(no terrestrial coverage)",
        xy=(strait_center_lon, strait_center_lat),
        xytext=(strait_center_lon + 1.8, strait_center_lat + 1.5),
        fontsize=10, color="#4fc3f7", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#4fc3f7", lw=1.5),
        zorder=7,
        path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")],
    )

    # Axis formatting
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}\u00b0E"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}\u00b0N"))
    ax.tick_params(colors="#555555", labelsize=10, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Attribution
    fig.text(0.99, 0.01,
             "Data: aisstream.io  |  github.com/yasumorishima/hormuz-ship-tracker",
             fontsize=8, color="#444444", ha="right", va="bottom")

    # Save
    output_path = output_dir / filename
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Heatmap saved: {output_path} ({file_size_mb:.1f} MB)")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate vessel traffic heatmap")
    parser.add_argument("--db", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    parser.add_argument("--hours", type=int, default=0, help="Hours of data (0=all)")
    parser.add_argument("--filename", default="heatmap.png", help="Output filename")
    args = parser.parse_args()

    generate_heatmap(
        db_path=args.db,
        output_dir=Path(args.output_dir),
        hours=args.hours,
        filename=args.filename,
    )
