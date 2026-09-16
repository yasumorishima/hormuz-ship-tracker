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

from ais_parse import STREAM_URL, StreamParser, subscribe_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("window_collect")


async def collect_window(api_key: str, seconds: float) -> tuple[list[tuple], dict]:
    """Hold the connection for `seconds`, then return the rows and the counters."""
    # One row per vessel per window: the throttle is the window itself.
    parser = StreamParser(position_interval_sec=seconds)
    rows: list[tuple] = []
    deadline = time.monotonic() + seconds
    reconnects = 0
    frame_errors = 0

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            async with websockets.connect(STREAM_URL) as ws:
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
            if time.monotonic() >= deadline:
                break
            reconnects += 1
            logger.warning("connection lost (%s) — retry %d", e, reconnects)
            await asyncio.sleep(min(5, max(0.0, deadline - time.monotonic())))

    summary = parser.summary()
    summary["rows"] = len(rows)
    summary["reconnects"] = reconnects
    summary["frame_errors"] = frame_errors
    summary["window_sec"] = seconds
    return rows, summary


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
