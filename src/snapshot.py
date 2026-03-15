"""Generate a static map snapshot of vessel positions from SQLite data.

Produces a dark-themed PNG image with vessel positions color-coded by type,
plus a text stats summary. Designed to run inside the Docker container.
"""

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from land_filter import is_on_land  # noqa: E402

DB_PATH = "/app/data/ais.db"
OUTPUT_DIR = Path("/app/data")

# Resolve land_mask.geojson relative to this file
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEOJSON_PATH = _DATA_DIR / "land_mask.geojson"

# Match the web UI color scheme exactly
TYPE_COLORS = {
    "Tanker": "#e65100",
    "Cargo": "#1565c0",
    "Passenger": "#2e7d32",
    "Fishing/Towing/Dredging": "#6a1b9a",
    "Military/Sailing/Pleasure": "#b71c1c",
    "HSC": "#00838f",
    "Other": "#455a64",
    "Unknown": "#616161",
}

# Marker shapes per vessel type for better visual distinction
TYPE_MARKERS = {
    "Tanker": "D",        # diamond
    "Cargo": "s",         # square
    "Passenger": "^",     # triangle up
    "Fishing/Towing/Dredging": "v",  # triangle down
    "Military/Sailing/Pleasure": "P",  # plus (filled)
    "HSC": ">",           # triangle right
    "Other": "h",         # hexagon
    "Unknown": "o",       # circle
}

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

# Monitoring BBOX (matches the collector's WebSocket subscription)
MONITOR_BBOX = {
    "lat_min": 22.0, "lat_max": 30.5,
    "lon_min": 48.0, "lon_max": 60.0,
}

# Key geographic labels for the Strait of Hormuz region
GEO_LABELS = [
    # (lat, lon, label, fontsize, color)
    (26.25, 56.25, "Strait of\nHormuz", 14, "#4fc3f7"),
    (28.50, 53.00, "IRAN", 16, "#cccccc"),
    (23.80, 54.60, "UAE", 16, "#cccccc"),
    (24.00, 57.80, "OMAN", 16, "#cccccc"),
    (29.50, 48.50, "KUWAIT", 10, "#999999"),
    (25.35, 54.80, "QATAR", 10, "#999999"),
    (26.05, 50.55, "BAHRAIN", 9, "#999999"),
    (25.20, 55.30, "Dubai", 10, "#888888"),
    (24.45, 54.65, "Abu Dhabi", 10, "#888888"),
    (27.20, 56.60, "Bandar Abbas", 9, "#888888"),
    (23.60, 58.55, "Muscat", 10, "#888888"),
    (24.80, 49.50, "Al-Jubail", 8, "#777777"),
    (26.25, 50.20, "Dammam", 8, "#777777"),
]


def _load_coastline_polygons() -> list:
    """Load coastline polygons from land_mask.geojson for rendering."""
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
        print(f"Warning: Could not load coastlines from {_GEOJSON_PATH}: {e}")
        return []


def get_ship_type_label(type_code: int | None) -> str:
    """Convert AIS ship type code to human-readable label."""
    if type_code is None:
        return "Unknown"
    for r, label in SHIP_TYPE_RANGES.items():
        if type_code in r:
            return label
    return "Unknown"


def get_color(ship_type: str) -> str:
    """Get color for a ship type, matching the web UI."""
    for key, color in TYPE_COLORS.items():
        if ship_type.startswith(key.split("/")[0]):
            return color
    return TYPE_COLORS["Unknown"]


def get_marker(ship_type: str) -> str:
    """Get marker shape for a ship type."""
    for key, marker in TYPE_MARKERS.items():
        if ship_type.startswith(key.split("/")[0]):
            return marker
    return TYPE_MARKERS["Unknown"]


def query_latest_positions(db_path: str) -> list[dict]:
    """Query SQLite for the latest position of each vessel (last 30 min)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT mmsi, latitude, longitude, speed, course,
                   ship_name, ship_type, destination, flag
            FROM positions
            WHERE id IN (
                SELECT MAX(id) FROM positions
                WHERE received_at > datetime('now', '-30 minutes')
                GROUP BY mmsi
            )
        """).fetchall()
        vessels = []
        for r in rows:
            if is_on_land(r["latitude"], r["longitude"]):
                continue
            type_label = get_ship_type_label(r["ship_type"])
            vessels.append({
                "mmsi": r["mmsi"],
                "lat": r["latitude"],
                "lon": r["longitude"],
                "speed": r["speed"],
                "course": r["course"],
                "name": r["ship_name"] or f"MMSI:{r['mmsi']}",
                "type": type_label,
                "destination": r["destination"],
                "flag": r["flag"],
            })
        return vessels
    finally:
        conn.close()


def query_stats(db_path: str) -> dict:
    """Query basic statistics from the database."""
    conn = sqlite3.connect(db_path)
    try:
        total_records = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        unique_vessels_24h = conn.execute(
            "SELECT COUNT(DISTINCT mmsi) FROM positions "
            "WHERE received_at > datetime('now', '-24 hours')"
        ).fetchone()[0]
        return {
            "total_records": total_records,
            "unique_vessels_24h": unique_vessels_24h,
        }
    finally:
        conn.close()


def generate_snapshot(db_path: str = DB_PATH, output_dir: Path = OUTPUT_DIR) -> Path:
    """Generate a dark-themed map image of current vessel positions.

    Returns the path to the generated PNG file.
    """
    vessels = query_latest_positions(db_path)
    stats = query_stats(db_path)
    now_utc = datetime.now(timezone.utc)

    # --- Figure setup ---
    fig, ax = plt.subplots(figsize=(16, 10), facecolor="#0a0a1a")
    ax.set_facecolor("#0d1b2a")

    # Display area (slightly wider than monitoring BBOX for context)
    lon_min, lon_max = 46.5, 61.0
    lat_min, lat_max = 21.0, 31.5
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal")

    # --- Subtle grid lines ---
    for lon in range(48, 61):
        ax.axvline(lon, color="#141e2e", linewidth=0.3, zorder=1)
    for lat in range(22, 32):
        ax.axhline(lat, color="#141e2e", linewidth=0.3, zorder=1)

    # --- Coastline from land_mask.geojson ---
    coastline_polys = _load_coastline_polygons()
    for poly in coastline_polys:
        exterior = poly.exterior
        xs, ys = exterior.xy
        ax.fill(xs, ys, facecolor="#111822", edgecolor="#2a3a4a",
                linewidth=0.8, zorder=2)

    # --- Monitoring area BBOX (dashed rectangle) ---
    bbox = MONITOR_BBOX
    bbox_rect = mpatches.FancyBboxPatch(
        (bbox["lon_min"], bbox["lat_min"]),
        bbox["lon_max"] - bbox["lon_min"],
        bbox["lat_max"] - bbox["lat_min"],
        boxstyle="round,pad=0",
        fill=False, edgecolor="#4fc3f7", linewidth=1.5,
        linestyle=(0, (8, 4)),  # dashed
        zorder=4,
    )
    ax.add_patch(bbox_rect)
    # Label for the monitoring area
    ax.text(
        bbox["lon_min"] + 0.15, bbox["lat_max"] - 0.3,
        "MONITORING AREA", fontsize=8, color="#4fc3f7", alpha=0.7,
        fontweight="bold", zorder=4,
    )

    # --- Geographic labels ---
    text_effects = [pe.withStroke(linewidth=3, foreground="#0a0a1a")]
    for lat, lon, label, size, color in GEO_LABELS:
        if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
            ax.text(
                lon, lat, label,
                fontsize=size, color=color, fontweight="bold",
                ha="center", va="center", zorder=3,
                path_effects=text_effects,
            )

    # --- Vessel markers ---
    type_counter: Counter = Counter()
    if vessels:
        by_type: dict[str, list] = {}
        for v in vessels:
            by_type.setdefault(v["type"], []).append(v)

        for ship_type, group in sorted(by_type.items()):
            color = get_color(ship_type)
            marker = get_marker(ship_type)
            lats = [v["lat"] for v in group]
            lons = [v["lon"] for v in group]
            size = 60 if "Tanker" in ship_type else 45
            ax.scatter(
                lons, lats,
                s=size, c=color, marker=marker,
                edgecolors="white", linewidths=0.6,
                alpha=0.9, zorder=5, label=f"{ship_type} ({len(group)})",
            )
            type_counter[ship_type] = len(group)

    # --- Legend ---
    legend = ax.legend(
        loc="lower right", fontsize=12,
        facecolor="#0d1b2aee", edgecolor="#2a3a4a", labelcolor="#cccccc",
        framealpha=0.95, borderpad=1.0, handletextpad=0.8,
        title="Vessel Types", title_fontsize=13,
    )
    if legend.get_title():
        legend.get_title().set_color("#4fc3f7")

    # --- Title (top-left overlay, not matplotlib title) ---
    total_str = f"{stats['total_records']:,}"
    fig.text(
        0.02, 0.97,
        "Strait of Hormuz  Live Ship Tracker",
        fontsize=18, fontweight="bold", color="#e0e0e0",
        va="top", fontfamily="sans-serif",
        path_effects=[pe.withStroke(linewidth=3, foreground="#0a0a1a")],
    )

    # Stats badges (top-right)
    fig.text(
        0.88, 0.97,
        f"{len(vessels)}", fontsize=22, fontweight="bold",
        color="#4fc3f7", va="top", ha="center",
        path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")],
    )
    fig.text(
        0.88, 0.935,
        "ACTIVE\nVESSELS", fontsize=7, color="#888888",
        va="top", ha="center", linespacing=1.2,
    )
    fig.text(
        0.96, 0.97,
        total_str, fontsize=22, fontweight="bold",
        color="#4fc3f7", va="top", ha="center",
        path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")],
    )
    fig.text(
        0.96, 0.935,
        "TOTAL\nRECORDS", fontsize=7, color="#888888",
        va="top", ha="center", linespacing=1.2,
    )

    # Timestamp (below title)
    fig.text(
        0.02, 0.935,
        f"{now_utc.strftime('%Y-%m-%d %H:%M UTC')}  |  "
        f"24h unique: {stats['unique_vessels_24h']}",
        fontsize=10, color="#888888", va="top",
    )

    # --- Axis formatting (degree notation, no labels) ---
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}\u00b0E"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}\u00b0N"))
    ax.tick_params(colors="#555555", labelsize=10, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # --- Attribution ---
    fig.text(
        0.99, 0.01,
        "Data: aisstream.io  |  github.com/yasumorishima/hormuz-ship-tracker",
        fontsize=8, color="#444444", ha="right", va="bottom",
    )

    # --- Save ---
    output_path = output_dir / "snapshot.png"
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    print(f"Snapshot saved: {output_path}")
    print(f"  Active vessels: {len(vessels)}")
    for t, c in type_counter.most_common():
        print(f"    {t}: {c}")
    print(f"  Total records: {stats['total_records']:,}")
    print(f"  Timestamp: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    return output_path


def generate_stats_summary(db_path: str = DB_PATH, output_dir: Path = OUTPUT_DIR) -> Path:
    """Generate a text-based stats summary file."""
    vessels = query_latest_positions(db_path)
    stats = query_stats(db_path)
    now_utc = datetime.now(timezone.utc)
    type_counter: Counter = Counter()
    for v in vessels:
        type_counter[v["type"]] += 1

    lines = [
        "# Hormuz Ship Tracker — Snapshot Stats",
        f"Generated: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"Active vessels (30 min): {len(vessels)}",
        f"Unique vessels (24h): {stats['unique_vessels_24h']}",
        f"Total position records: {stats['total_records']:,}",
        "",
        "## By vessel type",
    ]
    for t, c in type_counter.most_common():
        lines.append(f"  {t}: {c}")

    if vessels:
        lines.append("")
        lines.append("## Top vessels by name")
        named = [v for v in vessels if v["name"] and not v["name"].startswith("MMSI:")]
        for v in named[:15]:
            speed_str = f"{v['speed']:.1f} kn" if v["speed"] else "--"
            lines.append(
                f"  {v['name']} [{v['type']}] → {v['destination'] or '--'} | {speed_str}"
            )

    output_path = output_dir / "snapshot_stats.txt"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Stats saved: {output_path}")
    return output_path


if __name__ == "__main__":
    # Allow overriding paths for local testing
    db = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT_DIR
    generate_snapshot(db, out)
    generate_stats_summary(db, out)
