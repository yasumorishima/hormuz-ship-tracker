# Collection pipeline

The monitor used to be a pair of containers on a Raspberry Pi 5: one held the
aisstream.io WebSocket open around the clock, the other pushed a snapshot to
this repository every six hours. The Pi is no longer part of it. Nothing here
runs on hardware we own.

```
GitHub Actions (collect.yml, every ~15 min)
  → open the aisstream.io stream for 180 s
  → land filter (Natural Earth 10m + Shapely)
  → keep one position per vessel for the window
  → raw/<YYYY-MM-DD>/<HHMMSS>.parquet on the Hub dataset

GitHub Actions (compact.yml, daily)
  → merge a finished day of shards into daily/<YYYY-MM-DD>.parquet
  → the add and the deletes are one commit

GitHub Actions (publish.yml, every 3 h)
  → rebuild a throwaway SQLite database (positions only) from recent parquet
  → snapshot.py / heatmap.py / stats_report.py, unchanged
  → commit docs/ back to this repository
```

Dataset: [yasumorishima/hormuz-ais](https://huggingface.co/datasets/yasumorishima/hormuz-ais)

The map at
[yasumorishima.github.io/hormuz-ship-tracker](https://yasumorishima.github.io/hormuz-ship-tracker/)
is not part of this loop. It is a static page on GitHub Pages that reads the
newest file in the dataset directly from the browser, so it is as fresh as the
last collection window rather than as fresh as the last publish, and it stays
up whether or not any of these workflows ran.

## Why a window instead of a stream

aisstream.io only pushes. There is no endpoint that answers "which vessels are
in the Gulf right now", so a schedule cannot poll for the current fleet the way
the other data pipelines here poll an API. What a scheduled job can do is open
the connection for a few minutes and keep what arrives.

That changes what the data is. The Pi recorded a **continuous track** — every
vessel every two minutes, without gaps. A window records a **sample**: the
vessels that happened to transmit while the connection was open. Class A
transponders repeat every few seconds under way and every few minutes at
anchor, so a three-minute window sees most of the active fleet, but the gap
between windows is not observed at all.

Consequences worth stating plainly:

- Fleet composition, anchored ratio, port and flag breakdowns survive, but not
  for free. A vessel's description — name, type, destination, dimensions —
  arrives in its own frame every few minutes, so it frequently lands in a
  different window from the positions it describes, and the Pi's answer was a
  cache that lived for months. Here `src/enrich.py` does the equivalent when
  the working database is built: per vessel, the most recent non-empty value
  anywhere in the loaded range is copied onto the rows that lack it. Nothing is
  invented — a value is only ever copied between rows of the same MMSI — but it
  does mean the raw shards on the Hub carry more nulls than the Pi's database
  did, and a vessel never described in the loaded range stays undescribed.
- Gate-crossing transit detection does not run at all here. Nothing in the
  three workflows invokes `analytics.py`, and only `positions` is rebuilt, so
  `transit_events` does not exist on this path — the transit figures in
  `STATS.md` are zero by construction, not by observation. Even if the engine
  were run, the sampling would weaken it: a crossing is inferred from
  consecutive positions either side of a gate line, and the gap between
  windows is unobserved.
- Speed and course come from the transponder, not from differencing our own
  positions, so they are unaffected.

## Why not a Hugging Face Space

A Space could hold the connection open the way the Pi did. Docker and Gradio
Spaces run on compute and require a paid plan for personal accounts, so that
route is not free and is not used here. Static Spaces are free, which is the
option available for serving a map.

## The feed has no coverage here at present

Measured 2026-09-16, the day the collector first ran with a live key:

| subscription | result |
|---|---|
| the collection box | 2 position reports in 13 minutes, both inside the land mask |
| the same box, corners reversed | 0 |
| `[[0, 30], [40, 80]]` — Arabian Sea to the Red Sea | 158 vessels in 90 s, all near Suez |
| the whole world, 180 s | **19,261 positions, 12,312 vessels, 0 inside the strait** |

The busiest ten-degree cells were `50N/0E` (7,543) and `50N/10E` (1,978):
aisstream is a European terrestrial network at the moment. The key is fine —
the subscription is confirmed, with compression negotiated — and the code is
the same code that filled `positions.parquet`. There are no receivers in this
water.

This is also the likeliest explanation for the archive stopping on
**2026-04-11**, which was recorded at the time as a suspected collector fault.

So an empty window is a warning rather than a failure: the collector exits 0
when the subscription was confirmed and no positions arrived, and `publish.yml`
says "nothing to publish" and skips rendering. Ninety-six red runs a day over
someone else's coverage would only teach us to stop reading them. A window
that never gets a confirmation still fails.

Collection keeps running, so data resumes by itself if coverage returns.

## Scheduling is best-effort

GitHub delays scheduled workflows under load and drops them outright during
incidents; the cron here is written off the hour for that reason. A missed run
is a gap in the sample, not an error, and nothing downstream assumes the
windows are evenly spaced.

## Secrets

| Secret | Used by | Where it comes from |
|---|---|---|
| `AISSTREAM_API_KEY` | collect.yml | free key from https://aisstream.io/ |
| `HF_TOKEN` | all three | a write token for the Hub dataset |

Until both are set, the scheduled workflows post a warning and do nothing.
An unconfigured repository is not a broken one, and a job that fails every
fifteen minutes teaches you to ignore it.

## Running a window by hand

Install `requirements-collect.txt`, then:

```bash
# measure only — connects, reports what it saw, uploads nothing
AISSTREAM_API_KEY=... python src/window_collect.py --seconds 180 --probe

# collect and upload
AISSTREAM_API_KEY=... HF_TOKEN=... python src/window_collect.py --seconds 180
```

The probe output reports frames, position reports, distinct MMSI seen, how many
were dropped on land and how many rows were kept. That is the measurement that
decides how long a window needs to be.

## Radar, because the feed has no receivers here

AIS only exists if somebody is listening. Sentinel-1 does not need anybody to
be listening, and it does not need the ship to be transmitting either. Since
the feed went quiet, `sar-collect.yml` runs a second collector beside the
first one; the AIS jobs are untouched and resume on their own if coverage
comes back.

### What the satellites actually give

Measured 2026-09-17 by asking Planetary Computer's STAC endpoint:

| | |
|---|---|
| Scenes covering the centre of the strait (56.40E, 26.55N) | 8 in 16 days — about one every two days |
| Scenes touching the AOI box | 43 in 30 days |
| Satellites | S1C and S1D, ascending near 14:16Z and descending near 02:10Z |
| Latency | the 2026-09-16T02:06Z scene was queryable the same day |
| Account needed | none: both the STAC search and the SAS signing answer anonymously |
| One scene | a 702 MB COG, 1024-pixel tiles, ZSTD, six overview levels, byte ranges served |

So a 200 km box can be cut out of a 702 MB file in well under a minute, and
the whole thing is free and keyless. What it is not is continuous: a scene is
an instant, two days apart, not a track.

### Where the work actually is

Not in finding bright things on dark water — in not calling the land a ship.
Measured on S1C 2026-09-16T02:06Z over the strait, same threshold throughout,
counting objects of vessel size and shape standing in what each mask calls
water:

| Coastline | 200 m off the coast | 500 m | 1,000 m |
|---|---:|---:|---:|
| Natural Earth 10m (what the AIS side uses) | 698 | 533 | 292 |
| GSHHG full resolution | 122 | 39 | 30 |
| **ESA WorldCover 10m** | **45** | **30** | **22** |

The fraction of "sea" brighter than 270 DN falls from 0.21% to 0.0084% between
the first row and the last. Natural Earth generalises the Musandam fjords
away, so their water reads as land and the ridges beside them read as sea;
`data/land_mask.geojson` puts the head of Khawr ash Shamm on dry ground, and
`tests/test_sar_mask.py` asserts that it does, because that is the reason a
second mask file exists at all.

WorldCover was chosen over GSHHG on two counts: it leaves fewer objects
standing at every buffer, and it is CC BY 4.0, where GSHHG is LGPL v3 —
`LICENSE.TXT` and `COPYING.LESSERv3` inside the distribution, not public
domain as is often repeated. `data/sar_water_mask.tif` is 417 KB: one bit per
10 m pixel over the AOI, built by `scripts/generate_sar_water_mask.py`.

Terrain is not corrected in a GRD product, so a 1,800 m ridge is laid over
toward the sensor by roughly h/tan(theta) — one to three kilometres. Rather
than dilate the coast by three kilometres and lose every anchorage, each
detection carries `dist_to_land_km` and the decision is left open. Measured on
the same scene, detections run at 7.8 per 100 km² within a kilometre of the
shore against 0.31 to 0.45 further out, so the near-shore band is mixed and
says so.

### Does it find ships? Measured against AIS

The strait cannot answer that question: the published archive holds 143 rows
inside the SAR box in 28 days, because the terrestrial feed barely reached it.
Off Dubai it holds 140,555, so that is where the detector was checked, on two
scenes from inside the archive's own window.

| Scene | AIS vessels in scored water | found within 300 m |
|---|---:|---:|
| S1C 2026-03-18T02:14:46Z (descending) | 15 | **15** |
| S1C 2026-03-15T14:24:00Z (ascending) | 8 | **8** |

Positions are dead-reckoned from the nearest report within two minutes. The
denominator is the vessels the detector was allowed to see: nine of the 24 in
the first scene were berthed inside the coast buffer. `K_SIGMA` was chosen on
these numbers — 6, 8 and 10 all give 23 of 23, and 10 returns a third fewer
candidates than 6, so 10 it is.

Two things this does **not** measure. It says nothing about precision: the
archive is a sample of transmitting vessels, not a census, so an unmatched
detection is not a false alarm — it may be a buoy, a rig, or a ship with its
transponder off, which is the interesting case. And it was measured off Dubai,
not in the strait.

### What gets stored

Two tables, keyed by scene, under the detector's version:

```
sar/det/v1/<scene_id>.parquet      one row per candidate
sar/scenes/v1/<scene_id>.parquet   one row for the scene
```

There is no ledger of what has been processed: a scene is done when its scene
row exists. A ledger can disagree with the files; this cannot. The scene row
is written even when nothing was found, so "no vessels" and "never looked"
stay different — the absence that made the AIS side's transit count silently
zero.

Every candidate keeps what it was measured on — `bg_median_dn`, `bg_mad_dn`,
`snr`, `area_px`, `length_m`, `dist_to_land_km` — and the ones that failed the
shape test are kept too, with `is_vessel` false and a `reject_reason`. A
looser detector can then be run over the table instead of over 700 MB scenes,
and the version in the path means the new answers sit beside the old ones
rather than on top of them.

### Running it by hand

```bash
pip install -r requirements-sar.txt

# what would be processed, without writing to the Hub
python src/sar_collect.py --hours 72 --dry-run

# one real scene, no credentials, asserting what was measured
python scripts/sar_smoke.py
```
