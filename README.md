# Strait of Hormuz — Maritime Monitor

Real-time vessel tracking and maritime intelligence for the Persian Gulf, Strait of Hormuz, and Gulf of Oman.
Continuously monitors shipping patterns using AIS data, with automated transit detection, vessel state classification, and anomaly analysis.

Runs 24/7 on Raspberry Pi 5.

![Live Map](docs/screenshot.png)

### Latest Snapshot (auto-updated every 6 hours)

![Latest Snapshot](docs/snapshot_latest.png)

## What This Monitors

- **Strait transit rate** — virtual gate lines detect vessels entering/leaving the Persian Gulf and major ports
- **Vessel state** — anchored, maneuvering, transiting (speed-based + geofence classification)
- **Anchorage congestion** — named zones (Fujairah, Dubai, Bandar Abbas, etc.) with vessel counts
- **Waiting fleet** — vessels stationary for 6h+ / 24h+ (indicates disruption)
- **Flag state & destination analysis** — MMSI-based country detection, AIS destination normalization
- **Situation assessment** — data-driven status: NO TRANSIT / LIMITED / ACTIVE, auto-adapts to conditions

## Architecture

```
aisstream.io (WebSocket)
  → Land Filter (Natural Earth 10m + Shapely)
  → SQLite
  → Analytics Engine (transit detection, vessel classification)
  → FastAPI + Leaflet.js + Chart.js
  → matplotlib snapshot → GitHub (every 6h)
```

## Key Features

- Real-time vessel positions (30-sec refresh) with type/state color coding
- **3 virtual gate lines**: Strait of Hormuz, Dubai/Jebel Ali Approach, Fujairah Approach
- **Transit event detection** (INBOUND/OUTBOUND) with 6-hour deduplication
- Hourly transit chart (Chart.js, stacked IN/OUT)
- **Data-driven situation report** — severity and description auto-generated from traffic patterns
- Anchorage zone congestion monitoring (11 defined zones)
- Flag state distribution (MMSI MID → 100+ countries)
- Destination normalization (40+ AIS variants → canonical port names)
- Land mask filtering (Natural Earth 10m polygons)
- Track history visualization (6-hour trail per vessel)
- Auto-snapshot with gate lines, transit stats, and crisis context
- **Database migration tool** for timestamp/flag/destination backfill

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Live map + analytics dashboard |
| `GET /api/latest` | Latest position per vessel (last 30 min) |
| `GET /api/tracks/{mmsi}?hours=6` | Position history for a vessel |
| `GET /api/stats` | Active vessels, type breakdown |
| `GET /api/analytics/transits?hours=24&gate=` | Transit events (optional gate filter) |
| `GET /api/analytics/hourly?hours=48&gate=` | Hourly transit counts for charting |
| `GET /api/analytics/states` | Vessel state classification |
| `GET /api/analytics/blockade` | Traffic analysis: waiting fleet, anchored ratio, situation assessment |
| `GET /api/analytics/flags?hours=24` | Flag state distribution |
| `GET /api/analytics/destinations?hours=24` | Destination distribution |
| `GET /api/analytics/gate` | Gate lines, anchorage zones, danger zone, crisis timeline |
| `GET /api/analytics/summary` | Comprehensive daily summary |

## Quick Start

```bash
git clone https://github.com/yasumorishima/hormuz-ship-tracker.git
cd hormuz-ship-tracker

cp .env.example .env
# Edit .env: add your aisstream.io API key (free at https://aisstream.io/)

docker-compose up -d --build
# Open http://localhost:8002

# First run: fix historical data (timestamps, flags, destinations)
docker exec hormuz-tracker python src/migrate.py
```

## Tech Stack

- Python 3.12 / FastAPI / uvicorn / aiosqlite
- WebSocket client (aisstream.io)
- SQLite (positions + transit_events + analytics_state)
- Leaflet.js + Chart.js + CARTO dark tiles
- matplotlib (auto-snapshot)
- Shapely + Natural Earth 10m (land filtering)
- Docker on Raspberry Pi 5

## Roadmap

- **Time-series trend analysis** — daily/weekly transit counts, anchored ratio over time to track how conditions evolve
- **Satellite AIS integration** — terrestrial coverage misses mid-strait traffic; satellite data would fill the gap
- **Historical baseline comparison** — establish "normal" traffic patterns to quantify deviations
- **Automated daily report** — generate and push a text/image summary of the day's maritime activity
- **SQLite periodic purge** — retain summarized stats, drop raw positions older than N days to manage DB size
- **Cloudflare Tunnel** — expose the dashboard publicly without a static IP
- **Timelapse animation** — generate video of vessel movements over 24h/7d periods
- **Additional gate lines** — Bab el-Mandeb, Suez approach, or other chokepoints using the same infrastructure

## Data Source

Ship position data: [aisstream.io](https://aisstream.io/) (free WebSocket API, terrestrial AIS receivers).
Note: terrestrial AIS coverage is limited in open water — satellite AIS provides more complete coverage.

## Related

Part of the [Realtime Open Data](https://github.com/yasumorishima/realtime-open-data) project collection.

## License

MIT
