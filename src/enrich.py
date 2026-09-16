"""Fill in static vessel data that a single collection window could not see.

A window is three minutes. Class A transponders send their static frame (name,
type, destination, dimensions) every few minutes, so a vessel's position often
lands in a window that never carried its description — the fields arrive in a
different window from the positions they describe. The long-running collector
did not have this problem: its cache lived for months.

The equivalent here is to take, per vessel, the most recent non-empty value
anywhere in the loaded range. Nothing is invented: a value is only ever copied
from another row of the same MMSI.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# Columns that describe the vessel rather than the moment.
STATIC_COLUMNS = ("ship_name", "ship_type", "destination", "draught", "length", "width")


def _missing(conn: sqlite3.Connection, column: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM positions WHERE {column} IS NULL OR {column} = ''"
    ).fetchone()[0]


def backfill_static(conn: sqlite3.Connection) -> dict:
    """Copy static fields between rows of the same vessel. Returns what it filled."""
    filled = {}
    for column in STATIC_COLUMNS:
        before = _missing(conn, column)
        conn.execute(f"""
            UPDATE positions SET {column} = (
                SELECT other.{column} FROM positions AS other
                WHERE other.mmsi = positions.mmsi
                  AND other.{column} IS NOT NULL
                  AND other.{column} != ''
                ORDER BY other.received_at DESC
                LIMIT 1
            )
            WHERE ({column} IS NULL OR {column} = '')
              -- Only touch a row we can actually improve; without this the
              -- subquery writes its NULL over an empty string and calls it
              -- progress.
              AND EXISTS (
                SELECT 1 FROM positions AS donor
                WHERE donor.mmsi = positions.mmsi
                  AND donor.{column} IS NOT NULL
                  AND donor.{column} != ''
              )
        """)
        filled[column] = before - _missing(conn, column)
    conn.commit()
    logger.info("backfilled static fields: %s", filled)
    return filled
