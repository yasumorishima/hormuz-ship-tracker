"""AIS data collector via aisstream.io WebSocket."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import aiosqlite
import websockets

from land_filter import is_on_land

logger = logging.getLogger(__name__)

API_KEY = os.environ["AISSTREAM_API_KEY"]

# Strait of Hormuz bounding box (wider area to capture approaching vessels)
BBOX = [[23.5, 54.0], [27.5, 58.5]]

DB_PATH = "/app/data/ais.db"


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

    while True:
        try:
            async with websockets.connect("wss://stream.aisstream.io/v0/stream") as ws:
                await ws.send(json.dumps(subscribe_msg))
                logger.info("Connected to aisstream.io — collecting Strait of Hormuz")

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

                            static = static_cache.get(mmsi, {})
                            ship_name = meta_data.get("ShipName", "").strip() or static.get("ship_name", "")

                            now = datetime.now(timezone.utc).isoformat()
                            ts = meta_data.get("time_utc", now)

                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute(
                                    """INSERT INTO positions
                                    (mmsi, timestamp, latitude, longitude, speed, course, heading,
                                     ship_name, ship_type, destination, draught, length, width, flag, received_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (
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
                                    ),
                                )
                                await db.commit()

                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning("Parse error: %s", e)

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            logger.warning("Connection lost: %s — reconnecting in 10s", e)
            await asyncio.sleep(10)
        except Exception as e:
            logger.error("Unexpected error: %s — reconnecting in 30s", e)
            await asyncio.sleep(30)
