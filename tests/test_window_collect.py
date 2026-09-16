"""Exercises the collection loop against a local stand-in for aisstream.io.

The loop is the one part of the pipeline that cannot be checked by reading it:
it has a deadline, a reconnect path and a receive timeout, and the first real
run happens against a live stream with a quota. So the stream is faked here.
"""

import asyncio
import json
import sys
import time
import types
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_stub = types.ModuleType("land_filter")
_stub.is_on_land = lambda lat, lon: False
sys.modules["land_filter"] = _stub

try:
    import websockets
except ImportError:  # pragma: no cover - the parser tests still run without it
    websockets = None

if websockets is not None:
    import window_collect


def frame(mmsi, lat=26.0, lon=56.0):
    return json.dumps({
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "ShipName": f"SHIP {mmsi}",
                     "time_utc": "2026-09-16 06:57:51.594510977 +0000 UTC"},
        "Message": {"PositionReport": {"Latitude": lat, "Longitude": lon,
                                       "Sog": 10.0, "Cog": 90.0, "TrueHeading": 91}},
    })


@unittest.skipIf(websockets is None, "websockets is not installed")
class WindowTest(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, handler):
        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        self.addCleanup(server.close)
        window_collect.STREAM_URL = f"ws://127.0.0.1:{port}"
        return server

    async def test_a_window_keeps_one_row_per_vessel(self):
        async def handler(ws):
            await ws.recv()  # the subscription frame
            for mmsi in (111111111, 222222222, 111111111):
                await ws.send(frame(mmsi))
            await asyncio.sleep(5)

        await self._serve(handler)
        rows, summary = await window_collect.collect_window("key", 1.0)

        self.assertEqual(len(rows), 2, "the repeat should have been throttled")
        self.assertEqual(summary["position_reports"], 3)
        self.assertEqual(summary["distinct_mmsi_seen"], 2)
        self.assertEqual(summary["throttled"], 1)
        self.assertEqual(summary["frame_errors"], 0)
        self.assertEqual(summary["reconnects"], 0)

    async def test_the_deadline_is_respected_when_nothing_arrives(self):
        async def handler(ws):
            await ws.recv()
            await asyncio.sleep(30)  # silence for far longer than the window

        await self._serve(handler)
        started = time.monotonic()
        rows, summary = await window_collect.collect_window("key", 1.0)
        elapsed = time.monotonic() - started

        self.assertEqual(rows, [])
        self.assertLess(elapsed, 5.0, f"the window overran: {elapsed:.1f}s")
        self.assertGreaterEqual(elapsed, 1.0)

    async def test_rows_survive_a_dropped_connection(self):
        state = {"connections": 0}

        async def handler(ws):
            state["connections"] += 1
            await ws.recv()
            if state["connections"] == 1:
                await ws.send(frame(111111111))
                await asyncio.sleep(0.2)
                await ws.close()      # drop it mid-window
            else:
                await ws.send(frame(222222222))
                await asyncio.sleep(10)

        await self._serve(handler)
        rows, summary = await window_collect.collect_window("key", 3.0)

        self.assertGreaterEqual(state["connections"], 2, "it never reconnected")
        self.assertGreaterEqual(summary["reconnects"], 1)
        mmsis = {row[0] for row in rows}
        self.assertIn(111111111, mmsis, "rows from before the drop were lost")
        self.assertIn(222222222, mmsis)

    async def test_a_malformed_frame_does_not_cost_the_window(self):
        async def handler(ws):
            await ws.recv()
            await ws.send("this is not json")
            await ws.send(frame(111111111))
            await asyncio.sleep(5)

        await self._serve(handler)
        rows, summary = await window_collect.collect_window("key", 1.0)

        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["frame_errors"], 1)

    async def test_the_subscription_carries_the_key_and_the_bounding_box(self):
        seen = {}

        async def handler(ws):
            seen["subscribe"] = json.loads(await ws.recv())
            await asyncio.sleep(5)

        await self._serve(handler)
        await window_collect.collect_window("SECRET-KEY", 1.0)

        self.assertEqual(seen["subscribe"]["APIKey"], "SECRET-KEY")
        self.assertEqual(seen["subscribe"]["BoundingBoxes"], [[[22.0, 48.0], [30.5, 60.0]]])
        self.assertEqual(sorted(seen["subscribe"]["FilterMessageTypes"]),
                         ["PositionReport", "ShipStaticData"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
