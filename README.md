# Strait of Hormuz — Live Ship Tracker

Real-time vessel tracking in the Strait of Hormuz using AIS (Automatic Identification System) data.
Runs 24/7 on Raspberry Pi 5, collecting and visualizing live maritime traffic.

### Latest Snapshot (auto-updated every 6 hours)

![Latest Snapshot](docs/snapshot_latest.png)

## Architecture

```
aisstream.io (WebSocket) → Raspberry Pi 5 → SQLite → FastAPI + Leaflet.js
                                                   → matplotlib snapshot → GitHub (every 6h)
```

- **Data Source**: [aisstream.io](https://aisstream.io/) — free, real-time global AIS stream
- **Collection**: Python WebSocket client, filtered to Strait of Hormuz bounding box
- **Storage**: SQLite (lightweight, no external DB needed)
- **Visualization**: Leaflet.js dark map with vessel type color coding, track history, auto-refresh
- **Auto Snapshot**: matplotlib generates a map image every 6 hours and pushes to this repo

## Features

- Real-time vessel positions updated every 30 seconds
- Color-coded by vessel type (Tanker, Cargo, Passenger, Military, etc.)
- Click any vessel to see name, speed, course, destination, flag, and size
- Track history visualization (6-hour trail per vessel)
- Statistics dashboard (active vessels, total records, type breakdown)
- Auto-reconnect on connection loss
- Auto-snapshot pushed to GitHub every 6 hours (only if data changed)

## Quick Start

```bash
# Clone
git clone https://github.com/yasumorishima/hormuz-ship-tracker.git
cd hormuz-ship-tracker

# Set API key (free registration at https://aisstream.io/)
cp .env.example .env
# Edit .env and add your aisstream.io API key

# Run
docker-compose up -d --build

# Open http://localhost:8002
```

### Auto Snapshot (optional)

To enable automatic snapshot generation and push to GitHub, add to `.env`:

```
GITHUB_TOKEN=your_personal_access_token
GITHUB_REPO=your_username/hormuz-ship-tracker
```

The `snapshot-cron` container generates a map image every 6 hours and pushes it to the repo.

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Live map UI |
| `GET /api/latest` | Latest position per vessel (last 30 min) |
| `GET /api/tracks/{mmsi}?hours=6` | Position history for a vessel |
| `GET /api/stats` | Active vessels count, type breakdown |

## Tech Stack

- Python 3.12 / FastAPI / uvicorn
- WebSocket (aisstream.io)
- SQLite (aiosqlite)
- Leaflet.js + CARTO dark tiles
- matplotlib (auto-snapshot)
- Docker on Raspberry Pi 5

## Data Source

Ship position data is provided by [aisstream.io](https://aisstream.io/) via their free WebSocket API.
AIS (Automatic Identification System) is a maritime safety system that broadcasts vessel position, speed, course, and identification.

## License

MIT
