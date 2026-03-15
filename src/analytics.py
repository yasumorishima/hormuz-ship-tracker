"""Strait of Hormuz transit detection and maritime analytics engine.

Core capabilities:
  1. Gate-line crossing detection (IN/OUT through the Strait)
  2. Vessel state classification (anchored/slow/transiting)
  3. Anchorage zone identification
  4. Hourly/daily traffic aggregation
  5. Daily summary generation
"""

import asyncio
import logging
import math
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = "/app/data/ais.db"

# ── Virtual gate line across the Strait of Hormuz ──
# Ships INBOUND to the Gulf cross from east (Gulf of Oman) to west (Persian Gulf).
# Ships OUTBOUND from the Gulf cross from west to east.
# The gate runs from the Musandam Peninsula (Oman) to near Qeshm Island (Iran).
GATE_A = (26.05, 56.50)  # south end (Oman/Musandam) — (lat, lon)
GATE_B = (26.65, 56.10)  # north end (Iran/Qeshm)   — (lat, lon)

# ── Vessel speed states ──
SPEED_ANCHORED = 0.5    # knots
SPEED_SLOW = 3.0
SPEED_MANEUVERING = 8.0

# ── Known anchorage / waiting zones ──
# Each zone: center (lat, lon), radius in nautical miles
ANCHORAGE_ZONES = {
    "Fujairah Anchorage": {"lat": 25.15, "lon": 56.40, "radius_nm": 10},
    "Khor Fakkan": {"lat": 25.35, "lon": 56.40, "radius_nm": 5},
    "Dubai / Jebel Ali": {"lat": 25.05, "lon": 55.05, "radius_nm": 12},
    "Sharjah / Ajman": {"lat": 25.40, "lon": 55.45, "radius_nm": 6},
    "Bandar Abbas": {"lat": 27.15, "lon": 56.30, "radius_nm": 8},
    "Strait Waiting Area": {"lat": 26.30, "lon": 56.80, "radius_nm": 10},
    "Abu Dhabi": {"lat": 24.50, "lon": 54.40, "radius_nm": 10},
    "Ras Al Khaimah": {"lat": 25.80, "lon": 56.05, "radius_nm": 6},
    "Mina Al Ahmadi (Kuwait)": {"lat": 29.05, "lon": 48.20, "radius_nm": 8},
    "Ras Tanura (Saudi)": {"lat": 26.65, "lon": 50.15, "radius_nm": 6},
    "Doha (Qatar)": {"lat": 25.30, "lon": 51.55, "radius_nm": 8},
}


# ── Database setup ──

async def init_analytics_db():
    """Create analytics tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mmsi INTEGER NOT NULL,
                direction TEXT NOT NULL,
                crossed_at TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                speed REAL,
                ship_name TEXT,
                ship_type INTEGER,
                flag TEXT,
                destination TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_transit_crossed_at
            ON transit_events(crossed_at)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_transit_mmsi
            ON transit_events(mmsi)
        """)
        # Key-value store for analytics state
        await db.execute("""
            CREATE TABLE IF NOT EXISTS analytics_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.commit()
    logger.info("Analytics tables initialized")


# ── Geometry helpers ──

def _cross_product_2d(o: tuple, a: tuple, b: tuple) -> float:
    """2D cross product of vectors OA and OB. Positive = counter-clockwise."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def segments_intersect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    """Check if line segment p1-p2 intersects segment p3-p4."""
    d1 = _cross_product_2d(p3, p4, p1)
    d2 = _cross_product_2d(p3, p4, p2)
    d3 = _cross_product_2d(p1, p2, p3)
    d4 = _cross_product_2d(p1, p2, p4)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    R_NM = 3440.065  # Earth radius in nautical miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R_NM * 2 * math.asin(math.sqrt(a))


def determine_transit_direction(
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> str:
    """Determine if a vessel crossing the gate is INBOUND or OUTBOUND.

    The gate runs roughly NE-SW (from Oman at lower-right to Iran at upper-left).
    - INBOUND (into the Gulf): vessel moves from east (high lon) to west (low lon),
      i.e., from Gulf of Oman side to Persian Gulf side.
    - OUTBOUND: the reverse.

    We use the cross product relative to the gate vector to determine which
    side each point is on. Gate vector: A→B (south to north, Oman to Iran).
    """
    # Cross product of gate vector (A→B) with point vector (A→P)
    # Positive = point is to the left of A→B (east side = Gulf of Oman)
    # Negative = point is to the right of A→B (west side = Persian Gulf)
    side1 = _cross_product_2d(GATE_A, GATE_B, p1)
    side2 = _cross_product_2d(GATE_A, GATE_B, p2)

    if side1 > 0 and side2 < 0:
        # Started on east side (Oman), ended on west side (Gulf) = INBOUND
        return "INBOUND"
    elif side1 < 0 and side2 > 0:
        # Started on west side (Gulf), ended on east side (Oman) = OUTBOUND
        return "OUTBOUND"
    return "UNKNOWN"


# ── Transit detection ──

async def detect_transits(lookback_minutes: int = 10) -> int:
    """Scan recent positions for gate-line crossings.

    Returns the number of new transit events detected.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Get last processed time
        row = await db.execute_fetchall(
            "SELECT value FROM analytics_state WHERE key = 'last_transit_check'"
        )
        if row:
            since = row[0][0]
        else:
            since = "2000-01-01T00:00:00"

        # Get all positions since last check, grouped by vessel
        rows = await db.execute_fetchall(
            """
            SELECT mmsi, latitude, longitude, speed, received_at,
                   ship_name, ship_type, flag, destination
            FROM positions
            WHERE received_at > ?
            ORDER BY mmsi, received_at
            """,
            (since,),
        )

        if not rows:
            return 0

        # Also get the last known position BEFORE the window for each vessel
        # (to detect crossings that span the window boundary)
        mmsi_set = set(r["mmsi"] for r in rows)
        prev_positions: dict[int, dict] = {}
        for mmsi in mmsi_set:
            prev = await db.execute_fetchall(
                """
                SELECT latitude, longitude, speed, received_at,
                       ship_name, ship_type, flag, destination
                FROM positions
                WHERE mmsi = ? AND received_at <= ?
                ORDER BY received_at DESC
                LIMIT 1
                """,
                (mmsi, since),
            )
            if prev:
                p = prev[0]
                prev_positions[mmsi] = {
                    "lat": p["latitude"],
                    "lon": p["longitude"],
                    "speed": p["speed"],
                    "received_at": p["received_at"],
                    "ship_name": p["ship_name"],
                    "ship_type": p["ship_type"],
                    "flag": p["flag"],
                    "destination": p["destination"],
                }

        # Group positions by MMSI
        vessel_positions: dict[int, list[dict]] = {}
        for r in rows:
            mmsi = r["mmsi"]
            if mmsi not in vessel_positions:
                vessel_positions[mmsi] = []
                # Prepend previous position if available
                if mmsi in prev_positions:
                    vessel_positions[mmsi].append(prev_positions[mmsi])
            vessel_positions[mmsi].append({
                "lat": r["latitude"],
                "lon": r["longitude"],
                "speed": r["speed"],
                "received_at": r["received_at"],
                "ship_name": r["ship_name"],
                "ship_type": r["ship_type"],
                "flag": r["flag"],
                "destination": r["destination"],
            })

        # Detect crossings
        new_events = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        for mmsi, positions in vessel_positions.items():
            if len(positions) < 2:
                continue

            for i in range(len(positions) - 1):
                p1 = positions[i]
                p2 = positions[i + 1]

                # Skip if points are too far apart in time (>30 min gap)
                # Use received_at which is in ISO format
                try:
                    t1 = datetime.fromisoformat(p1["received_at"])
                    t2 = datetime.fromisoformat(p2["received_at"])
                    if (t2 - t1).total_seconds() > 1800:
                        continue
                except (ValueError, TypeError):
                    continue

                # Skip if both points are far from the gate (>30 nm)
                gate_center = (
                    (GATE_A[0] + GATE_B[0]) / 2,
                    (GATE_A[1] + GATE_B[1]) / 2,
                )
                d1 = haversine_nm(p1["lat"], p1["lon"], gate_center[0], gate_center[1])
                d2 = haversine_nm(p2["lat"], p2["lon"], gate_center[0], gate_center[1])
                if d1 > 40 and d2 > 40:
                    continue

                # Check if the track segment crosses the gate line
                if segments_intersect(
                    (p1["lat"], p1["lon"]),
                    (p2["lat"], p2["lon"]),
                    GATE_A,
                    GATE_B,
                ):
                    direction = determine_transit_direction(
                        (p1["lat"], p1["lon"]),
                        (p2["lat"], p2["lon"]),
                    )
                    if direction == "UNKNOWN":
                        continue

                    # Deduplicate: skip if same MMSI crossed in the last 6 hours
                    existing = await db.execute_fetchall(
                        """
                        SELECT id FROM transit_events
                        WHERE mmsi = ? AND crossed_at > datetime(?, '-6 hours')
                        """,
                        (mmsi, p2["received_at"]),
                    )
                    if existing:
                        continue

                    # Estimate crossing point (midpoint of segment)
                    cross_lat = (p1["lat"] + p2["lat"]) / 2
                    cross_lon = (p1["lon"] + p2["lon"]) / 2
                    cross_speed = p2["speed"] or p1["speed"]

                    await db.execute(
                        """
                        INSERT INTO transit_events
                        (mmsi, direction, crossed_at, latitude, longitude,
                         speed, ship_name, ship_type, flag, destination)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mmsi,
                            direction,
                            p2["received_at"],
                            cross_lat,
                            cross_lon,
                            cross_speed,
                            p2.get("ship_name", ""),
                            p2.get("ship_type"),
                            p2.get("flag", ""),
                            p2.get("destination", ""),
                        ),
                    )
                    new_events += 1
                    logger.info(
                        "Transit detected: MMSI %d %s at %s (%.1f kn)",
                        mmsi, direction, p2["received_at"], cross_speed or 0,
                    )

        # Update last check time
        await db.execute(
            """
            INSERT OR REPLACE INTO analytics_state (key, value)
            VALUES ('last_transit_check', ?)
            """,
            (now_iso,),
        )
        await db.commit()

    return new_events


# ── Vessel state classification ──

def classify_vessel_state(speed: float | None) -> str:
    """Classify a vessel's operational state based on speed."""
    if speed is None:
        return "unknown"
    if speed < SPEED_ANCHORED:
        return "anchored"
    if speed < SPEED_SLOW:
        return "slow"
    if speed < SPEED_MANEUVERING:
        return "maneuvering"
    return "transiting"


def identify_anchorage_zone(lat: float, lon: float) -> str | None:
    """Identify which anchorage zone a position falls within."""
    for name, zone in ANCHORAGE_ZONES.items():
        dist = haversine_nm(lat, lon, zone["lat"], zone["lon"])
        if dist <= zone["radius_nm"]:
            return name
    return None


# ── Query functions for API ──

async def get_transit_summary(hours: int = 24) -> dict:
    """Get transit event summary for the last N hours."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        inbound = await db.execute_fetchall(
            """
            SELECT COUNT(*) as cnt FROM transit_events
            WHERE direction = 'INBOUND'
              AND crossed_at > datetime('now', ? || ' hours')
            """,
            (f"-{hours}",),
        )
        outbound = await db.execute_fetchall(
            """
            SELECT COUNT(*) as cnt FROM transit_events
            WHERE direction = 'OUTBOUND'
              AND crossed_at > datetime('now', ? || ' hours')
            """,
            (f"-{hours}",),
        )

        # Recent events
        recent = await db.execute_fetchall(
            """
            SELECT mmsi, direction, crossed_at, speed,
                   ship_name, ship_type, flag, destination
            FROM transit_events
            WHERE crossed_at > datetime('now', ? || ' hours')
            ORDER BY crossed_at DESC
            LIMIT 20
            """,
            (f"-{hours}",),
        )

    return {
        "hours": hours,
        "inbound": inbound[0][0] if inbound else 0,
        "outbound": outbound[0][0] if outbound else 0,
        "recent_events": [
            {
                "mmsi": r["mmsi"],
                "direction": r["direction"],
                "crossed_at": r["crossed_at"],
                "speed": r["speed"],
                "ship_name": r["ship_name"],
                "ship_type": r["ship_type"],
                "flag": r["flag"],
                "destination": r["destination"],
            }
            for r in recent
        ],
    }


async def get_hourly_transits(hours: int = 48) -> list[dict]:
    """Get transit counts aggregated by hour for charting."""
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall(
            """
            SELECT strftime('%Y-%m-%dT%H:00:00', crossed_at) as hour,
                   direction,
                   COUNT(*) as cnt
            FROM transit_events
            WHERE crossed_at > datetime('now', ? || ' hours')
            GROUP BY hour, direction
            ORDER BY hour
            """,
            (f"-{hours}",),
        )

    # Build a complete hourly timeline
    result: dict[str, dict] = {}
    for row in rows:
        hour = row[0]
        if hour not in result:
            result[hour] = {"hour": hour, "inbound": 0, "outbound": 0}
        if row[1] == "INBOUND":
            result[hour]["inbound"] = row[2]
        else:
            result[hour]["outbound"] = row[2]

    return sorted(result.values(), key=lambda x: x["hour"])


async def get_vessel_states() -> dict:
    """Get current vessel state breakdown (last 30 min positions)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("""
            SELECT mmsi, latitude, longitude, speed, ship_name, ship_type, flag
            FROM positions
            WHERE id IN (
                SELECT MAX(id) FROM positions
                WHERE received_at > datetime('now', '-30 minutes')
                GROUP BY mmsi
            )
        """)

    states: dict[str, int] = {
        "anchored": 0, "slow": 0, "maneuvering": 0, "transiting": 0, "unknown": 0,
    }
    zone_counts: dict[str, int] = {}
    vessels_by_state: dict[str, list] = {
        "anchored": [], "slow": [], "maneuvering": [], "transiting": [], "unknown": [],
    }

    for r in rows:
        state = classify_vessel_state(r["speed"])
        states[state] += 1
        vessels_by_state[state].append({
            "mmsi": r["mmsi"],
            "lat": r["latitude"],
            "lon": r["longitude"],
            "speed": r["speed"],
            "name": r["ship_name"] or f"MMSI:{r['mmsi']}",
        })

        # Zone identification (only for slow/anchored)
        if state in ("anchored", "slow"):
            zone = identify_anchorage_zone(r["latitude"], r["longitude"])
            if zone:
                zone_counts[zone] = zone_counts.get(zone, 0) + 1

    return {
        "states": states,
        "total": sum(states.values()),
        "zone_counts": dict(sorted(zone_counts.items(), key=lambda x: -x[1])),
        "vessels_by_state": vessels_by_state,
    }


async def get_flag_distribution(hours: int = 24) -> list[dict]:
    """Get vessel flag state distribution."""
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall(
            """
            SELECT flag, COUNT(DISTINCT mmsi) as vessels
            FROM positions
            WHERE received_at > datetime('now', ? || ' hours')
              AND flag IS NOT NULL AND flag != ''
            GROUP BY flag
            ORDER BY vessels DESC
            LIMIT 20
            """,
            (f"-{hours}",),
        )
    return [{"flag": r[0], "vessels": r[1]} for r in rows]


async def get_destination_distribution(hours: int = 24) -> list[dict]:
    """Get destination distribution for active vessels."""
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall(
            """
            SELECT destination, COUNT(DISTINCT mmsi) as vessels
            FROM positions
            WHERE received_at > datetime('now', ? || ' hours')
              AND destination IS NOT NULL AND destination != ''
            GROUP BY destination
            ORDER BY vessels DESC
            LIMIT 20
            """,
            (f"-{hours}",),
        )
    return [{"destination": r[0], "vessels": r[1]} for r in rows]


async def get_daily_summary() -> dict:
    """Generate a comprehensive daily summary."""
    transit = await get_transit_summary(24)
    states = await get_vessel_states()
    flags = await get_flag_distribution(24)
    destinations = await get_destination_distribution(24)

    async with aiosqlite.connect(DB_PATH) as db:
        total_records = (
            await db.execute_fetchall("SELECT COUNT(*) FROM positions")
        )[0][0]
        records_24h = (
            await db.execute_fetchall(
                "SELECT COUNT(*) FROM positions "
                "WHERE received_at > datetime('now', '-24 hours')"
            )
        )[0][0]
        unique_24h = (
            await db.execute_fetchall(
                "SELECT COUNT(DISTINCT mmsi) FROM positions "
                "WHERE received_at > datetime('now', '-24 hours')"
            )
        )[0][0]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_records": total_records,
        "records_24h": records_24h,
        "unique_vessels_24h": unique_24h,
        "transits_24h": {
            "inbound": transit["inbound"],
            "outbound": transit["outbound"],
            "total": transit["inbound"] + transit["outbound"],
        },
        "vessel_states": states["states"],
        "anchorage_zones": states["zone_counts"],
        "top_flags": flags[:10],
        "top_destinations": destinations[:10],
    }


# ── Background task ──

async def transit_detection_loop(interval_sec: int = 300):
    """Background loop: detect transits every `interval_sec` seconds."""
    await init_analytics_db()

    # On first run, process ALL historical data
    logger.info("Running initial historical transit detection...")
    n = await detect_transits(lookback_minutes=999999)
    logger.info("Initial scan: %d transit events detected", n)

    while True:
        await asyncio.sleep(interval_sec)
        try:
            n = await detect_transits(lookback_minutes=interval_sec // 60 + 5)
            if n > 0:
                logger.info("Periodic scan: %d new transits detected", n)
        except Exception as e:
            logger.error("Transit detection error: %s", e)
