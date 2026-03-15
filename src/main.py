"""Entry point: run AIS collector, analytics engine, and web server concurrently."""

import asyncio
import logging

import uvicorn

from analytics import transit_detection_loop
from collector import collect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def run_server():
    """Run FastAPI server."""
    config = uvicorn.Config("api:app", host="0.0.0.0", port=8002, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Run collector, analytics, and web server in parallel."""
    await asyncio.gather(
        collect(),
        run_server(),
        transit_detection_loop(interval_sec=300),
    )


if __name__ == "__main__":
    asyncio.run(main())
