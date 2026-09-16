"""AIS data collector via aisstream.io WebSocket.

This is the long-running form: it holds the connection open and writes every
position report it is allowed to keep. For the windowed form that CI runs on a
schedule, see ``window_collect.py``. Both share ``ais_parse.StreamParser``.
"""

import asyncio
import json
import logging
import os
import time

import aiosqlite
import websockets

from ais_parse import STREAM_URL, StreamParser, subscribe_message

logger = logging.getLogger(__name__)

API_KEY = os.environ["AISSTREAM_API_KEY"]

DB_PATH = os.environ.get("AIS_DB_PATH", "/app/data/ais.db")

# Batch flush interval (seconds)
BATCH_FLUSH_SEC = 5

INSERT_SQL = """INSERT INTO positions
    (mmsi, timestamp, latitude, longitude, speed, course, heading,
     ship_name, ship_type, destination, draught, length, width, flag, received_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


async def init_db(db_path: str | None = None):
    """Create tables if they don't exist."""
    async with aiosqlite.connect(db_path or DB_PATH) as db:
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
    logger.info("Database initialized: %s", db_path or DB_PATH)


async def flush_batch(batch: list[tuple], db_path: str | None = None) -> int:
    """Write a batch of position records to SQLite in a single transaction."""
    if not batch:
        return 0
    async with aiosqlite.connect(db_path or DB_PATH) as db:
        await db.executemany(INSERT_SQL, batch)
        await db.commit()
    return len(batch)


async def collect():
    """Connect to aisstream.io and store AIS messages until cancelled."""
    await init_db()

    subscribe_msg = subscribe_message(API_KEY)
    parser = StreamParser()

    batch: list[tuple] = []
    last_flush = time.monotonic()

    while True:
        try:
            async with websockets.connect(STREAM_URL) as ws:
                await ws.send(subscribe_msg)
                logger.info(
                    "Connected to aisstream.io — collecting Persian Gulf & Gulf of Oman"
                )

                async for raw in ws:
                    try:
                        row = parser.feed(raw)
                        if row is not None:
                            batch.append(row)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning("Parse error: %s", e)

                    # Flush batch periodically
                    if time.monotonic() - last_flush >= BATCH_FLUSH_SEC:
                        if batch:
                            n = await flush_batch(batch)
                            logger.debug("Flushed %d records", n)
                            batch.clear()
                        last_flush = time.monotonic()

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
