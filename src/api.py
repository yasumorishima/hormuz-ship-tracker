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
    for r in rows:
        if is_on_land(r["latitude"], r["longitude"]):
            continue
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
        })
    return {"vessels": vessels, "count": len(vessels)}


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


@app.get("/api/analytics/summary")
async def api_daily_summary():
    """Comprehensive daily summary."""
    return await get_daily_summary()
