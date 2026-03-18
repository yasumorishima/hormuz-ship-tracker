"""Generate timelapse animation of vessel movements from AIS position data.

Produces a GIF animation showing ship positions over time with trails,
color-coded by vessel type. Designed to run inside the Docker container.

Usage:
    python src/timelapse.py [--hours 96] [--interval 30] [--trail 120] [--fps 8] [--output timelapse.gif]
"""

import argparse
import io
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
from PIL import Image  # noqa: E402
from shapely.geometry import shape  # noqa: E402

DB_PATH = "/app/data/ais.db"
OUTPUT_DIR = Path("/app/data")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEOJSON_PATH = _DATA_DIR / "land_mask.geojson"

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

TYPE_MARKERS = {
    "Tanker": "D",
    "Cargo": "s",
    "Passenger": "^",
    "Fishing/Towing/Dredging": "v",
    "Military/Sailing/Pleasure": "P",
    "HSC": ">",
    "Other": "h",
    "Unknown": "o",
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

# Gate line coordinates
GATE_LINES = [
    {"name": "Strait of Hormuz", "lats": [26.05, 26.65], "lons": [56.50, 56.10]},
    {"name": "Dubai / Jebel Ali", "lats": [25.00, 25.35], "lons": [55.20, 55.20]},
    {"name": "Fujairah", "lats": [25.00, 25.30], "lons": [56.50, 56.50]},
]

GEO_LABELS = [
    (26.25, 56.25, "Strait of\nHormuz", 12, "#4fc3f7"),
    (28.50, 53.00, "IRAN", 14, "#cccccc"),
    (23.80, 54.60, "UAE", 14, "#cccccc"),
    (24.00, 57.80, "OMAN", 14, "#cccccc"),
    (29.50, 48.50, "KUWAIT", 9, "#999999"),
    (25.35, 54.80, "QATAR", 9, "#999999"),
    (25.20, 55.30, "Dubai", 9, "#888888"),
    (27.20, 56.60, "Bandar Abbas", 8, "#888888"),
]

MONITOR_BBOX = {"lat_min": 22.0, "lat_max": 30.5, "lon_min": 48.0, "lon_max": 60.0}


def get_ship_type_label(type_code):
    if type_code is None:
        return "Unknown"
    for r, label in SHIP_TYPE_RANGES.items():
        if type_code in r:
            return label
    return "Unknown"


def get_color(ship_type):
    for key, color in TYPE_COLORS.items():
        if ship_type.startswith(key.split("/")[0]):
            return color
    return TYPE_COLORS["Unknown"]


def get_marker(ship_type):
    for key, marker in TYPE_MARKERS.items():
        if ship_type.startswith(key.split("/")[0]):
            return marker
    return TYPE_MARKERS["Unknown"]


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


def query_positions_in_window(db_path, start_ts, end_ts):
    """Query latest position per vessel within a time window."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT mmsi, latitude, longitude, speed, ship_name, ship_type, flag
            FROM positions
            WHERE id IN (
                SELECT MAX(id) FROM positions
                WHERE timestamp >= ? AND timestamp < ?
                GROUP BY mmsi
            )
        """, (start_ts, end_ts)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_trail_positions(db_path, end_ts, trail_minutes):
    """Query all positions within the trail window, grouped by MMSI."""
    start_ts = (datetime.fromisoformat(end_ts) - timedelta(minutes=trail_minutes)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT mmsi, latitude, longitude, ship_type, timestamp
            FROM positions
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY mmsi, timestamp
        """, (start_ts, end_ts)).fetchall()
        trails = defaultdict(list)
        for r in rows:
            trails[r["mmsi"]].append({
                "lat": r["latitude"], "lon": r["longitude"],
                "type": get_ship_type_label(r["ship_type"]),
            })
        return trails
    finally:
        conn.close()


def query_transit_events_in_window(db_path, start_ts, end_ts):
    """Query transit events in a time window."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT gate_name, direction, COUNT(*) as cnt
            FROM transit_events
            WHERE crossed_at >= ? AND crossed_at < ?
            GROUP BY gate_name, direction
        """, (start_ts, end_ts)).fetchall()
        return {f"{r['gate_name']}_{r['direction']}": r['cnt'] for r in rows}
    finally:
        conn.close()


def query_data_range(db_path):
    """Get earliest and latest timestamps."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp), COUNT(*), COUNT(DISTINCT mmsi) FROM positions"
        ).fetchone()
        return row[0], row[1], row[2], row[3]
    finally:
        conn.close()


def render_frame(frame_ts, vessels, trails, transit_counts, coastlines,
                 total_records, total_ships, frame_num, total_frames):
    """Render a single timelapse frame and return as PIL Image."""
    fig, ax = plt.subplots(figsize=(16, 10), facecolor="#0a0a1a")
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
    for poly in coastlines:
        xs, ys = poly.exterior.xy
        ax.fill(xs, ys, facecolor="#111822", edgecolor="#2a3a4a", linewidth=0.8, zorder=2)

    # Monitoring area
    bbox = MONITOR_BBOX
    bbox_rect = mpatches.FancyBboxPatch(
        (bbox["lon_min"], bbox["lat_min"]),
        bbox["lon_max"] - bbox["lon_min"],
        bbox["lat_max"] - bbox["lat_min"],
        boxstyle="round,pad=0", fill=False,
        edgecolor="#4fc3f7", linewidth=1.5, linestyle=(0, (8, 4)), zorder=4,
    )
    ax.add_patch(bbox_rect)

    # Gate lines
    for gate in GATE_LINES:
        ax.plot(gate["lons"], gate["lats"], color="#ff1744", linewidth=2.0,
                linestyle=(0, (6, 4)), zorder=4, alpha=0.8)

    # Geo labels
    text_effects = [pe.withStroke(linewidth=3, foreground="#0a0a1a")]
    for lat, lon, label, size, color in GEO_LABELS:
        if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
            ax.text(lon, lat, label, fontsize=size, color=color, fontweight="bold",
                    ha="center", va="center", zorder=3, path_effects=text_effects)

    # Trails (faded lines showing recent movement)
    for mmsi, points in trails.items():
        if len(points) < 2:
            continue
        color = get_color(points[0]["type"])
        lats = [p["lat"] for p in points]
        lons = [p["lon"] for p in points]
        ax.plot(lons, lats, color=color, linewidth=0.8, alpha=0.35, zorder=4)

    # Current vessel positions
    if vessels:
        by_type = defaultdict(list)
        for v in vessels:
            type_label = get_ship_type_label(v["ship_type"])
            by_type[type_label].append(v)

        for ship_type, group in sorted(by_type.items()):
            color = get_color(ship_type)
            marker = get_marker(ship_type)
            lats = [v["latitude"] for v in group]
            lons = [v["longitude"] for v in group]
            size = 55 if "Tanker" in ship_type else 40
            ax.scatter(lons, lats, s=size, c=color, marker=marker,
                       edgecolors="white", linewidths=0.5, alpha=0.9,
                       zorder=5, label=f"{ship_type} ({len(group)})")

    # Legend
    legend = ax.legend(
        loc="lower right", fontsize=11, facecolor="#0d1b2aee",
        edgecolor="#2a3a4a", labelcolor="#cccccc", framealpha=0.95,
        borderpad=1.0, handletextpad=0.8, title="Vessel Types", title_fontsize=12,
    )
    if legend.get_title():
        legend.get_title().set_color("#4fc3f7")

    # Title
    fig.text(0.02, 0.97, "Strait of Hormuz  Maritime Monitor — TIMELAPSE",
             fontsize=18, fontweight="bold", color="#e0e0e0", va="top",
             path_effects=[pe.withStroke(linewidth=3, foreground="#0a0a1a")])

    # Large timestamp
    ts_display = datetime.fromisoformat(frame_ts).strftime("%Y-%m-%d  %H:%M UTC")
    fig.text(0.02, 0.935, ts_display, fontsize=14, fontweight="bold",
             color="#4fc3f7", va="top",
             path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")])

    # Vessel count badge
    fig.text(0.88, 0.97, f"{len(vessels)}", fontsize=22, fontweight="bold",
             color="#4fc3f7", va="top", ha="center",
             path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")])
    fig.text(0.88, 0.935, "ACTIVE\nVESSELS", fontsize=7, color="#888888",
             va="top", ha="center", linespacing=1.2)

    # Transit counts (cumulative from data start)
    t_in = sum(v for k, v in transit_counts.items() if "INBOUND" in k)
    t_out = sum(v for k, v in transit_counts.items() if "OUTBOUND" in k)
    if t_in or t_out:
        fig.text(0.96, 0.97, f"{t_in}", fontsize=20, fontweight="bold",
                 color="#4caf50", va="top", ha="center",
                 path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")])
        fig.text(0.96, 0.935, "TRANSITS\nIN", fontsize=7, color="#66bb6a",
                 va="top", ha="center")

    # Progress bar
    progress = (frame_num + 1) / total_frames
    bar_y = 0.015
    bar_h = 0.008
    fig.patches.append(mpatches.FancyBboxPatch(
        (0.02, bar_y), 0.96, bar_h, boxstyle="round,pad=0.002",
        facecolor="#1a2a3a", edgecolor="none", transform=fig.transFigure,
        figure=fig, zorder=10))
    fig.patches.append(mpatches.FancyBboxPatch(
        (0.02, bar_y), 0.96 * progress, bar_h, boxstyle="round,pad=0.002",
        facecolor="#4fc3f7", edgecolor="none", transform=fig.transFigure,
        figure=fig, zorder=11))
    fig.text(0.5, bar_y + bar_h + 0.008,
             f"Frame {frame_num + 1}/{total_frames}", fontsize=8,
             color="#666666", ha="center", va="bottom")

    # Attribution
    fig.text(0.99, 0.04, "Data: aisstream.io  |  github.com/yasumorishima/hormuz-ship-tracker",
             fontsize=8, color="#444444", ha="right", va="bottom")

    # Axis formatting
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}\u00b0E"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}\u00b0N"))
    ax.tick_params(colors="#555555", labelsize=10, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Convert to PIL Image
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).copy()


def generate_timelapse(db_path=DB_PATH, output_dir=OUTPUT_DIR,
                       hours=96, interval_minutes=30, trail_minutes=120,
                       fps=8, filename="timelapse.gif"):
    """Generate a timelapse GIF of vessel movements.

    Args:
        hours: How many hours of data to animate.
        interval_minutes: Time between frames (e.g., 30 = one frame per 30 min).
        trail_minutes: How many minutes of trail to show behind each vessel.
        fps: Frames per second in the output GIF.
        filename: Output filename.
    """
    earliest, latest, total_records, total_ships = query_data_range(db_path)
    if not earliest or not latest:
        print("No data available.")
        return None

    print(f"Data range: {earliest} → {latest}")
    print(f"Total: {total_records:,} positions, {total_ships} unique ships")

    end_dt = datetime.fromisoformat(latest)
    start_dt = max(
        datetime.fromisoformat(earliest),
        end_dt - timedelta(hours=hours),
    )

    # Generate time steps
    steps = []
    current = start_dt
    while current <= end_dt:
        steps.append(current.isoformat())
        current += timedelta(minutes=interval_minutes)

    total_frames = len(steps)
    print(f"Generating {total_frames} frames ({interval_minutes}min interval, {trail_minutes}min trails)...")

    coastlines = _load_coastline_polygons()
    frames = []

    for i, frame_ts in enumerate(steps):
        next_ts = (datetime.fromisoformat(frame_ts) + timedelta(minutes=interval_minutes)).isoformat()

        # Get current positions (vessels visible in this window)
        vessels = query_positions_in_window(db_path, frame_ts, next_ts)
        # If no vessels in exact window, look back a bit
        if not vessels:
            lookback_ts = (datetime.fromisoformat(frame_ts) - timedelta(minutes=15)).isoformat()
            vessels = query_positions_in_window(db_path, lookback_ts, next_ts)

        trails = query_trail_positions(db_path, frame_ts, trail_minutes)
        transit_counts = query_transit_events_in_window(
            db_path, start_dt.isoformat(), frame_ts
        )

        img = render_frame(
            frame_ts, vessels, trails, transit_counts, coastlines,
            total_records, total_ships, i, total_frames,
        )
        frames.append(img)

        if (i + 1) % 10 == 0 or i == 0:
            print(f"  [{i + 1}/{total_frames}] {frame_ts[:16]} — {len(vessels)} vessels")

    if not frames:
        print("No frames generated.")
        return None

    output_path = output_dir / filename
    duration_ms = int(1000 / fps)

    # Last frame stays longer
    durations = [duration_ms] * len(frames)
    durations[-1] = duration_ms * 4

    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )

    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nTimelapse saved: {output_path} ({file_size_mb:.1f} MB)")
    print(f"  {total_frames} frames, {fps} fps, ~{total_frames / fps:.0f}s playback")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate vessel movement timelapse GIF")
    parser.add_argument("--db", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    parser.add_argument("--hours", type=int, default=96, help="Hours of data to animate")
    parser.add_argument("--interval", type=int, default=30, help="Minutes between frames")
    parser.add_argument("--trail", type=int, default=120, help="Trail length in minutes")
    parser.add_argument("--fps", type=int, default=8, help="Frames per second")
    parser.add_argument("--filename", default="timelapse.gif", help="Output filename")
    args = parser.parse_args()

    generate_timelapse(
        db_path=args.db,
        output_dir=Path(args.output_dir),
        hours=args.hours,
        interval_minutes=args.interval,
        trail_minutes=args.trail,
        fps=args.fps,
        filename=args.filename,
    )
