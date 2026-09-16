"""Shared aisstream.io message handling.

Both the long-running collector (``collector.py``) and the windowed collector
used by CI (``window_collect.py``) must turn the same stream into exactly the
same ``positions`` rows, so the parsing lives here instead of in either caller.
"""

import json
import logging
import time
from datetime import datetime, timezone

from columns import COLUMNS
from country_codes import mmsi_to_flag
from destinations import normalize_destination
from land_filter import is_on_land

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.aisstream.io/v0/stream"

# Persian Gulf + Gulf of Oman — full coverage
BBOX = [[22.0, 48.0], [30.5, 60.0]]

MESSAGE_TYPES = ["PositionReport", "ShipStaticData"]

# Per-vessel throttle: store at most one position per MMSI per this many seconds
POSITION_INTERVAL_SEC = 120


def subscribe_message(api_key: str, bbox=None) -> str:
    """Build the aisstream.io subscription frame."""
    return json.dumps({
        "APIKey": api_key,
        "BoundingBoxes": [bbox or BBOX],
        "FilterMessageTypes": MESSAGE_TYPES,
    })


def normalize_timestamp(raw: str) -> str:
    """Normalize aisstream.io timestamp to ISO 8601.

    Input:  "2026-03-14 06:57:51.594510977 +0000 UTC"
    Output: "2026-03-14T06:57:51.594510+00:00"
    """
    if not raw:
        return ""
    try:
        # Strip " +0000 UTC" or similar timezone suffix
        parts = raw.split(" +")
        if len(parts) >= 2:
            base = parts[0]
        else:
            base = raw.rstrip(" UTC")
        # Replace space with T for ISO format
        base = base.replace(" ", "T", 1)
        # Truncate nanoseconds to microseconds (max 6 decimal places)
        if "." in base:
            main, frac = base.split(".", 1)
            base = main + "." + frac[:6]
        return base
    except Exception:
        return ""


class StreamParser:
    """Stateful parser: raw frames in, `positions` rows out.

    Keeps the static-data cache and the per-vessel throttle that the stream
    itself does not provide. Counters are exposed so a caller can report what a
    collection window actually saw.
    """

    def __init__(self, position_interval_sec: float = POSITION_INTERVAL_SEC):
        self.position_interval_sec = position_interval_sec
        self.static_cache: dict[int, dict] = {}
        self._last_stored: dict[int, float] = {}
        # Counters (a window that yields few rows should say why)
        self.frames = 0
        self.position_reports = 0
        self.static_reports = 0
        self.dropped_on_land = 0
        self.throttled = 0
        self.seen_mmsi: set[int] = set()

    def feed(self, raw) -> tuple | None:
        """Consume one frame. Returns a row tuple, or None if nothing to store."""
        self.frames += 1
        msg = json.loads(raw)
        msg_type = msg.get("MessageType")

        if msg_type == "ShipStaticData":
            self.static_reports += 1
            meta = msg.get("Message", {}).get("ShipStaticData", {})
            mmsi = msg.get("MetaData", {}).get("MMSI")
            if mmsi:
                dim = meta.get("Dimension", {})
                self.static_cache[mmsi] = {
                    "ship_name": meta.get("Name", "").strip(),
                    "ship_type": meta.get("Type"),
                    "destination": meta.get("Destination", "").strip(),
                    "draught": meta.get("MaximumStaticDraught"),
                    "length": dim.get("A", 0) + dim.get("B", 0),
                    "width": dim.get("C", 0) + dim.get("D", 0),
                }
            return None

        if msg_type != "PositionReport":
            return None

        self.position_reports += 1
        pos = msg.get("Message", {}).get("PositionReport", {})
        meta_data = msg.get("MetaData", {})
        mmsi = meta_data.get("MMSI")
        if not mmsi:
            return None

        lat = pos.get("Latitude")
        lon = pos.get("Longitude")
        if lat is None or lon is None:
            return None

        self.seen_mmsi.add(mmsi)

        if is_on_land(lat, lon):
            self.dropped_on_land += 1
            return None

        # Per-vessel throttle. `None`, not 0: time.monotonic() counts from
        # boot, so a 0 sentinel throttles every vessel's first sighting in a
        # process that starts early in a machine's life.
        now_mono = time.monotonic()
        prev = self._last_stored.get(mmsi)
        if prev is not None and now_mono - prev < self.position_interval_sec:
            self.throttled += 1
            return None
        self._last_stored[mmsi] = now_mono

        static = self.static_cache.get(mmsi, {})
        ship_name = (
            meta_data.get("ShipName", "").strip()
            or static.get("ship_name", "")
        )

        now = datetime.now(timezone.utc)

        # Normalize timestamp from aisstream.io format
        # e.g. "2026-03-14 06:57:51.594510977 +0000 UTC"
        # The fallback drops the offset so that `timestamp` is one format
        # throughout: naive UTC, as the archived rows already are. It is
        # `received_at` that carries the offset.
        ts = (normalize_timestamp(meta_data.get("time_utc", ""))
              or now.replace(tzinfo=None).isoformat())

        # Flag: derive from MMSI (aisstream MetaData does not reliably
        # provide country_code)
        flag_code, _ = mmsi_to_flag(mmsi)

        return (
            mmsi,
            ts,
            lat,
            lon,
            pos.get("Sog"),
            pos.get("Cog"),
            pos.get("TrueHeading"),
            ship_name,
            static.get("ship_type"),
            normalize_destination(static.get("destination", "")),
            static.get("draught"),
            static.get("length"),
            static.get("width"),
            flag_code,
            now.isoformat(),
        )

    def summary(self) -> dict:
        """Counters for one collection window."""
        return {
            "frames": self.frames,
            "position_reports": self.position_reports,
            "static_reports": self.static_reports,
            "distinct_mmsi_seen": len(self.seen_mmsi),
            "dropped_on_land": self.dropped_on_land,
            "throttled": self.throttled,
        }
