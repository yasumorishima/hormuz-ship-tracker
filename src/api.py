"""FastAPI endpoints for the ship tracker + analytics."""

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from analytics import (
    ANCHORAGE_ZONES,
    CRISIS_TIMELINE,
    GATE_A,
    GATE_B,
    GATES,
    STRAIT_DANGER_ZONE,
    get_blockade_indicators,
    get_daily_summary,
    get_destination_distribution,
    get_flag_distribution,
    get_hourly_transits,
    get_transit_summary,
    get_vessel_states,
)
from land_filter import is_on_land

app = FastAPI(title="Hormuz Ship Tracker")

# AIS speed sentinel: 102.3 knots = "not available" in AIS protocol (10-bit 0x3FF)
AIS_SPEED_UNAVAILABLE = 102.3
# Threshold for suspicious speed (most merchant ships max ~25 kn)
SUSPICIOUS_SPEED_THRESHOLD = 40.0


def classify_anomalies(speed, lat, lon, prev_lat=None, prev_lon=None):
    """Classify AIS data quality issues for a position report.

    Returns a list of anomaly codes (empty = clean data):
      - "speed_unavailable": AIS speed = 102.3 (protocol "not available")
      - "speed_suspicious": Speed > 40 kn (likely receiver glitch or AIS mixup)
      - "position_jump": > 0.5 degree jump from previous position (AIS spoofing/glitch)
    """
    anomalies = []
    if speed is not None and speed >= AIS_SPEED_UNAVAILABLE:
        anomalies.append("speed_unavailable")
    elif speed is not None and speed >= SUSPICIOUS_SPEED_THRESHOLD:
        anomalies.append("speed_suspicious")
    if prev_lat is not None and prev_lon is not None:
        if abs(lat - prev_lat) > 0.5 or abs(lon - prev_lon) > 0.5:
            anomalies.append("position_jump")
    return anomalies
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_PATH = "/app/data/ais.db"

SHIP_TYPE_LABELS = {
    range(20, 30): "WIG",
    range(30, 36): "Fishing/Towing/Dredging",
    range(36, 40): "Military/Sailing/Pleasure",
    range(40, 50): "HSC",
    range(60, 70): "Passenger",
    range(70, 80): "Cargo",
    range(80, 90): "Tanker",
    range(90, 100): "Other",
}


def get_ship_type_label(type_code: int | None) -> str:
    """Convert AIS ship type code to human-readable label."""
    if type_code is None:
        return "Unknown"
    for r, label in SHIP_TYPE_LABELS.items():
        if type_code in r:
            return label
    return "Unknown"


# ── Core endpoints ──

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the live map + analytics dashboard."""
    return templates.TemplateResponse("map.html", {"request": request})


@app.get("/api/latest")
async def latest_positions():
    """Return the latest position for each vessel (last 30 min)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("""
            SELECT mmsi, latitude, longitude, speed, course, heading,
                   ship_name, ship_type, destination, flag, timestamp,
                   length, width
            FROM positions
            WHERE id IN (
                SELECT MAX(id) FROM positions
                WHERE received_at > datetime('now', '-30 minutes')
                GROUP BY mmsi
            )
        """)
    vessels = []
    anomaly_count = 0
    for r in rows:
        if is_on_land(r["latitude"], r["longitude"]):
            continue
        anomalies = classify_anomalies(r["speed"], r["latitude"], r["longitude"])
        if anomalies:
            anomaly_count += 1
        vessels.append({
            "mmsi": r["mmsi"],
            "lat": r["latitude"],
            "lon": r["longitude"],
            "speed": r["speed"],
            "course": r["course"],
            "heading": r["heading"],
            "name": r["ship_name"] or f"MMSI:{r['mmsi']}",
            "type": get_ship_type_label(r["ship_type"]),
            "type_code": r["ship_type"],
            "destination": r["destination"],
            "flag": r["flag"],
            "timestamp": r["timestamp"],
            "length": r["length"],
            "width": r["width"],
            "anomalies": anomalies,
        })
    return {"vessels": vessels, "count": len(vessels), "anomaly_count": anomaly_count}


@app.get("/api/tracks/{mmsi}")
async def vessel_track(mmsi: int, hours: int = 6):
    """Return position history for a specific vessel."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT latitude, longitude, speed, course, timestamp
            FROM positions
            WHERE mmsi = ?
              AND received_at > datetime('now', ? || ' hours')
            ORDER BY timestamp
            """,
            (mmsi, f"-{hours}"),
        )
    return {
        "mmsi": mmsi,
        "points": [
            {
                "lat": r["latitude"],
                "lon": r["longitude"],
                "speed": r["speed"],
                "course": r["course"],
                "ts": r["timestamp"],
            }
            for r in rows
            if not is_on_land(r["latitude"], r["longitude"])
        ],
    }


@app.get("/api/stats")
async def stats():
    """Return basic statistics."""
    async with aiosqlite.connect(DB_PATH) as db:
        total_records = (await db.execute_fetchall("SELECT COUNT(*) FROM positions"))[0][0]
        unique_vessels = (
            await db.execute_fetchall(
                "SELECT COUNT(DISTINCT mmsi) FROM positions WHERE received_at > datetime('now', '-1 hour')"
            )
        )[0][0]
        type_counts = await db.execute_fetchall("""
            SELECT ship_type, COUNT(DISTINCT mmsi) as cnt
            FROM positions
            WHERE received_at > datetime('now', '-1 hour')
            GROUP BY ship_type
            ORDER BY cnt DESC
        """)
    return {
        "total_records": total_records,
        "active_vessels_1h": unique_vessels,
        "vessel_types": [
            {"type": get_ship_type_label(row[0]), "count": row[1]} for row in type_counts
        ],
    }


# ── Analytics endpoints ──

@app.get("/api/analytics/transits")
async def api_transits(hours: int = 24, gate: str | None = None):
    """Transit events. Optional gate filter: 'Strait of Hormuz', 'Dubai / Jebel Ali Approach', etc."""
    return await get_transit_summary(hours, gate)


@app.get("/api/analytics/hourly")
async def api_hourly_transits(hours: int = 48, gate: str | None = None):
    """Hourly transit counts for charting. Optional gate filter."""
    return {"hours": hours, "data": await get_hourly_transits(hours, gate)}


@app.get("/api/analytics/states")
async def api_vessel_states():
    """Current vessel state classification (anchored/transiting/etc)."""
    return await get_vessel_states()


@app.get("/api/analytics/flags")
async def api_flags(hours: int = 24):
    """Flag state distribution."""
    return {"hours": hours, "data": await get_flag_distribution(hours)}


@app.get("/api/analytics/destinations")
async def api_destinations(hours: int = 24):
    """Destination distribution."""
    return {"hours": hours, "data": await get_destination_distribution(hours)}


@app.get("/api/analytics/gate")
async def api_gate_info():
    """All gate line coordinates, anchorage zones, danger zone, and crisis timeline."""
    return {
        "gates": {
            name: {
                "a": {"lat": g["a"][0], "lon": g["a"][1]},
                "b": {"lat": g["b"][0], "lon": g["b"][1]},
                "description": g.get("description", ""),
            }
            for name, g in GATES.items()
        },
        "anchorage_zones": {
            name: {
                "lat": z["lat"],
                "lon": z["lon"],
                "radius_nm": z["radius_nm"],
            }
            for name, z in ANCHORAGE_ZONES.items()
        },
        "danger_zone": [{"lat": p[0], "lon": p[1]} for p in STRAIT_DANGER_ZONE],
        "crisis_timeline": CRISIS_TIMELINE,
    }


@app.get("/api/analytics/blockade")
async def api_blockade():
    """Blockade impact indicators — waiting fleet, anchored ratio, strait status."""
    return await get_blockade_indicators()


@app.get("/api/analytics/data-quality")
async def api_data_quality():
    """AIS data quality summary — anomaly counts and explanations."""
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await db.execute_fetchall("SELECT COUNT(*) FROM positions"))[0][0]

        speed_unavailable = (await db.execute_fetchall(
            "SELECT COUNT(*) FROM positions WHERE speed >= 102.0"
        ))[0][0]

        speed_suspicious = (await db.execute_fetchall(
            "SELECT COUNT(*) FROM positions WHERE speed >= 40.0 AND speed < 102.0"
        ))[0][0]

        # Ships with wide position jumps (likely AIS glitches)
        jump_ships = await db.execute_fetchall("""
            SELECT ship_name, mmsi, flag, COUNT(*) as glitch_count
            FROM positions
            WHERE speed >= 40.0
            GROUP BY mmsi
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """)

    clean = total - speed_unavailable - speed_suspicious
    clean_pct = (clean / total * 100) if total > 0 else 0

    return {
        "total_positions": total,
        "clean_positions": clean,
        "clean_percentage": round(clean_pct, 1),
        "anomalies": {
            "speed_unavailable": {
                "count": speed_unavailable,
                "description": "AIS speed = 102.3 kn (protocol sentinel for 'not available'). "
                               "Occurs when vessel GPS is offline or AIS transmitter error.",
            },
            "speed_suspicious": {
                "count": speed_suspicious,
                "description": "Speed 40-102 kn, likely AIS receiver glitch or signal mixup. "
                               "Most merchant vessels max ~25 kn. Common near coastal AIS stations.",
            },
        },
        "known_glitch_sources": [
            {
                "name": r["ship_name"] or f"MMSI:{r['mmsi']}",
                "mmsi": r["mmsi"],
                "flag": r["flag"],
                "glitch_positions": r["glitch_count"],
            }
            for r in jump_ships
        ],
        "notes": [
            "AIS is a self-reporting system — vessels control what they broadcast",
            "Terrestrial AIS receivers cannot cover mid-strait (30+ nm offshore)",
            "Speed = 102.3 kn is the AIS protocol 'not available' value (0x3FF in 10-bit field)",
            "Multiple vessels appearing at the same location with ~48 kn often indicates "
            "a single receiver decoding error affecting all signals",
        ],
    }


@app.get("/api/analytics/summary")
async def api_daily_summary():
    """Comprehensive daily summary."""
    return await get_daily_summary()


# ── Replay & transit detail endpoints ──

@app.get("/api/replay/frames")
async def api_replay_frames(hours: int = 96, interval: int = 30):
    """Return position data bucketed by time for animated replay.

    Each frame contains latest position per vessel within the interval window.
    Optimized for Leaflet-based client-side animation.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Get data range
        row = await db.execute_fetchall(
            "SELECT MIN(timestamp), MAX(timestamp) FROM positions"
        )
        if not row or not row[0][0]:
            return {"frames": [], "meta": {}}

        earliest = row[0][0]
        latest = row[0][1]

        from datetime import datetime, timedelta

        end_dt = datetime.fromisoformat(latest)
        start_dt = max(
            datetime.fromisoformat(earliest),
            end_dt - timedelta(hours=hours),
        )

        # Build time windows and query positions for each
        frames = []
        current = start_dt
        while current <= end_dt:
            next_ts = current + timedelta(minutes=interval)
            rows = await db.execute_fetchall("""
                SELECT mmsi, latitude, longitude, speed, course,
                       ship_name, ship_type, flag, destination, timestamp
                FROM positions
                WHERE id IN (
                    SELECT MAX(id) FROM positions
                    WHERE timestamp >= ? AND timestamp < ?
                    GROUP BY mmsi
                )
            """, (current.isoformat(), next_ts.isoformat()))

            vessels = []
            for r in rows:
                if is_on_land(r["latitude"], r["longitude"]):
                    continue
                vessels.append({
                    "mmsi": r["mmsi"],
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                    "speed": r["speed"],
                    "course": r["course"],
                    "name": r["ship_name"] or f"MMSI:{r['mmsi']}",
                    "type": get_ship_type_label(r["ship_type"]),
                    "flag": r["flag"],
                    "dest": r["destination"],
                })
            frames.append({
                "ts": current.isoformat(),
                "vessels": vessels,
            })
            current = next_ts

    return {
        "frames": frames,
        "meta": {
            "earliest": earliest,
            "latest": latest,
            "hours": hours,
            "interval_min": interval,
            "total_frames": len(frames),
        },
    }


@app.get("/api/analytics/transit-ships")
async def api_transit_ships(hours: int = 0, gate: str | None = None):
    """Detailed list of ships that actually crossed gate lines.

    Returns ship name, type, flag, destination, speed at crossing, and direction
    for each transit event. hours=0 means all time.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        query = """
            SELECT t.mmsi, t.gate_name, t.direction, t.crossed_at,
                   t.speed, t.ship_name, t.ship_type, t.flag, t.destination,
                   t.latitude, t.longitude
            FROM transit_events t
        """
        conditions = []
        params = []

        if hours > 0:
            conditions.append("t.crossed_at > datetime('now', ? || ' hours')")
            params.append(f"-{hours}")
        if gate:
            conditions.append("t.gate_name = ?")
            params.append(gate)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY t.crossed_at DESC"

        rows = await db.execute_fetchall(query, params)

        ships = []
        for r in rows:
            ships.append({
                "mmsi": r["mmsi"],
                "name": r["ship_name"] or f"MMSI:{r['mmsi']}",
                "type": get_ship_type_label(r["ship_type"]),
                "type_code": r["ship_type"],
                "flag": r["flag"],
                "destination": r["destination"],
                "gate": r["gate_name"],
                "direction": r["direction"],
                "crossed_at": r["crossed_at"],
                "speed_kn": r["speed"],
                "lat": r["latitude"],
                "lon": r["longitude"],
            })

        # Summary stats
        unique_ships = len({s["mmsi"] for s in ships})
        by_gate = {}
        by_type = {}
        by_flag = {}
        for s in ships:
            by_gate[s["gate"]] = by_gate.get(s["gate"], 0) + 1
            by_type[s["type"]] = by_type.get(s["type"], 0) + 1
            if s["flag"]:
                by_flag[s["flag"]] = by_flag.get(s["flag"], 0) + 1

    return {
        "ships": ships,
        "summary": {
            "total_transits": len(ships),
            "unique_ships": unique_ships,
            "by_gate": dict(sorted(by_gate.items(), key=lambda x: -x[1])),
            "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
            "by_flag": dict(sorted(by_flag.items(), key=lambda x: -x[1])[:20]),
        },
    }


@app.get("/api/ship/{mmsi}/profile")
async def api_ship_profile(mmsi: int):
    """Full profile of a specific ship — name, type, flag, all positions, transits."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Latest info
        info_rows = await db.execute_fetchall("""
            SELECT ship_name, ship_type, flag, destination, length, width, draught
            FROM positions WHERE mmsi = ? ORDER BY id DESC LIMIT 1
        """, (mmsi,))
        if not info_rows:
            return {"error": "Ship not found"}

        info = info_rows[0]

        # Position history
        positions = await db.execute_fetchall("""
            SELECT latitude, longitude, speed, course, timestamp
            FROM positions WHERE mmsi = ? ORDER BY timestamp
        """, (mmsi,))

        # Transit events
        transits = await db.execute_fetchall("""
            SELECT gate_name, direction, crossed_at, speed
            FROM transit_events WHERE mmsi = ? ORDER BY crossed_at
        """, (mmsi,))

    return {
        "mmsi": mmsi,
        "name": info["ship_name"] or f"MMSI:{mmsi}",
        "type": get_ship_type_label(info["ship_type"]),
        "type_code": info["ship_type"],
        "flag": info["flag"],
        "destination": info["destination"],
        "length_m": info["length"],
        "width_m": info["width"],
        "draught_m": info["draught"],
        "positions": [
            {"lat": p["latitude"], "lon": p["longitude"],
             "speed": p["speed"], "course": p["course"], "ts": p["timestamp"]}
            for p in positions
        ],
        "transits": [
            {"gate": t["gate_name"], "direction": t["direction"],
             "crossed_at": t["crossed_at"], "speed_kn": t["speed"]}
            for t in transits
        ],
        "total_positions": len(positions),
        "first_seen": positions[0]["timestamp"] if positions else None,
        "last_seen": positions[-1]["timestamp"] if positions else None,
    }


@app.get("/replay", response_class=HTMLResponse)
async def replay_page(request: Request):
    """Serve the animated replay page."""
    return templates.TemplateResponse("replay.html", {"request": request})
