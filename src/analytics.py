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

# ── Virtual gate lines ──
# Multiple gates to capture traffic patterns at different scales.
# Each gate: name, point_a, point_b, and a rule to determine INBOUND direction.
#
# "inbound_side": which side of the gate vector (A→B) is the "outside" / approach side.
#   "left"  = points with positive cross product are the approach side
#   "right" = points with negative cross product are the approach side
GATES = {
    "Strait of Hormuz": {
        "a": (26.05, 56.50),  # Oman/Musandam
        "b": (26.65, 56.10),  # Iran/Qeshm
        "inbound_side": "left",  # east (Gulf of Oman) side
        "description": "Main chokepoint — satellite AIS needed for full coverage",
    },
    "Dubai / Jebel Ali Approach": {
        "a": (25.00, 55.20),  # south — offshore Abu Dhabi
        "b": (25.35, 55.20),  # north — offshore Sharjah
        "inbound_side": "left",  # east (offshore) side approaching port
        "description": "Traffic entering/leaving Dubai & Jebel Ali ports",
    },
    "Fujairah Approach": {
        "a": (25.00, 56.50),  # south
        "b": (25.30, 56.50),  # north
        "inbound_side": "left",  # east (Gulf of Oman offshore) side
        "description": "Fujairah anchorage & bunkering traffic",
    },
}

# Legacy single-gate references (for snapshot.py compatibility)
GATE_A = GATES["Strait of Hormuz"]["a"]
GATE_B = GATES["Strait of Hormuz"]["b"]

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
                gate_name TEXT NOT NULL DEFAULT 'Strait of Hormuz',
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
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_transit_gate
            ON transit_events(gate_name)
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
    gate_a: tuple[float, float],
    gate_b: tuple[float, float],
    inbound_side: str = "left",
) -> str:
    """Determine if a vessel crossing a gate is INBOUND or OUTBOUND.

    Uses the cross product relative to the gate vector (A→B) to determine
    which side each point is on:
    - Positive cross product = point is to the LEFT of A→B
    - Negative = to the RIGHT

    inbound_side: "left" means left-side is the approach/outside direction.
    """
    side1 = _cross_product_2d(gate_a, gate_b, p1)
    side2 = _cross_product_2d(gate_a, gate_b, p2)

    if inbound_side == "left":
        # left (positive) = outside/approach side
        if side1 > 0 and side2 < 0:
            return "INBOUND"
        elif side1 < 0 and side2 > 0:
            return "OUTBOUND"
    else:
        # right (negative) = outside/approach side
        if side1 < 0 and side2 > 0:
            return "INBOUND"
        elif side1 > 0 and side2 < 0:
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

        # Detect crossings across ALL gates
        new_events = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        for mmsi, positions in vessel_positions.items():
            if len(positions) < 2:
                continue

            for i in range(len(positions) - 1):
                p1 = positions[i]
                p2 = positions[i + 1]

                # Skip if points are too far apart in time (>30 min gap)
                try:
                    t1 = datetime.fromisoformat(p1["received_at"])
                    t2 = datetime.fromisoformat(p2["received_at"])
                    if (t2 - t1).total_seconds() > 1800:
                        continue
                except (ValueError, TypeError):
                    continue

                # Check each gate
                for gate_name, gate in GATES.items():
                    gate_a = gate["a"]
                    gate_b = gate["b"]
                    gate_center = (
                        (gate_a[0] + gate_b[0]) / 2,
                        (gate_a[1] + gate_b[1]) / 2,
                    )

                    # Skip if both points are far from this gate (>30 nm)
                    d1 = haversine_nm(p1["lat"], p1["lon"], gate_center[0], gate_center[1])
                    d2 = haversine_nm(p2["lat"], p2["lon"], gate_center[0], gate_center[1])
                    if d1 > 30 and d2 > 30:
                        continue

                    # Check crossing
                    if not segments_intersect(
                        (p1["lat"], p1["lon"]),
                        (p2["lat"], p2["lon"]),
                        gate_a,
                        gate_b,
                    ):
                        continue

                    direction = determine_transit_direction(
                        (p1["lat"], p1["lon"]),
                        (p2["lat"], p2["lon"]),
                        gate_a,
                        gate_b,
                        gate.get("inbound_side", "left"),
                    )
                    if direction == "UNKNOWN":
                        continue

                    # Deduplicate: same MMSI + same gate in last 6 hours
                    existing = await db.execute_fetchall(
                        """
                        SELECT id FROM transit_events
                        WHERE mmsi = ? AND gate_name = ?
                          AND crossed_at > datetime(?, '-6 hours')
                        """,
                        (mmsi, gate_name, p2["received_at"]),
                    )
                    if existing:
                        continue

                    cross_lat = (p1["lat"] + p2["lat"]) / 2
                    cross_lon = (p1["lon"] + p2["lon"]) / 2
                    cross_speed = p2["speed"] or p1["speed"]

                    await db.execute(
                        """
                        INSERT INTO transit_events
                        (mmsi, gate_name, direction, crossed_at, latitude,
                         longitude, speed, ship_name, ship_type, flag, destination)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mmsi,
                            gate_name,
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
                        "Transit [%s]: MMSI %d %s at %s (%.1f kn)",
                        gate_name, mmsi, direction,
                        p2["received_at"], cross_speed or 0,
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

async def get_transit_summary(hours: int = 24, gate: str | None = None) -> dict:
    """Get transit event summary for the last N hours, optionally filtered by gate."""
    gate_filter = "AND gate_name = ?" if gate else ""
    params_base = (f"-{hours}",) + ((gate,) if gate else ())

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        inbound = await db.execute_fetchall(
            f"""
            SELECT COUNT(*) as cnt FROM transit_events
            WHERE direction = 'INBOUND'
              AND crossed_at > datetime('now', ? || ' hours')
              {gate_filter}
            """,
            params_base,
        )
        outbound = await db.execute_fetchall(
            f"""
            SELECT COUNT(*) as cnt FROM transit_events
            WHERE direction = 'OUTBOUND'
              AND crossed_at > datetime('now', ? || ' hours')
              {gate_filter}
            """,
            params_base,
        )

        # Per-gate breakdown
        by_gate = await db.execute_fetchall(
            """
            SELECT gate_name, direction, COUNT(*) as cnt
            FROM transit_events
            WHERE crossed_at > datetime('now', ? || ' hours')
            GROUP BY gate_name, direction
            ORDER BY gate_name
            """,
            (f"-{hours}",),
        )

        # Recent events
        recent = await db.execute_fetchall(
            f"""
            SELECT mmsi, gate_name, direction, crossed_at, speed,
                   ship_name, ship_type, flag, destination
            FROM transit_events
            WHERE crossed_at > datetime('now', ? || ' hours')
              {gate_filter}
            ORDER BY crossed_at DESC
            LIMIT 20
            """,
            params_base,
        )

    # Build per-gate summary
    gate_summary: dict[str, dict] = {}
    for row in by_gate:
        gn = row[0]
        if gn not in gate_summary:
            gate_summary[gn] = {"inbound": 0, "outbound": 0}
        if row[1] == "INBOUND":
            gate_summary[gn]["inbound"] = row[2]
        else:
            gate_summary[gn]["outbound"] = row[2]

    return {
        "hours": hours,
        "gate_filter": gate,
        "inbound": inbound[0][0] if inbound else 0,
        "outbound": outbound[0][0] if outbound else 0,
        "by_gate": gate_summary,
        "recent_events": [
            {
                "mmsi": r["mmsi"],
                "gate": r["gate_name"],
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


async def get_hourly_transits(hours: int = 48, gate: str | None = None) -> list[dict]:
    """Get transit counts aggregated by hour for charting."""
    gate_filter = "AND gate_name = ?" if gate else ""
    params = (f"-{hours}",) + ((gate,) if gate else ())

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall(
            f"""
            SELECT strftime('%Y-%m-%dT%H:00:00', crossed_at) as hour,
                   direction,
                   COUNT(*) as cnt
            FROM transit_events
            WHERE crossed_at > datetime('now', ? || ' hours')
              {gate_filter}
            GROUP BY hour, direction
            ORDER BY hour
            """,
            params,
        )

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
