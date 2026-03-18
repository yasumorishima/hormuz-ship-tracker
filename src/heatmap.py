"""Generate vessel traffic density heatmap from AIS position data.

Two-panel layout:
  Left: Full Persian Gulf overview (hexbin density)
  Right: Zoomed-in Strait of Hormuz area

Usage:
    python src/heatmap.py [--hours 96] [--output heatmap.png]
"""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
import numpy as np  # noqa: E402
from shapely.geometry import shape  # noqa: E402

DB_PATH = "/app/data/ais.db"
OUTPUT_DIR = Path("/app/data")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEOJSON_PATH = _DATA_DIR / "land_mask.geojson"

GATE_LINES = [
    {"name": "Strait of Hormuz", "lats": [26.05, 26.65], "lons": [56.50, 56.10]},
    {"name": "Dubai / Jebel Ali", "lats": [25.00, 25.35], "lons": [55.20, 55.20]},
    {"name": "Fujairah", "lats": [25.00, 25.30], "lons": [56.50, 56.50]},
]


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
    """Query all valid positions (excluding AIS anomalies)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if hours > 0:
            rows = conn.execute("""
                SELECT latitude, longitude, speed, ship_type, flag
                FROM positions
                WHERE timestamp > datetime(
                    (SELECT MAX(timestamp) FROM positions), ? || ' hours')
                  AND (speed IS NULL OR speed < 40)
            """, (f"-{hours}",)).fetchall()
        else:
            rows = conn.execute("""
                SELECT latitude, longitude, speed, ship_type, flag
                FROM positions
                WHERE speed IS NULL OR speed < 40
            """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_stats(db_path, hours):
    conn = sqlite3.connect(db_path)
    try:
        if hours > 0:
            tf = f"WHERE timestamp > datetime((SELECT MAX(timestamp) FROM positions), '-{hours} hours')"
        else:
            tf = ""
        total = conn.execute(f"SELECT COUNT(*) FROM positions {tf}").fetchone()[0]
        filtered = conn.execute(
            f"SELECT COUNT(*) FROM positions {tf.replace('WHERE', 'WHERE (speed IS NULL OR speed < 40) AND') if tf else 'WHERE speed IS NULL OR speed < 40'}"
        ).fetchone()[0]
        unique = conn.execute(
            f"SELECT COUNT(DISTINCT mmsi) FROM positions {tf}"
        ).fetchone()[0]
        earliest = conn.execute("SELECT MIN(timestamp) FROM positions").fetchone()[0]
        latest = conn.execute("SELECT MAX(timestamp) FROM positions").fetchone()[0]
        anomaly_count = total - filtered
        return {
            "total": total, "filtered": filtered, "unique_ships": unique,
            "earliest": earliest, "latest": latest,
            "anomaly_count": anomaly_count,
        }
    finally:
        conn.close()


def _draw_coastline(ax, coastlines):
    for poly in coastlines:
        xs, ys = poly.exterior.xy
        ax.fill(xs, ys, facecolor="#1a2535", edgecolor="#3a5060", linewidth=0.6, zorder=2)


def _draw_gates(ax, fontsize=9):
    for gate in GATE_LINES:
        ax.plot(gate["lons"], gate["lats"], color="#00e5ff", linewidth=2,
                linestyle=(0, (5, 3)), zorder=8, alpha=0.9)
        mid_lat = sum(gate["lats"]) / 2
        mid_lon = sum(gate["lons"]) / 2
        ax.text(mid_lon + 0.12, mid_lat, gate["name"],
                fontsize=fontsize, color="#00e5ff", fontweight="bold",
                ha="left", va="center", zorder=8,
                path_effects=[pe.withStroke(linewidth=2, foreground="#0a0e18")])


def _style_axis(ax):
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}\u00b0E"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}\u00b0N"))
    ax.tick_params(colors="#667788", labelsize=10, length=0)
    for spine in ax.spines.values():
        spine.set_edgecolor("#2a3a4a")


def generate_heatmap(db_path=DB_PATH, output_dir=OUTPUT_DIR,
                     hours=0, filename="heatmap.png"):
    positions = query_all_positions(db_path, hours)
    stats = query_stats(db_path, hours)

    if not positions:
        print("No data available.")
        return None

    lats = np.array([p["latitude"] for p in positions])
    lons = np.array([p["longitude"] for p in positions])

    print(f"Generating heatmap from {len(positions):,} clean positions "
          f"({stats['anomaly_count']:,} anomalies filtered)")
    print(f"Data range: {stats['earliest'][:16]} → {stats['latest'][:16]}")

    coastlines = _load_coastline_polygons()

    # Custom colormap: dark blue → cyan → yellow → white
    cmap_colors = ["#0a0e18", "#0a2a4a", "#0077b6", "#00b4d8",
                   "#48cae4", "#90e0ef", "#ade8f4", "#fff3b0", "#ffffff"]
    cmap = mcolors.LinearSegmentedColormap.from_list("maritime", cmap_colors, N=256)

    # ── Figure: 2 panels ──
    fig = plt.figure(figsize=(22, 11), facecolor="#0a0e18")

    # ── Left panel: Full Gulf ──
    ax1 = fig.add_axes([0.03, 0.10, 0.48, 0.78])
    ax1.set_facecolor("#0a0e18")
    ax1.set_xlim(47.5, 60.0)
    ax1.set_ylim(22.0, 30.5)
    ax1.set_aspect("equal")

    _draw_coastline(ax1, coastlines)

    # Hexbin — the right tool for spatial density
    hb1 = ax1.hexbin(lons, lats, gridsize=80, cmap=cmap, mincnt=1,
                     linewidths=0.1, edgecolors="#1a2535", zorder=3,
                     bins="log")

    _draw_gates(ax1, fontsize=8)

    # Labels
    te = [pe.withStroke(linewidth=3, foreground="#0a0e18")]
    for lat, lon, label, size, color in [
        (28.50, 53.00, "IRAN", 16, "#8899aa"),
        (23.80, 54.60, "UAE", 16, "#8899aa"),
        (24.00, 57.80, "OMAN", 16, "#8899aa"),
        (29.30, 48.50, "KUWAIT", 10, "#667788"),
        (25.35, 54.80, "QATAR", 10, "#667788"),
        (25.20, 55.30, "Dubai", 10, "#778899"),
        (27.20, 56.60, "Bandar Abbas", 9, "#778899"),
        (23.60, 58.55, "Muscat", 10, "#778899"),
        (26.25, 56.25, "Strait of\nHormuz", 12, "#00e5ff"),
    ]:
        ax1.text(lon, lat, label, fontsize=size, color=color, fontweight="bold",
                 ha="center", va="center", zorder=5, path_effects=te)

    ax1.set_title("Persian Gulf — Full Coverage", fontsize=14,
                  color="#aabbcc", pad=10, loc="left")
    _style_axis(ax1)

    # Colorbar
    cbar = fig.colorbar(hb1, ax=ax1, shrink=0.7, pad=0.02, aspect=30)
    cbar.set_label("Position Count (log scale)", color="#8899aa", fontsize=11)
    cbar.ax.yaxis.set_tick_params(color="#667788")
    cbar.outline.set_edgecolor("#2a3a4a")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="#8899aa")

    # ── Right panel: Strait zoom ──
    ax2 = fig.add_axes([0.55, 0.10, 0.42, 0.78])
    ax2.set_facecolor("#0a0e18")
    ax2.set_xlim(54.5, 57.5)
    ax2.set_ylim(24.5, 27.0)
    ax2.set_aspect("equal")

    _draw_coastline(ax2, coastlines)

    # Filter positions in zoom area
    mask = (lons >= 54.5) & (lons <= 57.5) & (lats >= 24.5) & (lats <= 27.0)
    zoom_lons = lons[mask]
    zoom_lats = lats[mask]

    if len(zoom_lons) > 0:
        hb2 = ax2.hexbin(zoom_lons, zoom_lats, gridsize=60, cmap=cmap, mincnt=1,
                         linewidths=0.1, edgecolors="#1a2535", zorder=3,
                         bins="log")

    _draw_gates(ax2, fontsize=10)

    # Strait zoom labels
    for lat, lon, label, size, color in [
        (26.25, 56.25, "Strait of\nHormuz", 14, "#00e5ff"),
        (26.80, 56.50, "Bandar Abbas", 10, "#778899"),
        (25.20, 55.30, "Dubai", 12, "#778899"),
        (25.10, 56.30, "Fujairah", 10, "#778899"),
        (26.50, 55.00, "IRAN", 14, "#8899aa"),
        (24.80, 55.80, "UAE", 12, "#8899aa"),
        (25.00, 57.20, "OMAN", 12, "#8899aa"),
    ]:
        if 54.5 <= lon <= 57.5 and 24.5 <= lat <= 27.0:
            ax2.text(lon, lat, label, fontsize=size, color=color, fontweight="bold",
                     ha="center", va="center", zorder=5, path_effects=te)

    # AIS Dead Zone annotation
    from matplotlib.patches import FancyArrowPatch
    ax2.annotate(
        "AIS Dead Zone\n(no terrestrial\ncoverage mid-strait)",
        xy=(56.3, 26.35), xytext=(56.8, 26.7),
        fontsize=10, color="#ff6b6b", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#ff6b6b", lw=1.5,
                        connectionstyle="arc3,rad=0.2"),
        zorder=9,
        path_effects=[pe.withStroke(linewidth=2, foreground="#0a0e18")],
    )

    ax2.set_title("Strait of Hormuz — Zoomed", fontsize=14,
                  color="#aabbcc", pad=10, loc="left")
    _style_axis(ax2)

    # ── Title block ──
    hours_label = f"Past {hours}h" if hours > 0 else "All Data"
    period = f"{stats['earliest'][:10]} → {stats['latest'][:10]}"
    fig.text(0.03, 0.97,
             "Strait of Hormuz — Vessel Traffic Density",
             fontsize=22, fontweight="bold", color="#ddeeff", va="top",
             path_effects=[pe.withStroke(linewidth=3, foreground="#0a0e18")])
    fig.text(0.03, 0.925,
             f"{hours_label}  |  {len(positions):,} positions (anomalies excluded)  |  "
             f"{stats['unique_ships']} ships  |  {period}",
             fontsize=11, color="#8899aa", va="top")
    fig.text(0.03, 0.90,
             f"Strait transit: 0 confirmed  |  "
             f"{stats['anomaly_count']:,} anomalous positions filtered (AIS speed >= 40 kn)",
             fontsize=10, color="#ff6b6b", va="top", fontweight="bold",
             path_effects=[pe.withStroke(linewidth=2, foreground="#0a0e18")])

    # Attribution
    fig.text(0.99, 0.01,
             "Data: aisstream.io  |  github.com/yasumorishima/hormuz-ship-tracker",
             fontsize=8, color="#445566", ha="right", va="bottom")

    output_path = output_dir / filename
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Heatmap saved: {output_path} ({file_size_mb:.1f} MB)")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate vessel traffic heatmap")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--hours", type=int, default=0, help="Hours of data (0=all)")
    parser.add_argument("--filename", default="heatmap.png")
    args = parser.parse_args()

    generate_heatmap(
        db_path=args.db,
        output_dir=Path(args.output_dir),
        hours=args.hours,
        filename=args.filename,
    )
