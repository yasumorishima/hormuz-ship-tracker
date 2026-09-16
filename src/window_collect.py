"""Collect one window from aisstream.io and store it on the Hub.

The stream only pushes, so there is no way to ask it for "the current fleet".
What a scheduled job can do is open the connection for a few minutes and keep
one position per vessel — a sample of the fleet rather than a continuous track.

  python src/window_collect.py --seconds 180 --probe    # measure, upload nothing
  python src/window_collect.py --seconds 180            # measure and upload
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import websockets

from ais_parse import BBOX, STREAM_URL, StreamParser, subscribe_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("window_collect")

# How long to wait before reconnecting inside a window. The first retry has
# to be short relative to the window — a flat 5 s ate the whole remainder of a
# short one — but it has to grow, or a rejected key becomes a connection storm
# against a free service: 180 attempts per window, 96 windows a day.
RECONNECT_BACKOFF_SEC = 1.0
RECONNECT_BACKOFF_MAX_SEC = 15.0

# The library's own default is 10 s and is not bounded by anything we set, so
# a peer that accepts the connection and never answers the handshake would
# hold a short window past its deadline. Measured: a stalled handshake raises
# TimeoutError after exactly this long. TimeoutError subclasses OSError, so
# the existing handler already catches it.
HANDSHAKE_TIMEOUT_SEC = 10.0


async def collect_window(api_key: str, seconds: float) -> tuple[list[tuple], dict]:
    """Hold the connection for `seconds`, then return the rows and the counters."""
    # One row per vessel per window: the throttle is the window itself.
    parser = StreamParser(position_interval_sec=seconds)
    rows: list[tuple] = []
    deadline = time.monotonic() + seconds
    reconnects = 0
    connect_errors = 0
    frame_errors = 0

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            async with websockets.connect(
                STREAM_URL,
                open_timeout=min(HANDSHAKE_TIMEOUT_SEC, max(0.1, remaining)),
            ) as ws:
                await ws.send(subscribe_message(api_key))
                logger.info("connected — %.0fs left in window", remaining)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    try:
                        row = parser.feed(raw)
                    except Exception as e:
                        # One malformed frame must not cost the whole window.
                        frame_errors += 1
                        logger.warning("frame error: %s", e)
                        continue
                    if row is not None:
                        rows.append(row)
        except (websockets.exceptions.WebSocketException, OSError) as e:
            # Counted whether or not there is time to try again, so that an
            # empty window says which kind of empty it was: never connected,
            # or connected and silent. TimeoutError from a stalled handshake
            # arrives here too — it subclasses OSError.
            connect_errors += 1
            left = deadline - time.monotonic()
            if left <= 0:
                logger.warning("connection ended at the deadline (%s)", e)
                break
            reconnects += 1
            logger.warning("connection lost (%s) — retry %d", e, reconnects)
            backoff = RECONNECT_BACKOFF_SEC * 2 ** (reconnects - 1)
            await asyncio.sleep(min(backoff, RECONNECT_BACKOFF_MAX_SEC, left))

    summary = parser.summary()
    summary["where"] = coverage(parser.coords)
    summary["rows"] = len(rows)
    summary["reconnects"] = reconnects
    summary["connect_errors"] = connect_errors
    summary["frame_errors"] = frame_errors
    summary["window_sec"] = seconds
    return rows, summary


def coverage(coords) -> dict:
    """Where the feed actually had something to say.

    A count alone cannot distinguish "the box is not being applied" from
    "there are no receivers left in this water", and those call for opposite
    responses.
    """
    if not coords:
        return {}
    (lat0, lon0), (lat1, lon1) = BBOX
    inside = sum(1 for la, lo in coords
                 if min(lat0, lat1) <= la <= max(lat0, lat1)
                 and min(lon0, lon1) <= lo <= max(lon0, lon1))
    cells: dict[str, int] = {}
    for la, lo in coords:
        cell = f"{int(la // 10) * 10}N/{int(lo // 10) * 10}E"
        cells[cell] = cells.get(cell, 0) + 1
    busiest = sorted(cells.items(), key=lambda kv: -kv[1])[:8]
    return {
        "positions": len(coords),
        "inside_the_collection_box": inside,
        "lat_range": [min(c[0] for c in coords), max(c[0] for c in coords)],
        "lon_range": [min(c[1] for c in coords), max(c[1] for c in coords)],
        "busiest_10deg_cells": dict(busiest),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=180.0,
                    help="how long to hold the connection (default: 180)")
    ap.add_argument("--probe", action="store_true",
                    help="report what the window saw and upload nothing")
    ap.add_argument("--min-rows", type=int, default=1,
                    help="fail if the window yielded fewer rows than this")
    args = ap.parse_args()

    api_key = os.environ.get("AISSTREAM_API_KEY")
    if not api_key:
        print("AISSTREAM_API_KEY is not set", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    rows, summary = asyncio.run(collect_window(api_key, args.seconds))
    summary["started_utc"] = started.isoformat()

    # Report what the window saw before attempting the upload: a failed
    # upload must not take the measurement with it.
    print(json.dumps(summary, indent=2, sort_keys=True))

    if not args.probe and rows:
        import hf_store
        print(json.dumps({"shard": hf_store.upload_rows(rows, started)}, indent=2))

    if len(rows) < args.min_rows:
        print(f"window yielded {len(rows)} rows (< {args.min_rows})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
