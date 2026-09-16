"""Merge finished days of shards into one file per day.

A collection window writes a small file every time it wakes up. Left alone that
is tens of thousands of files a year, so once a day the finished days are
merged and their shards removed in the same commit.

  python src/compact.py --days 3 --dry-run
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import hf_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=3,
                    help="how many finished days back to look at (default: 3)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    # Never touch today: the collector is still writing into it.
    days = [(today - timedelta(days=d)).isoformat() for d in range(1, args.days + 1)]

    results = [hf_store.merge_day(day, dry_run=args.dry_run) for day in days]
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
