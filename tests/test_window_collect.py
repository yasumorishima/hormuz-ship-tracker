"""Exercises the collection loop against a local stand-in for aisstream.io.

The loop is the one part of the pipeline that cannot be checked by reading it:
it has a deadline, a reconnect path and a receive timeout, and the first real
run happens against a live stream with a quota. So the stream is faked here.
"""

import asyncio
import json
import os
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


async def hold(ws):
    """Keep the connection open until the client closes it.

    Bounded, because a client bug that stops it closing must fail the test
    rather than hang the suite — a hung test tells you nothing.
    """
    try:
        await asyncio.wait_for(ws.wait_closed(), timeout=20)
    except asyncio.TimeoutError:
        pass


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
    async def asyncSetUp(self):
        import ais_parse

        # ais_parse bound `is_on_land` at import time, from whichever stub was
        # installed first — under discovery that is test_ais_parse's, whose
        # answer is a module global another test flips to True. Replacing
        # sys.modules["land_filter"] here would be too late to matter, so
        # patch the name the parser actually calls.
        real = ais_parse.is_on_land
        ais_parse.is_on_land = lambda lat, lon: False
        self.addCleanup(setattr, ais_parse, "is_on_land", real)

        # Fail closed: a test that forgets _serve must not reach the real
        # stream, which is the thing this file exists to avoid.
        self.addCleanup(setattr, window_collect, "STREAM_URL",
                        window_collect.STREAM_URL)
        window_collect.STREAM_URL = "ws://127.0.0.1:1"

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
            await hold(ws)

        await self._serve(handler)
        rows, summary = await window_collect.collect_window("key", 3.0)

        self.assertEqual(len(rows), 2, "the repeat should have been throttled")
        self.assertEqual(summary["position_reports"], 3)
        self.assertEqual(summary["distinct_mmsi_seen"], 2)
        self.assertEqual(summary["throttled"], 1)
        self.assertEqual(summary["frame_errors"], 0)
        self.assertEqual(summary["reconnects"], 0)
        self.assertEqual(summary["connect_errors"], 0)

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
        state = {"connections": 0, "dropped_at": None, "reconnected_at": None}

        async def handler(ws):
            state["connections"] += 1
            await ws.recv()
            if state["connections"] == 1:
                await ws.send(frame(111111111))
                await asyncio.sleep(0.2)
                state["dropped_at"] = time.monotonic()
                await ws.close()      # drop it mid-window
            else:
                state["reconnected_at"] = time.monotonic()
                await ws.send(frame(222222222))
                await hold(ws)

        await self._serve(handler)
        # Two handshakes plus the server's 0.2 s plus the 1 s backoff have to
        # fit, and a contended runner is not fast.
        rows, summary = await window_collect.collect_window("key", 6.0)

        self.assertGreaterEqual(state["connections"], 2, "it never reconnected")
        self.assertGreaterEqual(summary["reconnects"], 1)
        gap = state["reconnected_at"] - state["dropped_at"]
        self.assertLess(gap, 2.5, f"the first retry waited {gap:.1f}s")
        mmsis = {row[0] for row in rows}
        self.assertIn(111111111, mmsis, "rows from before the drop were lost")
        self.assertIn(222222222, mmsis)

    async def test_a_malformed_frame_does_not_cost_the_window(self):
        async def handler(ws):
            await ws.recv()
            await ws.send("this is not json")
            await ws.send(frame(111111111))
            await hold(ws)

        await self._serve(handler)
        rows, summary = await window_collect.collect_window("key", 1.0)

        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["frame_errors"], 1)

    async def test_the_subscription_carries_the_key_and_the_bounding_box(self):
        seen = {}

        async def handler(ws):
            seen["subscribe"] = json.loads(await ws.recv())
            await hold(ws)

        await self._serve(handler)
        await window_collect.collect_window("SECRET-KEY", 1.0)

        self.assertEqual(seen["subscribe"]["APIKey"], "SECRET-KEY")
        self.assertEqual(seen["subscribe"]["BoundingBoxes"], [[[22.0, 48.0], [30.5, 60.0]]])
        self.assertEqual(sorted(seen["subscribe"]["FilterMessageTypes"]),
                         ["PositionReport", "ShipStaticData"])

    async def test_repeated_refusals_back_off_instead_of_hammering(self):
        """A rejected key closes the socket at once, every time.

        With a flat retry that is one connection per second for the whole
        window, ninety-six windows a day, against a free service that is
        entitled to rate-limit us for it.
        """
        state = {"connections": 0}

        async def handler(ws):
            state["connections"] += 1
            await ws.close()

        await self._serve(handler)
        rows, summary = await window_collect.collect_window("key", 8.0)

        self.assertEqual(rows, [])
        self.assertGreaterEqual(summary["connect_errors"], 3, "it stopped trying")
        # 1 + 2 + 4 s of backoff fills an 8 s window in about four attempts;
        # a flat one-second retry would make eight or so.
        self.assertLessEqual(summary["reconnects"], 5,
                             f"{summary['reconnects']} retries in 8s is a storm")

    async def test_a_stalled_handshake_does_not_outlive_the_window(self):
        """Accept the TCP connection and never answer it.

        Measured against websockets 15.0.1: the library waits 10 s by default,
        which is longer than many windows and is not bounded by anything the
        caller sets unless open_timeout is passed.
        """
        async def stall(reader, writer):
            await asyncio.sleep(60)

        server = await asyncio.start_server(stall, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        self.addCleanup(server.close)
        window_collect.STREAM_URL = f"ws://127.0.0.1:{port}"

        started = time.monotonic()
        rows, summary = await window_collect.collect_window("key", 2.0)
        elapsed = time.monotonic() - started

        self.assertEqual(rows, [])
        self.assertLess(elapsed, 6.0,
                        f"the handshake outlived the window: {elapsed:.1f}s")
        # Not `reconnects`: running out of window is not a reconnect. What
        # matters is that the attempt was made, failed, and was recorded.
        self.assertGreaterEqual(summary["connect_errors"], 1,
                                "the stalled handshake was not recorded")


@unittest.skipIf(websockets is None, "websockets is not installed")
class EmptyWindowTest(WindowTest):
    """An empty window has two causes and only one of them is ours."""

    async def run_main(self, handler, seconds=2.0):
        await self._serve(handler)
        argv = sys.argv
        sys.argv = ["window_collect", "--probe", "--seconds", str(seconds)]
        os.environ["AISSTREAM_API_KEY"] = "test"
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, window_collect.main)
        finally:
            sys.argv = argv

    async def test_a_confirmed_subscription_with_no_positions_is_not_a_failure(self):
        async def handler(ws):
            await ws.recv()
            await ws.send('{"MessageType":"SubscriptionConfirmation",'
                          '"Message":{"CompressionEnabled":true}}')
            await hold(ws)

        self.assertEqual(await self.run_main(handler), 0,
                         "someone else's missing coverage reddened our run")

    async def test_a_stream_that_says_nothing_at_all_is_a_failure(self):
        async def handler(ws):
            await ws.recv()
            await hold(ws)

        self.assertEqual(await self.run_main(handler), 1,
                         "a silent stream with no confirmation should fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
