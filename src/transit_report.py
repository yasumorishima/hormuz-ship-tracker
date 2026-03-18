"""Generate a visual transit report showing ships that crossed gate lines.

Produces a summary image with:
  - Map showing all transit crossing points
  - Table of ships with details (name, type, flag, speed, destination)
  - Key findings (0 Strait crossings, etc.)

Usage:
    python src/transit_report.py [--output transit_report.png]
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
from shapely.geometry import shape  # noqa: E402

DB_PATH = "/app/data/ais.db"
OUTPUT_DIR = Path("/app/data")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GEOJSON_PATH = _DATA_DIR / "land_mask.geojson"

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

GATE_LINES = [
    {"name": "Strait of Hormuz", "lats": [26.05, 26.65], "lons": [56.50, 56.10]},
    {"name": "Dubai / Jebel Ali", "lats": [25.00, 25.35], "lons": [55.20, 55.20]},
    {"name": "Fujairah", "lats": [25.00, 25.30], "lons": [56.50, 56.50]},
]

# Country code → flag emoji / name for display
COUNTRY_NAMES = {
    "AE": "UAE", "NL": "Netherlands", "PA": "Panama", "MH": "Marshall Is.",
    "LR": "Liberia", "SG": "Singapore", "GB": "UK", "TH": "Thailand",
    "SA": "Saudi Arabia", "KW": "Kuwait", "BH": "Bahrain", "IN": "India",
    "CN": "China", "KR": "South Korea", "PK": "Pakistan", "IR": "Iran",
    "VC": "St. Vincent", "KN": "St. Kitts", "KY": "Cayman Is.",
    "MV": "Maldives", "MT": "Malta", "KM": "Comoros", "BS": "Bahamas",
}


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
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def generate_transit_report(db_path=DB_PATH, output_dir=OUTPUT_DIR,
                            filename="transit_report.png"):
    """Generate a visual transit report."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all transit events
    transits = conn.execute("""
        SELECT mmsi, gate_name, direction, crossed_at, speed,
               ship_name, ship_type, flag, destination, latitude, longitude
        FROM transit_events
        ORDER BY crossed_at
    """).fetchall()

    # Get data range
    stats = conn.execute("""
        SELECT COUNT(*) as total, COUNT(DISTINCT mmsi) as ships,
               MIN(timestamp) as earliest, MAX(timestamp) as latest
        FROM positions
    """).fetchone()

    # Get Karachi / Pakistan-bound ships
    karachi_ships = conn.execute("""
        SELECT DISTINCT mmsi, ship_name, flag, destination, COUNT(*) as pts
        FROM positions
        WHERE destination LIKE '%KARACHI%' OR destination LIKE '%Karachi%'
           OR flag = 'PK'
        GROUP BY mmsi
    """).fetchall()

    conn.close()

    # ── Figure: 2 panels (map + table) ──
    fig = plt.figure(figsize=(20, 12), facecolor="#0a0a1a")

    # Left panel: Map with transit points
    ax_map = fig.add_axes([0.02, 0.08, 0.55, 0.85])
    ax_map.set_facecolor("#0d1b2a")

    # Focus on strait area + Dubai
    ax_map.set_xlim(54.0, 58.0)
    ax_map.set_ylim(24.0, 27.5)
    ax_map.set_aspect("equal")

    # Grid
    for lon_v in range(54, 59):
        ax_map.axvline(lon_v, color="#141e2e", linewidth=0.3, zorder=1)
    for lat_v in range(24, 28):
        ax_map.axhline(lat_v, color="#141e2e", linewidth=0.3, zorder=1)

    # Coastline
    coastlines = _load_coastline_polygons()
    for poly in coastlines:
        xs, ys = poly.exterior.xy
        ax_map.fill(xs, ys, facecolor="#111822", edgecolor="#2a3a4a",
                    linewidth=0.8, zorder=2)

    # Gate lines
    for gate in GATE_LINES:
        ax_map.plot(gate["lons"], gate["lats"], color="#ff1744", linewidth=3,
                    linestyle=(0, (6, 4)), zorder=6, alpha=0.9)
        mid_lat = sum(gate["lats"]) / 2
        mid_lon = sum(gate["lons"]) / 2
        ax_map.text(mid_lon + 0.1, mid_lat, gate["name"],
                    fontsize=10, color="#ff1744", fontweight="bold",
                    ha="left", va="center", zorder=6,
                    path_effects=[pe.withStroke(linewidth=2, foreground="#0a0a1a")])

    # Transit crossing points
    dir_colors = {"INBOUND": "#4caf50", "OUTBOUND": "#ff9800"}
    for t in transits:
        if t["latitude"] and t["longitude"]:
            color = dir_colors.get(t["direction"], "#888888")
            ax_map.scatter(
                t["longitude"], t["latitude"],
                s=80, c=color, marker="o", edgecolors="white",
                linewidths=1, alpha=0.8, zorder=7,
            )

    # Labels
    text_effects = [pe.withStroke(linewidth=3, foreground="#0a0a1a")]
    labels = [
        (26.25, 56.25, "Strait of\nHormuz", 14, "#4fc3f7"),
        (27.00, 56.60, "Bandar Abbas", 9, "#888888"),
        (25.20, 55.30, "Dubai", 11, "#888888"),
        (25.00, 56.40, "Fujairah", 9, "#888888"),
        (26.50, 54.50, "IRAN", 14, "#cccccc"),
        (24.50, 55.50, "UAE", 14, "#cccccc"),
        (24.50, 57.50, "OMAN", 14, "#cccccc"),
    ]
    for lat, lon, label, size, color in labels:
        ax_map.text(lon, lat, label, fontsize=size, color=color, fontweight="bold",
                    ha="center", va="center", zorder=5, path_effects=text_effects)

    # Legend
    in_patch = mpatches.Patch(color="#4caf50", label="INBOUND")
    out_patch = mpatches.Patch(color="#ff9800", label="OUTBOUND")
    ax_map.legend(handles=[in_patch, out_patch], loc="lower left", fontsize=12,
                  facecolor="#0d1b2aee", edgecolor="#2a3a4a", labelcolor="#cccccc")

    ax_map.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}\u00b0E"))
    ax_map.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}\u00b0N"))
    ax_map.tick_params(colors="#555555", labelsize=10, length=0)
    for spine in ax_map.spines.values():
        spine.set_visible(False)

    # ── Right panel: Transit table ──
    ax_table = fig.add_axes([0.60, 0.08, 0.38, 0.85])
    ax_table.axis("off")

    # Title
    fig.text(0.02, 0.97,
             "Strait of Hormuz  Transit Report",
             fontsize=20, fontweight="bold", color="#e0e0e0", va="top",
             path_effects=[pe.withStroke(linewidth=3, foreground="#0a0a1a")])

    period = f"{stats['earliest'][:10]} → {stats['latest'][:10]}"
    fig.text(0.02, 0.935,
             f"{stats['total']:,} positions  |  {stats['ships']} ships  |  {period}",
             fontsize=11, color="#888888", va="top")

    # Key findings
    strait_transits = [t for t in transits if t["gate_name"] == "Strait of Hormuz"]
    dubai_transits = [t for t in transits if "Dubai" in t["gate_name"]]
    fujairah_transits = [t for t in transits if "Fujairah" in t["gate_name"]]

    y_pos = 0.95
    findings = [
        ("KEY FINDINGS", "#4fc3f7", 14),
        (f"Strait of Hormuz crossings: {len(strait_transits)} "
         "(terrestrial AIS — mid-strait blind spot)", "#ff5252", 12),
        (f"Dubai / Jebel Ali gate: {len(dubai_transits)} transits", "#e0e0e0", 12),
        (f"Fujairah gate: {len(fujairah_transits)} transits", "#e0e0e0", 12),
        (f"Karachi-bound ships detected: {len(karachi_ships)}", "#e0e0e0", 12),
    ]
    for text, color, size in findings:
        ax_table.text(0.02, y_pos, text, fontsize=size, color=color,
                      fontweight="bold", va="top", transform=ax_table.transAxes)
        y_pos -= 0.05

    # Transit table
    y_pos -= 0.03
    ax_table.text(0.02, y_pos, "GATE CROSSINGS (all gates)",
                  fontsize=13, color="#4fc3f7", fontweight="bold",
                  va="top", transform=ax_table.transAxes)
    y_pos -= 0.04

    # Header
    header = f"{'Ship Name':<18s} {'Type':<10s} {'Flag':<4s} {'Dir':<4s} {'Speed':>6s} {'Gate':<12s}"
    ax_table.text(0.02, y_pos, header,
                  fontsize=9, color="#888888", fontfamily="monospace",
                  va="top", transform=ax_table.transAxes)
    y_pos -= 0.005
    ax_table.plot([0.02, 0.98], [y_pos, y_pos], color="#2a3a4a",
                  linewidth=0.5, transform=ax_table.transAxes, clip_on=False)
    y_pos -= 0.02

    # Rows
    seen_mmsi_gate = set()
    for t in sorted(transits, key=lambda x: x["crossed_at"]):
        key = (t["mmsi"], t["gate_name"])
        if key in seen_mmsi_gate:
            continue  # show each ship once per gate
        seen_mmsi_gate.add(key)

        name = (t["ship_name"] or f"MMSI:{t['mmsi']}")[:17]
        ship_type = get_ship_type_label(t["ship_type"])[:9]
        flag = (t["flag"] or "--")[:3]
        direction = "IN" if t["direction"] == "INBOUND" else "OUT"
        speed = f"{t['speed']:.0f}kn" if t["speed"] and t["speed"] < 100 else "--"
        gate_short = t["gate_name"].replace(" Approach", "").replace("Dubai / ", "")[:11]

        row = f"{name:<18s} {ship_type:<10s} {flag:<4s} {direction:<4s} {speed:>6s} {gate_short:<12s}"
        dir_color = "#4caf50" if direction == "IN" else "#ffb74d"
        ax_table.text(0.02, y_pos, row,
                      fontsize=9, color=dir_color, fontfamily="monospace",
                      va="top", transform=ax_table.transAxes)
        y_pos -= 0.025

        if y_pos < 0.05:
            ax_table.text(0.02, y_pos, f"  ... and {len(transits) - len(seen_mmsi_gate)} more",
                          fontsize=9, color="#666666", fontfamily="monospace",
                          va="top", transform=ax_table.transAxes)
            break

    # Karachi section
    if karachi_ships and y_pos > 0.15:
        y_pos -= 0.04
        ax_table.text(0.02, y_pos, "KARACHI-BOUND / PAKISTAN SHIPS",
                      fontsize=13, color="#4fc3f7", fontweight="bold",
                      va="top", transform=ax_table.transAxes)
        y_pos -= 0.04
        for s in karachi_ships:
            name = (s["ship_name"] or f"MMSI:{s['mmsi']}")[:20]
            flag = COUNTRY_NAMES.get(s["flag"], s["flag"] or "--")
            dest = s["destination"] or "--"
            ax_table.text(0.02, y_pos,
                          f"{name:<20s}  {flag:<15s}  → {dest}  ({s['pts']} positions)",
                          fontsize=9, color="#e0e0e0", fontfamily="monospace",
                          va="top", transform=ax_table.transAxes)
            y_pos -= 0.025

    # Attribution
    fig.text(0.99, 0.01,
             "Data: aisstream.io  |  github.com/yasumorishima/hormuz-ship-tracker",
             fontsize=8, color="#444444", ha="right", va="bottom")

    # Save
    output_path = output_dir / filename
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Transit report saved: {output_path} ({file_size_mb:.1f} MB)")
    print(f"  Strait crossings: {len(strait_transits)}")
    print(f"  Dubai gate: {len(dubai_transits)}")
    print(f"  Fujairah gate: {len(fujairah_transits)}")
    print(f"  Karachi-bound: {len(karachi_ships)}")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate transit report")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--filename", default="transit_report.png")
    args = parser.parse_args()

    generate_transit_report(
        db_path=args.db,
        output_dir=Path(args.output_dir),
        filename=args.filename,
    )
