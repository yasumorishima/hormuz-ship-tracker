"""Generate a static map snapshot of vessel positions from SQLite data.

Produces a dark-themed PNG image with vessel positions color-coded by type,
plus a text stats summary. Designed to run inside the Docker container.
"""

import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402

from land_filter import is_on_land  # noqa: E402

DB_PATH = "/app/data/ais.db"
OUTPUT_DIR = Path("/app/data")

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

# Key geographic labels for the Strait of Hormuz region
GEO_LABELS = [
    (26.25, 56.25, "Strait of\nHormuz", 12, "#4fc3f7"),
    (27.15, 56.30, "IRAN", 14, "#cccccc"),
    (23.80, 54.60, "UAE", 14, "#cccccc"),
    (24.50, 57.50, "OMAN", 14, "#cccccc"),
    (25.35, 54.80, "QATAR", 9, "#999999"),
    (26.05, 50.55, "BAHRAIN", 8, "#999999"),
    (25.20, 55.30, "Dubai", 9, "#888888"),
    (24.45, 54.65, "Abu Dhabi", 9, "#888888"),
    (27.20, 56.60, "Bandar Abbas", 8, "#888888"),
    (23.60, 58.55, "Muscat", 9, "#888888"),
]

# Approximate coastline polygons (simplified) for the Hormuz region
# These are rough outlines to give geographic context on the dark background
COASTLINE_SEGMENTS = [
    # Iran southern coast (west to east)
    [
        (27.50, 54.00), (27.20, 54.50), (26.90, 54.80), (26.70, 55.10),
        (26.60, 55.40), (26.55, 55.70), (26.50, 55.90), (26.60, 56.10),
        (26.70, 56.20), (26.90, 56.30), (27.10, 56.30), (27.20, 56.50),
        (27.15, 56.80), (26.95, 57.05), (26.80, 57.20), (26.70, 57.40),
        (26.55, 57.60), (26.30, 57.80), (26.10, 58.00), (25.90, 58.30),
        (25.80, 58.50),
    ],
    # UAE coast (west to east, northern coast)
    [
        (24.00, 54.00), (24.30, 54.30), (24.50, 54.50), (24.80, 54.70),
        (25.00, 55.00), (25.10, 55.20), (25.25, 55.30), (25.35, 55.40),
        (25.40, 55.50), (25.60, 55.80), (25.70, 56.00), (25.80, 56.10),
        (25.95, 56.20), (26.10, 56.25), (26.30, 56.35), (26.35, 56.35),
    ],
    # Oman (Musandam peninsula tip + east coast)
    [
        (26.35, 56.35), (26.40, 56.40), (26.38, 56.45), (26.20, 56.50),
        (26.10, 56.40), (25.95, 56.30), (25.80, 56.30), (25.60, 56.35),
        (25.40, 56.40), (25.00, 56.60), (24.70, 56.80), (24.40, 57.00),
        (24.10, 57.20), (23.80, 57.50), (23.60, 57.80), (23.50, 58.00),
        (23.55, 58.30), (23.60, 58.50), (23.65, 58.60),
    ],
    # Qatar peninsula
    [
        (24.70, 50.80), (25.00, 51.00), (25.30, 51.20), (25.60, 51.30),
        (25.90, 51.40), (26.10, 51.50), (26.15, 51.55), (26.10, 51.60),
        (25.80, 51.60), (25.50, 51.55), (25.20, 51.50), (24.90, 51.40),
        (24.70, 51.30), (24.50, 51.20),
    ],
]


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
    fig, ax = plt.subplots(figsize=(14, 9), facecolor="#0a0a1a")
    ax.set_facecolor("#0d1b2a")

    # Bounding box matching the collector's area with some padding
    lon_min, lon_max = 47.5, 60.5
    lat_min, lat_max = 21.5, 31.0
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal")

    # --- Grid lines ---
    for lon in range(50, 59):
        ax.axvline(lon, color="#1a2a3a", linewidth=0.5, zorder=1)
    for lat in range(23, 28):
        ax.axhline(lat, color="#1a2a3a", linewidth=0.5, zorder=1)

    # --- Coastline ---
    for segment in COASTLINE_SEGMENTS:
        lats, lons = zip(*segment)
        ax.plot(lons, lats, color="#2a3a4a", linewidth=1.2, zorder=2)
        # Fill land side with a subtle shade
        ax.fill(lons, lats, color="#111822", alpha=0.6, zorder=1)

    # --- Geographic labels ---
    text_effects = [pe.withStroke(linewidth=2, foreground="#0a0a1a")]
    for lat, lon, label, size, color in GEO_LABELS:
        if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
            ax.text(
                lon, lat, label,
                fontsize=size, color=color, fontweight="bold",
                ha="center", va="center", zorder=3,
                path_effects=text_effects,
            )

    # --- Vessel dots ---
    type_counter: Counter = Counter()
    if vessels:
        # Group by type for efficient plotting and legend
        by_type: dict[str, list] = {}
        for v in vessels:
            by_type.setdefault(v["type"], []).append(v)

        for ship_type, group in sorted(by_type.items()):
            color = get_color(ship_type)
            lats = [v["lat"] for v in group]
            lons = [v["lon"] for v in group]
            size = 30 if "Tanker" in ship_type else 22
            ax.scatter(
                lons, lats,
                s=size, c=color, edgecolors="white", linewidths=0.4,
                alpha=0.85, zorder=5, label=f"{ship_type} ({len(group)})",
            )
            type_counter[ship_type] = len(group)

    # --- Legend ---
    legend = ax.legend(
        loc="lower left", fontsize=10,
        facecolor="#0d1b2a", edgecolor="#2a3a4a", labelcolor="#cccccc",
        framealpha=0.9, borderpad=0.8,
        title="Vessel Types", title_fontsize=11,
    )
    if legend.get_title():
        legend.get_title().set_color("#4fc3f7")

    # --- Title bar ---
    total_str = f"{stats['total_records']:,}"
    title_text = (
        f"Strait of Hormuz — Ship Tracker Snapshot\n"
        f"{now_utc.strftime('%Y-%m-%d %H:%M UTC')}  |  "
        f"Active: {len(vessels)} vessels  |  "
        f"24h unique: {stats['unique_vessels_24h']}  |  "
        f"Total records: {total_str}"
    )
    ax.set_title(
        title_text, fontsize=14, fontweight="bold",
        color="#e0e0e0", pad=12,
        fontfamily="sans-serif",
    )

    # --- Axis labels ---
    ax.set_xlabel("Longitude", fontsize=12, color="#888888", labelpad=8)
    ax.set_ylabel("Latitude", fontsize=12, color="#888888", labelpad=8)
    ax.tick_params(colors="#666666", labelsize=10)
    for spine in ax.spines.values():
        spine.set_color("#2a3a4a")

    # --- Attribution ---
    fig.text(
        0.99, 0.01, "Data: aisstream.io  |  github.com/yasumorishima/hormuz-ship-tracker",
        fontsize=8, color="#555555", ha="right", va="bottom",
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
