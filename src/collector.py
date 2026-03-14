"""AIS data collector via aisstream.io WebSocket."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import aiosqlite
import websockets

from land_filter import is_on_land

logger = logging.getLogger(__name__)

API_KEY = os.environ["AISSTREAM_API_KEY"]

# Persian Gulf + Gulf of Oman — full coverage
BBOX = [[22.0, 48.0], [30.5, 60.0]]

DB_PATH = "/app/data/ais.db"

# Per-vessel throttle: store at most one position per MMSI per this many seconds
POSITION_INTERVAL_SEC = 120

# Batch flush interval (seconds)
BATCH_FLUSH_SEC = 5


async def init_db():
    """Create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mmsi INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                speed REAL,
                course REAL,
                heading REAL,
                ship_name TEXT,
                ship_type INTEGER,
                destination TEXT,
                draught REAL,
                length REAL,
                width REAL,
                flag TEXT,
                received_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_timestamp
            ON positions(timestamp)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_mmsi
            ON positions(mmsi)
        """)
        await db.commit()
    logger.info("Database initialized: %s", DB_PATH)


async def flush_batch(batch: list[tuple]) -> int:
    """Write a batch of position records to SQLite in a single transaction."""
    if not batch:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """INSERT INTO positions
            (mmsi, timestamp, latitude, longitude, speed, course, heading,
             ship_name, ship_type, destination, draught, length, width, flag, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )
        await db.commit()
    return len(batch)


async def collect():
    """Connect to aisstream.io and store AIS messages."""
    await init_db()

    subscribe_msg = {
        "APIKey": API_KEY,
        "BoundingBoxes": [BBOX],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    # In-memory cache for static data (ship name, type, etc.)
    static_cache: dict[int, dict] = {}

    # Per-vessel throttle: last stored timestamp per MMSI
    last_stored: dict[int, float] = {}

    # Batch buffer
    batch: list[tuple] = []
    last_flush = time.monotonic()

    while True:
        try:
            async with websockets.connect("wss://stream.aisstream.io/v0/stream") as ws:
                await ws.send(json.dumps(subscribe_msg))
                logger.info(
                    "Connected to aisstream.io — collecting Persian Gulf & Gulf of Oman"
                )

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        msg_type = msg.get("MessageType")

                        if msg_type == "ShipStaticData":
                            meta = msg.get("Message", {}).get("ShipStaticData", {})
                            mmsi = msg.get("MetaData", {}).get("MMSI")
                            if mmsi:
                                static_cache[mmsi] = {
                                    "ship_name": meta.get("Name", "").strip(),
                                    "ship_type": meta.get("Type"),
                                    "destination": meta.get("Destination", "").strip(),
                                    "draught": meta.get("MaximumStaticDraught"),
                                    "length": meta.get("Dimension", {}).get("A", 0)
                                    + meta.get("Dimension", {}).get("B", 0),
                                    "width": meta.get("Dimension", {}).get("C", 0)
                                    + meta.get("Dimension", {}).get("D", 0),
                                }

                        elif msg_type == "PositionReport":
                            pos = msg.get("Message", {}).get("PositionReport", {})
                            meta_data = msg.get("MetaData", {})
                            mmsi = meta_data.get("MMSI")

                            if not mmsi:
                                continue

                            lat = pos.get("Latitude")
                            lon = pos.get("Longitude")
                            if lat is None or lon is None:
                                continue

                            if is_on_land(lat, lon):
                                continue

                            # Per-vessel throttle
                            now_mono = time.monotonic()
                            prev = last_stored.get(mmsi, 0)
                            if now_mono - prev < POSITION_INTERVAL_SEC:
                                continue
                            last_stored[mmsi] = now_mono

                            static = static_cache.get(mmsi, {})
                            ship_name = (
                                meta_data.get("ShipName", "").strip()
                                or static.get("ship_name", "")
                            )

                            now = datetime.now(timezone.utc).isoformat()
                            ts = meta_data.get("time_utc", now)

                            batch.append((
                                mmsi,
                                ts,
                                lat,
                                lon,
                                pos.get("Sog"),
                                pos.get("Cog"),
                                pos.get("TrueHeading"),
                                ship_name,
                                static.get("ship_type"),
                                static.get("destination", ""),
                                static.get("draught"),
                                static.get("length"),
                                static.get("width"),
                                meta_data.get("country_code", ""),
                                now,
                            ))

                        # Flush batch periodically
                        if time.monotonic() - last_flush >= BATCH_FLUSH_SEC:
                            if batch:
                                n = await flush_batch(batch)
                                logger.debug("Flushed %d records", n)
                                batch.clear()
                            last_flush = time.monotonic()

                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning("Parse error: %s", e)

                # Flush remaining on disconnect
                if batch:
                    await flush_batch(batch)
                    batch.clear()

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            logger.warning("Connection lost: %s — reconnecting in 10s", e)
            if batch:
                await flush_batch(batch)
                batch.clear()
            await asyncio.sleep(10)
        except Exception as e:
            logger.error("Unexpected error: %s — reconnecting in 30s", e)
            if batch:
                await flush_batch(batch)
                batch.clear()
            await asyncio.sleep(30)
