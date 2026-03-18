"""Generate vessel traffic density heatmap from AIS position data.

Three-panel layout:
  Top-left:  Full Persian Gulf overview (hexbin density)
  Top-right: Zoomed Strait of Hormuz with dead zone highlight
  Bottom:    Infographic bar — port counts, flag distribution, ship types

Usage:
    python src/heatmap.py [--hours 96] [--output heatmap.png]
"""

import argparse
import json
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import numpy as np  # noqa: E402
from shapely.geometry import shape  # noqa: E402

DB_PATH = "/app/data/ais.db"
OUTPUT_DIR = Path("/app/data")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEOJSON_PATH = _DATA_DIR / "land_mask.geojson"

BG = "#080c14"
PANEL_BG = "#0c1220"
TEXT_PRIMARY = "#ddeeff"
TEXT_SECONDARY = "#8899aa"
TEXT_DIM = "#556677"
ACCENT = "#00e5ff"
WARN = "#ff6b6b"

GATE_LINES = [
    {"name": "Strait of Hormuz", "lats": [26.05, 26.65], "lons": [56.50, 56.10],
     "label_offset": (0.15, 0.0)},
    {"name": "Dubai / Jebel Ali", "lats": [25.00, 25.35], "lons": [55.20, 55.20],
     "label_offset": (-0.6, 0.0)},
    {"name": "Fujairah", "lats": [25.00, 25.30], "lons": [56.50, 56.50],
     "label_offset": (0.12, -0.15)},
]

SHIP_TYPE_RANGES = {
    range(20, 30): "WIG",
    range(30, 36): "Fish/Tow",
    range(36, 40): "Military",
    range(40, 50): "HSC",
    range(60, 70): "Passenger",
    range(70, 80): "Cargo",
    range(80, 90): "Tanker",
    range(90, 100): "Other",
}

TYPE_COLORS_BAR = {
    "Tanker": "#e65100",
    "Cargo": "#1565c0",
    "Military": "#b71c1c",
    "Passenger": "#2e7d32",
    "Fish/Tow": "#6a1b9a",
    "Other": "#455a64",
    "HSC": "#00838f",
    "WIG": "#795548",
    "Unknown": "#37474f",
}

FLAG_NAMES = {
    "PA": "Panama", "AE": "UAE", "MH": "Marshall Is.", "LR": "Liberia",
    "KN": "St. Kitts", "SG": "Singapore", "HK": "Hong Kong", "KM": "Comoros",
    "VC": "St. Vincent", "NL": "Netherlands", "GR": "Greece", "TH": "Thailand",
    "KY": "Cayman", "KR": "S. Korea", "SA": "Saudi", "CN": "China",
    "IN": "India", "KW": "Kuwait", "PK": "Pakistan", "IR": "Iran",
}

PORT_AREAS = [
    ("Dubai / Jebel Ali", 25.05, 55.10, 0.3),
    ("Sharjah / Ajman", 25.35, 55.40, 0.2),
    ("Abu Dhabi", 24.50, 54.40, 0.3),
    ("Ras Tanura", 26.65, 50.15, 0.3),
]


def get_type_label(code):
    if code is None:
        return "Unknown"
    for r, label in SHIP_TYPE_RANGES.items():
        if code in r:
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
                FROM positions WHERE speed IS NULL OR speed < 40
            """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_infographic_data(db_path, hours):
    """Query data for the infographic panel."""
    conn = sqlite3.connect(db_path)
    try:
        if hours > 0:
            tf = f"AND timestamp > datetime((SELECT MAX(timestamp) FROM positions), '-{hours} hours')"
        else:
            tf = ""

        # Flags
        flags = conn.execute(f"""
            SELECT flag, COUNT(DISTINCT mmsi) as cnt
            FROM positions WHERE flag IS NOT NULL AND flag != '' {tf}
            GROUP BY flag ORDER BY cnt DESC LIMIT 8
        """).fetchall()

        # Ship types
        types_raw = conn.execute(f"""
            SELECT ship_type, COUNT(DISTINCT mmsi) as cnt
            FROM positions WHERE 1=1 {tf}
            GROUP BY ship_type ORDER BY cnt DESC
        """).fetchall()
        type_agg = {}
        for code, cnt in types_raw:
            t = get_type_label(code)
            type_agg[t] = type_agg.get(t, 0) + cnt

        # Port counts
        port_counts = {}
        for name, lat, lon, r in PORT_AREAS:
            cnt = conn.execute(f"""
                SELECT COUNT(DISTINCT mmsi) FROM positions
                WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
                  AND (speed IS NULL OR speed < 40) {tf}
            """, (lat - r, lat + r, lon - r, lon + r)).fetchone()[0]
            port_counts[name] = cnt

        # Stats
        total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        clean = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE speed IS NULL OR speed < 40"
        ).fetchone()[0]
        unique = conn.execute("SELECT COUNT(DISTINCT mmsi) FROM positions").fetchone()[0]
        earliest = conn.execute("SELECT MIN(timestamp) FROM positions").fetchone()[0]
        latest = conn.execute("SELECT MAX(timestamp) FROM positions").fetchone()[0]

        return {
            "flags": [(r[0], r[1]) for r in flags],
            "types": sorted(type_agg.items(), key=lambda x: -x[1]),
            "ports": port_counts,
            "total": total, "clean": clean, "unique": unique,
            "anomaly": total - clean,
            "earliest": earliest, "latest": latest,
        }
    finally:
        conn.close()


def _draw_coastline(ax, coastlines):
    for poly in coastlines:
        xs, ys = poly.exterior.xy
        ax.fill(xs, ys, facecolor="#151e2e", edgecolor="#2a3a50", linewidth=0.5, zorder=2)


def _draw_gates(ax, fontsize=9, zoom=False):
    te = [pe.withStroke(linewidth=2, foreground=PANEL_BG)]
    for gate in GATE_LINES:
        ax.plot(gate["lons"], gate["lats"], color=ACCENT, linewidth=2.5,
                linestyle=(0, (5, 3)), zorder=8, alpha=0.9)
        # Endpoints
        ax.scatter(gate["lons"], gate["lats"], s=20, c=ACCENT,
                   edgecolors="white", linewidths=0.5, zorder=9)
        if zoom:
            ox, oy = gate["label_offset"]
            mid_lat = sum(gate["lats"]) / 2 + oy
            mid_lon = sum(gate["lons"]) / 2 + ox
            ax.text(mid_lon, mid_lat, gate["name"],
                    fontsize=fontsize, color=ACCENT, fontweight="bold",
                    ha="left", va="center", zorder=8, path_effects=te)


def _style_axis(ax):
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}\u00b0E"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}\u00b0N"))
    ax.tick_params(colors=TEXT_DIM, labelsize=10, length=0)
    for spine in ax.spines.values():
        spine.set_edgecolor("#1a2a3a")


def generate_heatmap(db_path=DB_PATH, output_dir=OUTPUT_DIR,
                     hours=0, filename="heatmap.png"):
    positions = query_all_positions(db_path, hours)
    info = query_infographic_data(db_path, hours)

    if not positions:
        print("No data available.")
        return None

    lats = np.array([p["latitude"] for p in positions])
    lons = np.array([p["longitude"] for p in positions])

    print(f"Generating heatmap from {len(positions):,} clean positions "
          f"({info['anomaly']:,} anomalies filtered)")

    coastlines = _load_coastline_polygons()

    # Custom colormap with better mid-range visibility
    cmap = mcolors.LinearSegmentedColormap.from_list("maritime", [
        "#0c1220",   # background (invisible at min)
        "#0a3060",   # dark blue
        "#0077b6",   # blue
        "#00b4d8",   # cyan
        "#48cae4",   # light cyan
        "#90e0ef",   # pale cyan
        "#caf0f8",   # very pale
        "#fff176",   # yellow (high density)
        "#ffee58",   # bright yellow (peak)
    ], N=256)

    # Normalize: clip top 1% to prevent Dubai from dominating
    x_bins_full = np.linspace(47.5, 60.0, 250)
    y_bins_full = np.linspace(22.0, 30.5, 180)
    hist_preview, _, _ = np.histogram2d(lons, lats, bins=[x_bins_full, y_bins_full])
    vmax_clip = np.percentile(hist_preview[hist_preview > 0], 97)

    # ── Figure ──
    fig = plt.figure(figsize=(24, 14), facecolor=BG)

    # ── Top-left: Full Gulf ──
    ax1 = fig.add_axes([0.02, 0.28, 0.46, 0.62])
    ax1.set_facecolor(PANEL_BG)
    ax1.set_xlim(47.5, 60.0)
    ax1.set_ylim(22.0, 30.5)
    ax1.set_aspect("equal")

    _draw_coastline(ax1, coastlines)

    hb1 = ax1.hexbin(lons, lats, gridsize=90, cmap=cmap, mincnt=1,
                     linewidths=0.05, edgecolors=PANEL_BG, zorder=3,
                     reduce_C_function=np.sum,
                     norm=mcolors.LogNorm(vmin=1, vmax=max(vmax_clip, 10)))

    _draw_gates(ax1, fontsize=7, zoom=False)

    te = [pe.withStroke(linewidth=3, foreground=PANEL_BG)]
    for lat, lon, label, size, color in [
        (28.50, 53.00, "IRAN", 18, "#667788"),
        (23.80, 54.60, "UAE", 18, "#667788"),
        (24.20, 58.00, "OMAN", 18, "#667788"),
        (29.30, 48.30, "KUWAIT", 11, TEXT_DIM),
        (25.40, 54.60, "QATAR", 11, TEXT_DIM),
        (25.10, 55.40, "Dubai", 10, TEXT_SECONDARY),
        (27.30, 56.80, "Bandar\nAbbas", 9, TEXT_SECONDARY),
        (23.60, 58.55, "Muscat", 10, TEXT_SECONDARY),
        (26.30, 56.30, "Strait of\nHormuz", 11, ACCENT),
    ]:
        ax1.text(lon, lat, label, fontsize=size, color=color, fontweight="bold",
                 ha="center", va="center", zorder=5, path_effects=te)

    ax1.set_title("Persian Gulf — Full Coverage", fontsize=16,
                  color=TEXT_PRIMARY, pad=12, loc="left", fontweight="bold")
    _style_axis(ax1)

    # Colorbar
    cbar = fig.colorbar(hb1, ax=ax1, shrink=0.6, pad=0.015, aspect=25)
    cbar.set_label("Positions per cell", color=TEXT_SECONDARY, fontsize=11)
    cbar.ax.yaxis.set_tick_params(color=TEXT_DIM)
    cbar.outline.set_edgecolor("#1a2a3a")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=TEXT_SECONDARY, fontsize=9)

    # ── Top-right: Strait zoom ──
    ax2 = fig.add_axes([0.52, 0.28, 0.46, 0.62])
    ax2.set_facecolor(PANEL_BG)
    ax2.set_xlim(54.3, 57.3)
    ax2.set_ylim(24.5, 27.2)
    ax2.set_aspect("equal")

    _draw_coastline(ax2, coastlines)

    mask = (lons >= 54.3) & (lons <= 57.3) & (lats >= 24.5) & (lats <= 27.2)
    zoom_lons, zoom_lats = lons[mask], lats[mask]

    if len(zoom_lons) > 0:
        # Finer grid for zoom
        hist_zoom, _, _ = np.histogram2d(zoom_lons, zoom_lats,
                                         bins=[np.linspace(54.3, 57.3, 100),
                                               np.linspace(24.5, 27.2, 90)])
        vmax_zoom = np.percentile(hist_zoom[hist_zoom > 0], 95)
        ax2.hexbin(zoom_lons, zoom_lats, gridsize=80, cmap=cmap, mincnt=1,
                   linewidths=0.05, edgecolors=PANEL_BG, zorder=3,
                   norm=mcolors.LogNorm(vmin=1, vmax=max(vmax_zoom, 10)))

    _draw_gates(ax2, fontsize=11, zoom=True)

    for lat, lon, label, size, color in [
        (26.35, 56.30, "Strait of\nHormuz", 14, ACCENT),
        (26.90, 56.60, "Bandar Abbas", 11, TEXT_SECONDARY),
        (25.15, 55.15, "Dubai", 13, TEXT_SECONDARY),
        (24.85, 56.35, "Fujairah", 11, TEXT_SECONDARY),
        (26.60, 54.80, "IRAN", 16, "#667788"),
        (24.65, 55.60, "UAE", 14, "#667788"),
        (24.80, 57.10, "OMAN", 14, "#667788"),
    ]:
        if 54.3 <= lon <= 57.3 and 24.5 <= lat <= 27.2:
            ax2.text(lon, lat, label, fontsize=size, color=color, fontweight="bold",
                     ha="center", va="center", zorder=5, path_effects=te)

    # Dead zone — shaded rectangle + annotation
    dead_zone = mpatches.FancyBboxPatch(
        (56.05, 26.05), 0.55, 0.65, boxstyle="round,pad=0.05",
        facecolor=WARN, alpha=0.08, edgecolor=WARN,
        linewidth=1.5, linestyle="--", zorder=6,
    )
    ax2.add_patch(dead_zone)
    ax2.text(56.32, 26.80, "AIS DEAD ZONE",
             fontsize=10, color=WARN, fontweight="bold",
             ha="center", va="bottom", zorder=7,
             path_effects=[pe.withStroke(linewidth=2, foreground=PANEL_BG)])
    ax2.text(56.32, 26.75, "No terrestrial coverage\nmid-strait (~30 nm offshore)",
             fontsize=8, color="#cc5555", ha="center", va="top", zorder=7,
             path_effects=[pe.withStroke(linewidth=2, foreground=PANEL_BG)])

    ax2.set_title("Strait of Hormuz — Zoomed", fontsize=16,
                  color=TEXT_PRIMARY, pad=12, loc="left", fontweight="bold")
    _style_axis(ax2)

    # ── Bottom: Infographic bar ──
    # Three mini charts: Ports | Flags | Ship Types

    # --- Ports ---
    ax_ports = fig.add_axes([0.04, 0.04, 0.25, 0.18])
    ax_ports.set_facecolor(PANEL_BG)
    port_names = list(info["ports"].keys())
    port_vals = list(info["ports"].values())
    bars = ax_ports.barh(port_names, port_vals, color=ACCENT, alpha=0.8, height=0.6)
    for bar, val in zip(bars, port_vals):
        if val > 0:
            ax_ports.text(bar.get_width() + 2, bar.get_y() + bar.get_height() / 2,
                          str(val), va="center", ha="left",
                          fontsize=12, color=TEXT_PRIMARY, fontweight="bold")
    ax_ports.set_title("Ships by Port Area", fontsize=13,
                       color=TEXT_PRIMARY, loc="left", fontweight="bold", pad=8)
    ax_ports.set_xlim(0, max(port_vals) * 1.3 if port_vals else 10)
    ax_ports.tick_params(colors=TEXT_SECONDARY, labelsize=10)
    ax_ports.spines["top"].set_visible(False)
    ax_ports.spines["right"].set_visible(False)
    ax_ports.spines["bottom"].set_edgecolor("#1a2a3a")
    ax_ports.spines["left"].set_edgecolor("#1a2a3a")
    ax_ports.xaxis.set_visible(False)

    # --- Flags ---
    ax_flags = fig.add_axes([0.36, 0.04, 0.28, 0.18])
    ax_flags.set_facecolor(PANEL_BG)
    flag_labels = [FLAG_NAMES.get(f, f) for f, _ in info["flags"]]
    flag_vals = [c for _, c in info["flags"]]
    flag_colors = ["#0077b6", "#00b4d8", "#48cae4", "#90e0ef",
                   "#ade8f4", "#caf0f8", "#e0f7fa", "#fff176"]
    bars = ax_flags.barh(flag_labels[::-1], flag_vals[::-1],
                         color=flag_colors[:len(flag_vals)][::-1],
                         alpha=0.85, height=0.6)
    for bar, val in zip(bars, flag_vals[::-1]):
        if val > 0:
            ax_flags.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                          str(val), va="center", ha="left",
                          fontsize=11, color=TEXT_PRIMARY, fontweight="bold")
    ax_flags.set_title("Ships by Flag State", fontsize=13,
                       color=TEXT_PRIMARY, loc="left", fontweight="bold", pad=8)
    ax_flags.set_xlim(0, max(flag_vals) * 1.2 if flag_vals else 10)
    ax_flags.tick_params(colors=TEXT_SECONDARY, labelsize=10)
    ax_flags.spines["top"].set_visible(False)
    ax_flags.spines["right"].set_visible(False)
    ax_flags.spines["bottom"].set_edgecolor("#1a2a3a")
    ax_flags.spines["left"].set_edgecolor("#1a2a3a")
    ax_flags.xaxis.set_visible(False)

    # --- Ship Types ---
    ax_types = fig.add_axes([0.70, 0.04, 0.28, 0.18])
    ax_types.set_facecolor(PANEL_BG)
    type_labels = [t for t, _ in info["types"] if t != "Unknown"][:7]
    type_vals = [c for t, c in info["types"] if t != "Unknown"][:7]
    type_bar_colors = [TYPE_COLORS_BAR.get(t, "#455a64") for t in type_labels]
    bars = ax_types.barh(type_labels[::-1], type_vals[::-1],
                         color=type_bar_colors[::-1], alpha=0.85, height=0.6)
    for bar, val in zip(bars, type_vals[::-1]):
        if val > 0:
            ax_types.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                          str(val), va="center", ha="left",
                          fontsize=11, color=TEXT_PRIMARY, fontweight="bold")
    ax_types.set_title("Ships by Type", fontsize=13,
                       color=TEXT_PRIMARY, loc="left", fontweight="bold", pad=8)
    ax_types.set_xlim(0, max(type_vals) * 1.2 if type_vals else 10)
    ax_types.tick_params(colors=TEXT_SECONDARY, labelsize=10)
    ax_types.spines["top"].set_visible(False)
    ax_types.spines["right"].set_visible(False)
    ax_types.spines["bottom"].set_edgecolor("#1a2a3a")
    ax_types.spines["left"].set_edgecolor("#1a2a3a")
    ax_types.xaxis.set_visible(False)

    # ── Header ──
    hours_label = f"Past {hours}h" if hours > 0 else "All Data"
    period = f"{info['earliest'][:10]} \u2192 {info['latest'][:10]}"
    fig.text(0.02, 0.97,
             "Strait of Hormuz \u2014 Vessel Traffic Density",
             fontsize=24, fontweight="bold", color=TEXT_PRIMARY, va="top",
             path_effects=[pe.withStroke(linewidth=3, foreground=BG)])
    fig.text(0.02, 0.935,
             f"{hours_label}  \u2502  {len(positions):,} clean positions  \u2502  "
             f"{info['unique']} unique ships  \u2502  {period}",
             fontsize=12, color=TEXT_SECONDARY, va="top")

    # Key finding badges
    fig.text(0.55, 0.97, "STRAIT TRANSIT: 0", fontsize=16, fontweight="bold",
             color=WARN, va="top",
             path_effects=[pe.withStroke(linewidth=2, foreground=BG)])
    fig.text(0.55, 0.94,
             f"{info['anomaly']:,} anomalous positions excluded (AIS speed \u2265 40 kn)",
             fontsize=10, color="#aa5555", va="top")

    # Attribution
    fig.text(0.99, 0.005,
             "Data: aisstream.io (terrestrial AIS)  \u2502  "
             "github.com/yasumorishima/hormuz-ship-tracker",
             fontsize=8, color="#334455", ha="right", va="bottom")

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
