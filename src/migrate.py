"""Database migration: fix timestamps, backfill flags, normalize destinations.

Run once after updating the collector code to fix historical data.
Safe to run multiple times (idempotent).

Usage (inside Docker container):
    python src/migrate.py
"""

import sqlite3
import sys

from country_codes import mmsi_to_flag
from destinations import normalize_destination

DB_PATH = "/app/data/ais.db"


def migrate_timestamps(conn: sqlite3.Connection) -> int:
    """Fix timestamps from aisstream.io format to ISO 8601.

    Before: "2026-03-14 06:57:51.594510977 +0000 UTC"
    After:  "2026-03-14T06:57:51.594510"
    """
    cursor = conn.execute(
        "SELECT id, timestamp FROM positions WHERE timestamp LIKE '% +0000 UTC'"
    )
    rows = cursor.fetchall()
    if not rows:
        print("  No timestamps to fix.")
        return 0

    fixed = 0
    for row_id, raw_ts in rows:
        try:
            parts = raw_ts.split(" +")
            base = parts[0] if len(parts) >= 2 else raw_ts.rstrip(" UTC")
            base = base.replace(" ", "T", 1)
            if "." in base:
                main, frac = base.split(".", 1)
                base = main + "." + frac[:6]
            conn.execute(
                "UPDATE positions SET timestamp = ? WHERE id = ?",
                (base, row_id),
            )
            fixed += 1
        except Exception as e:
            print(f"  Warning: could not fix row {row_id}: {e}")

    conn.commit()
    print(f"  Fixed {fixed} timestamps.")
    return fixed


def migrate_flags(conn: sqlite3.Connection) -> int:
    """Backfill flag field from MMSI for rows where flag is empty."""
    cursor = conn.execute(
        "SELECT DISTINCT mmsi FROM positions WHERE flag IS NULL OR flag = ''"
    )
    mmsi_list = [r[0] for r in cursor.fetchall()]
    if not mmsi_list:
        print("  No flags to backfill.")
        return 0

    filled = 0
    for mmsi in mmsi_list:
        code, _ = mmsi_to_flag(mmsi)
        if code:
            conn.execute(
                "UPDATE positions SET flag = ? WHERE mmsi = ? AND (flag IS NULL OR flag = '')",
                (code, mmsi),
            )
            filled += 1

    conn.commit()
    print(f"  Backfilled flags for {filled} distinct MMSIs.")
    return filled


def migrate_destinations(conn: sqlite3.Connection) -> int:
    """Normalize destination fields."""
    cursor = conn.execute(
        "SELECT DISTINCT destination FROM positions "
        "WHERE destination IS NOT NULL AND destination != ''"
    )
    dest_list = [r[0] for r in cursor.fetchall()]
    if not dest_list:
        print("  No destinations to normalize.")
        return 0

    changed = 0
    for raw in dest_list:
        normalized = normalize_destination(raw)
        if normalized != raw:
            conn.execute(
                "UPDATE positions SET destination = ? WHERE destination = ?",
                (normalized, raw),
            )
            changed += 1

    conn.commit()
    print(f"  Normalized {changed} distinct destination values.")
    return changed


def create_analytics_tables(conn: sqlite3.Connection):
    """Create analytics tables if they don't exist."""
    conn.execute("""
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
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_transit_crossed_at
        ON transit_events(crossed_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_transit_mmsi
        ON transit_events(mmsi)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_transit_gate
        ON transit_events(gate_name)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analytics_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    print("  Analytics tables created/verified.")


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    print(f"Migrating database: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        print("\n1. Creating analytics tables...")
        create_analytics_tables(conn)

        print("\n2. Fixing timestamps...")
        migrate_timestamps(conn)

        print("\n3. Backfilling flags from MMSI...")
        migrate_flags(conn)

        print("\n4. Normalizing destinations...")
        migrate_destinations(conn)

        # Verify
        print("\n--- Verification ---")
        bad_ts = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE timestamp LIKE '% +0000 UTC'"
        ).fetchone()[0]
        empty_flag = conn.execute(
            "SELECT COUNT(DISTINCT mmsi) FROM positions WHERE flag IS NULL OR flag = ''"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        print(f"  Total records: {total}")
        print(f"  Bad timestamps remaining: {bad_ts}")
        print(f"  MMSIs without flag: {empty_flag}")
        print("\nMigration complete!")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
