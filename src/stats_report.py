"""Generate STATS.md — auto-updated period statistics for the README.

Outputs a Markdown file with collection period stats, daily breakdown,
hourly traffic pattern, transit summary, top ships, and data quality.

Usage:
    python src/stats_report.py [--db /app/data/ais.db] [--output /repo/docs/STATS.md]
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = "/app/data/ais.db"
OUTPUT_PATH = "/repo/docs/STATS.md"

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

FLAG_NAMES = {
    "PA": "Panama", "AE": "UAE", "MH": "Marshall Is.", "LR": "Liberia",
    "KN": "St. Kitts", "SG": "Singapore", "HK": "Hong Kong", "KM": "Comoros",
    "VC": "St. Vincent", "NL": "Netherlands", "GR": "Greece", "TH": "Thailand",
    "KY": "Cayman", "KR": "S. Korea", "SA": "Saudi", "CN": "China",
    "IN": "India", "KW": "Kuwait", "PK": "Pakistan", "IR": "Iran",
    "BS": "Bahamas", "MT": "Malta", "GB": "UK", "BH": "Bahrain",
}


def get_type_label(code):
    if code is None:
        return "Unknown"
    for r, label in SHIP_TYPE_RANGES.items():
        if code in r:
            return label
    return "Unknown"


def generate_stats(db_path=DB_PATH, output_path=OUTPUT_PATH):
    conn = sqlite3.connect(db_path)
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── Basic stats ──
    total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    clean = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE speed IS NULL OR speed < 40"
    ).fetchone()[0]
    anomaly = total - clean
    ships = conn.execute("SELECT COUNT(DISTINCT mmsi) FROM positions").fetchone()[0]
    earliest = conn.execute("SELECT MIN(timestamp) FROM positions").fetchone()[0]
    latest = conn.execute("SELECT MAX(timestamp) FROM positions").fetchone()[0]

    if not earliest or not latest:
        print("No data available.")
        return

    days = max(
        (datetime.fromisoformat(latest) - datetime.fromisoformat(earliest)).total_seconds() / 86400,
        0.01,
    )

    # ── Daily breakdown ──
    daily = conn.execute("""
        SELECT DATE(timestamp) as day,
               COUNT(*) as cnt,
               COUNT(DISTINCT mmsi) as ships
        FROM positions GROUP BY DATE(timestamp) ORDER BY day
    """).fetchall()

    # ── Hourly pattern ──
    hourly = conn.execute("""
        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour,
               COUNT(*) / MAX(1, COUNT(DISTINCT DATE(timestamp))) as avg_per_hour
        FROM positions WHERE speed IS NULL OR speed < 40
        GROUP BY hour ORDER BY hour
    """).fetchall()
    max_hourly = max((h[1] for h in hourly), default=1)

    # ── Transits ──
    transits_daily = conn.execute("""
        SELECT DATE(crossed_at) as day, gate_name, direction, COUNT(*) as cnt
        FROM transit_events
        GROUP BY day, gate_name, direction ORDER BY day
    """).fetchall()
    total_transits = conn.execute("SELECT COUNT(*) FROM transit_events").fetchone()[0]
    strait_transits = conn.execute(
        "SELECT COUNT(*) FROM transit_events WHERE gate_name = 'Strait of Hormuz'"
    ).fetchone()[0]

    # ── Top flags ──
    flags = conn.execute("""
        SELECT flag, COUNT(DISTINCT mmsi) as cnt
        FROM positions WHERE flag IS NOT NULL AND flag != ''
        GROUP BY flag ORDER BY cnt DESC LIMIT 10
    """).fetchall()

    # ── Top ship types ──
    types_raw = conn.execute("""
        SELECT ship_type, COUNT(DISTINCT mmsi) as cnt
        FROM positions GROUP BY ship_type ORDER BY cnt DESC
    """).fetchall()
    type_agg = {}
    for code, cnt in types_raw:
        t = get_type_label(code)
        type_agg[t] = type_agg.get(t, 0) + cnt
    types_sorted = sorted(
        ((t, c) for t, c in type_agg.items() if t != "Unknown"),
        key=lambda x: -x[1],
    )

    # ── Top tracked ships ──
    top_ships = conn.execute("""
        SELECT ship_name, mmsi, flag, ship_type, COUNT(*) as cnt,
               MIN(timestamp) as first_seen, MAX(timestamp) as last_seen
        FROM positions
        WHERE ship_name IS NOT NULL AND ship_name != ''
        GROUP BY mmsi ORDER BY cnt DESC LIMIT 15
    """).fetchall()

    # ── Destinations ──
    destinations = conn.execute("""
        SELECT destination, COUNT(DISTINCT mmsi) as cnt
        FROM positions
        WHERE destination IS NOT NULL AND destination != ''
        GROUP BY destination ORDER BY cnt DESC LIMIT 10
    """).fetchall()

    conn.close()

    # ── Build Markdown ──
    lines = []
    lines.append("# Collection Statistics")
    lines.append("")
    lines.append(f"*Auto-updated: {now_utc}*")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Collection period | {earliest[:10]} \u2192 {latest[:10]} ({days:.1f} days) |")
    lines.append(f"| Total positions | {total:,} |")
    lines.append(f"| Clean positions | {clean:,} ({clean * 100 // total}%) |")
    lines.append(f"| Anomalous positions | {anomaly:,} ({anomaly * 100 // total}%) |")
    lines.append(f"| Unique vessels | {ships} |")
    lines.append(f"| Avg positions/day | {total / days:,.0f} |")
    lines.append(f"| Strait of Hormuz transits | {strait_transits} |")
    lines.append(f"| Total gate crossings | {total_transits} |")
    lines.append("")

    # Daily
    lines.append("## Daily Breakdown")
    lines.append("")
    lines.append("| Date | Positions | Vessels |")
    lines.append("|---|---:|---:|")
    for day, cnt, s in daily:
        lines.append(f"| {day} | {cnt:,} | {s} |")
    lines.append("")

    # Hourly
    lines.append("## Hourly Traffic Pattern (UTC)")
    lines.append("")
    lines.append("Average positions per hour across all days (clean data only):")
    lines.append("")
    lines.append("```")
    for hour, avg in hourly:
        bar_len = int(avg / max_hourly * 40)
        bar = "\u2588" * bar_len
        lines.append(f"  {hour:02d}:00  {avg:4d}  {bar}")
    lines.append("```")
    lines.append("")

    # Transits
    lines.append("## Gate Crossings by Day")
    lines.append("")
    if transits_daily:
        lines.append("| Date | Gate | Direction | Count |")
        lines.append("|---|---|---|---:|")
        for day, gate, direction, cnt in transits_daily:
            lines.append(f"| {day} | {gate} | {direction} | {cnt} |")
    else:
        lines.append("No transit events recorded yet.")
    lines.append("")

    # Flags
    lines.append("## Top Flag States")
    lines.append("")
    lines.append("| Flag | Country | Vessels |")
    lines.append("|---|---|---:|")
    for flag, cnt in flags:
        country = FLAG_NAMES.get(flag, flag)
        lines.append(f"| {flag} | {country} | {cnt} |")
    lines.append("")

    # Types
    lines.append("## Vessel Types")
    lines.append("")
    lines.append("| Type | Vessels |")
    lines.append("|---|---:|")
    for t, c in types_sorted:
        lines.append(f"| {t} | {c} |")
    lines.append("")

    # Top ships
    lines.append("## Most Tracked Vessels")
    lines.append("")
    lines.append("| Ship Name | Flag | Type | Positions | First Seen | Last Seen |")
    lines.append("|---|---|---|---:|---|---|")
    for name, mmsi, flag, stype, cnt, first, last in top_ships:
        tl = get_type_label(stype)
        fl = flag or "--"
        lines.append(f"| {name} | {fl} | {tl} | {cnt:,} | {first[:10]} | {last[:10]} |")
    lines.append("")

    # Destinations
    lines.append("## Top Destinations")
    lines.append("")
    lines.append("| Destination | Vessels |")
    lines.append("|---|---:|")
    for dest, cnt in destinations:
        lines.append(f"| {dest} | {cnt} |")
    lines.append("")

    lines.append("---")
    lines.append(f"*Data source: [aisstream.io](https://aisstream.io/) (terrestrial AIS)*")

    # Write
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Stats report saved: {output_path}")
    print(f"  Period: {earliest[:10]} → {latest[:10]} ({days:.1f} days)")
    print(f"  {total:,} positions, {ships} ships, {total_transits} transits")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate STATS.md")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--output", default=OUTPUT_PATH)
    args = parser.parse_args()
    generate_stats(db_path=args.db, output_path=args.output)
