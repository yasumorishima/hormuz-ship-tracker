"""Rebuild a working SQLite database from the Hub dataset.

The analysis and rendering scripts all read SQLite. On a runner there is no
database to read, so this recreates one from the recent parquet files and the
rest of the pipeline continues unchanged.

  python src/build_db.py --hours 48 --db /tmp/ais.db
"""

import argparse
import json
import logging
import sys

import hf_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=48,
                    help="how far back to pull (whole days, rounded outwards)")
    ap.add_argument("--db", default="/tmp/ais.db")
    ap.add_argument("--include-archive", action="store_true",
                    help="also load the 2026-03..05 archive from the Pi era")
    args = ap.parse_args()

    files = hf_store.list_data_files()
    paths = hf_store.files_since(args.hours, files=files)
    if args.include_archive and hf_store.LEGACY_FILE in files:
        paths = [hf_store.LEGACY_FILE] + paths

    rows = hf_store.build_sqlite(paths, args.db)
    print(json.dumps({"files": len(paths), "rows": rows, "db": args.db}, indent=2))
    # Zero rows is a fact about the window, not a failure of this program. The
    # caller knows whether an empty window is expected — while the feed has no
    # coverage here, it is the normal state, and exiting 1 would redden the
    # publish run every three hours over someone else's receivers.
    return 0


if __name__ == "__main__":
    sys.exit(main())
