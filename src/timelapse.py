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

from land_filter import is_on_land  # noqa: E402

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


def load_all_trajectories(db_path, start_ts, end_ts):
    """Load ALL position data for the time range, grouped by MMSI.

    Returns dict: mmsi -> list of {ts_epoch, lat, lon, speed, ship_name, ship_type, flag}
    sorted by timestamp. This enables smooth interpolation between any two timestamps.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT mmsi, latitude, longitude, speed, ship_name, ship_type, flag, timestamp
            FROM positions
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY mmsi, timestamp
        """, (start_ts, end_ts)).fetchall()

        trajectories = defaultdict(list)
        for r in rows:
            ts_epoch = datetime.fromisoformat(r["timestamp"]).timestamp()
            trajectories[r["mmsi"]].append({
                "ts": ts_epoch,
                "lat": r["latitude"],
                "lon": r["longitude"],
                "speed": r["speed"],
                "ship_name": r["ship_name"],
                "ship_type": r["ship_type"],
                "flag": r["flag"],
            })
        return trajectories
    finally:
        conn.close()


def _is_ais_unavailable(speed):
    """Check if speed indicates AIS data unavailable (102.3 knots sentinel)."""
    return speed is not None and speed > 100.0


def interpolate_positions(trajectories, frame_epoch, max_age_seconds=1800):
    """For each vessel, interpolate its position at the given timestamp.

    Uses linear interpolation between the two nearest known positions.
    Skips interpolation when:
      - The vessel has no data within max_age_seconds
      - The interpolated point would fall on land (prevents crossing peninsulas)
      - Either endpoint has AIS-unavailable speed (102.3 kn = position unreliable)
      - The two points are too far apart geographically (> 0.5 degrees)

    Returns list of vessel dicts with interpolated lat/lon.
    """
    vessels = []
    for mmsi, points in trajectories.items():
        if points[0]["ts"] > frame_epoch + max_age_seconds:
            continue
        if points[-1]["ts"] < frame_epoch - max_age_seconds:
            continue

        # Find bracketing indices
        before_idx = None
        after_idx = None
        for i, p in enumerate(points):
            if p["ts"] <= frame_epoch:
                before_idx = i
            if p["ts"] >= frame_epoch and after_idx is None:
                after_idx = i

        if before_idx is None and after_idx is None:
            continue

        lat, lon, speed = None, None, None

        if before_idx is not None and after_idx is not None and before_idx != after_idx:
            p0 = points[before_idx]
            p1 = points[after_idx]
            dt = p1["ts"] - p0["ts"]

            # Geographic distance between the two points
            dlat = abs(p1["lat"] - p0["lat"])
            dlon = abs(p1["lon"] - p0["lon"])

            can_interpolate = (
                dt > 0
                and dt < max_age_seconds * 2
                and not _is_ais_unavailable(p0["speed"])
                and not _is_ais_unavailable(p1["speed"])
                and dlat < 0.5 and dlon < 0.5  # ~55km max jump
            )

            if can_interpolate:
                t = (frame_epoch - p0["ts"]) / dt
                lat = p0["lat"] + t * (p1["lat"] - p0["lat"])
                lon = p0["lon"] + t * (p1["lon"] - p0["lon"])
                speed = p0["speed"] if p0["speed"] else p1["speed"]

                # Check if interpolated point is on land → fall back to nearest
                if is_on_land(lat, lon):
                    nearest = p0 if abs(p0["ts"] - frame_epoch) < abs(p1["ts"] - frame_epoch) else p1
                    lat, lon, speed = nearest["lat"], nearest["lon"], nearest["speed"]
            else:
                # Use nearest known position (no interpolation)
                nearest = p0 if abs(p0["ts"] - frame_epoch) < abs(p1["ts"] - frame_epoch) else p1
                lat, lon, speed = nearest["lat"], nearest["lon"], nearest["speed"]
        else:
            idx = before_idx if before_idx is not None else after_idx
            p = points[idx]
            if abs(p["ts"] - frame_epoch) > max_age_seconds:
                continue
            lat, lon, speed = p["lat"], p["lon"], p["speed"]

        # Final land check — skip positions on land entirely
        if is_on_land(lat, lon):
            continue
        # Skip AIS-unavailable ghost positions
        if _is_ais_unavailable(speed):
            continue

        ref = points[before_idx if before_idx is not None else after_idx]
        vessels.append({
            "mmsi": mmsi,
            "latitude": lat,
            "longitude": lon,
            "speed": speed,
            "ship_name": ref["ship_name"],
            "ship_type": ref["ship_type"],
            "flag": ref["flag"],
        })
    return vessels


def get_trails_at(trajectories, frame_epoch, trail_seconds=7200):
    """Extract trail polylines for each vessel up to the frame timestamp.

    Returns dict: mmsi -> list of {lat, lon, type}.
    """
    trails = {}
    cutoff = frame_epoch - trail_seconds
    for mmsi, points in trajectories.items():
        trail_pts = [
            {"lat": p["lat"], "lon": p["lon"],
             "type": get_ship_type_label(p["ship_type"])}
            for p in points
            if cutoff <= p["ts"] <= frame_epoch
        ]
        if len(trail_pts) >= 2:
            trails[mmsi] = trail_pts
    return trails


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
                       hours=96, interval_minutes=10, trail_minutes=120,
                       fps=10, filename="timelapse.gif"):
    """Generate a timelapse GIF with smooth interpolated vessel movement.

    Key change: loads ALL trajectories upfront, then interpolates each vessel's
    position at every frame timestamp. This produces smooth, continuous motion
    instead of jerky frame-to-frame jumps.

    Args:
        hours: How many hours of data to animate.
        interval_minutes: Time between frames (smaller = smoother, more frames).
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

    # Load ALL trajectories once (the key to smooth animation)
    print("Loading all vessel trajectories...")
    trajectories = load_all_trajectories(db_path, start_dt.isoformat(), end_dt.isoformat())
    print(f"  Loaded {sum(len(v) for v in trajectories.values()):,} positions for {len(trajectories)} vessels")

    # Generate time steps
    steps = []
    current = start_dt
    while current <= end_dt:
        steps.append(current)
        current += timedelta(minutes=interval_minutes)

    total_frames = len(steps)
    print(f"Generating {total_frames} frames ({interval_minutes}min interval, {trail_minutes}min trails)...")

    coastlines = _load_coastline_polygons()
    frames = []
    trail_seconds = trail_minutes * 60
    max_age = interval_minutes * 60 * 3  # allow 3x interval gap before hiding vessel

    for i, frame_dt in enumerate(steps):
        frame_epoch = frame_dt.timestamp()
        frame_ts = frame_dt.isoformat()

        # Interpolate all vessel positions at this exact timestamp
        vessels = interpolate_positions(trajectories, frame_epoch, max_age_seconds=max_age)
        trails = get_trails_at(trajectories, frame_epoch, trail_seconds=trail_seconds)
        transit_counts = query_transit_events_in_window(
            db_path, start_dt.isoformat(), frame_ts
        )

        img = render_frame(
            frame_ts, vessels, trails, transit_counts, coastlines,
            total_records, total_ships, i, total_frames,
        )
        frames.append(img)

        if (i + 1) % 20 == 0 or i == 0:
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
