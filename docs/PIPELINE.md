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

| Coastline | vessels at 200 m | at 500 m | at 1,000 m |
|---|---:|---:|---:|
| Natural Earth 10m (what the AIS side uses) | 612 | 508 | 357 |
| GSHHG full resolution | 407 | 199 | 155 |
| **ESA WorldCover 10m** | **233** | **176** | **125** |

The totals understate it, because most of that water is nowhere near a coast.
Split by distance to land, at a 200 m buffer, in vessels per 100 km²:

| km from land | Natural Earth | WorldCover |
|---|---:|---:|
| 0.2 – 1 | 162.6 | **36.4** |
| 1 – 2 | 56.2 | **4.9** |
| 2 – 5 | 19.3 | **4.0** |
| 5 – 10 | 6.5 | **4.9** |
| beyond 10 | 0.97 | 0.84 |

In open water the two masks agree, as they must — out there the coastline is
not in the picture at all. Everything the second mask buys is within a few
kilometres of the shore, and there it is a factor of three to four. Natural
Earth generalises the Musandam fjords away, so their water reads as land and
the ridges beside them read as sea; `data/land_mask.geojson` puts the head of
Khawr ash Shamm on dry ground, and `tests/test_sar_mask.py` asserts that it
does, because that is the reason a second mask file exists at all.

WorldCover was chosen over GSHHG on two counts: it leaves fewer objects
standing at every buffer, and it is CC BY 4.0, where GSHHG is LGPL v3 —
`LICENSE.TXT` and `COPYING.LESSERv3` inside the distribution, not public
domain as is often repeated. `data/sar_water_mask.tif` is 435 KiB: one bit per
10 m pixel over the AOI, built by `scripts/generate_sar_water_mask.py`.

Terrain is not corrected in a GRD product, so a 1,800 m ridge is laid over
toward the sensor by roughly h/tan(theta) — one to three kilometres. Rather
than dilate the coast by three kilometres and lose every anchorage, each
detection carries `dist_to_land_km` and the decision is left open. Measured on
the same scene, detections run at 36 per 100 km² within a kilometre of the
shore against 0.8 beyond ten, so the near-shore band is mixed and says so.

### Does it find ships? Measured against AIS

The strait cannot answer that question: the published archive holds 143 rows
inside the SAR box in 28 days, because the terrestrial feed barely reached it.
Off Dubai it holds 140,555, so that is where the detector was checked, on two
scenes from inside the archive's own window.

| Scene | AIS vessels in scored water | found within 300 m |
|---|---:|---:|
| S1C 2026-03-18T02:14:46Z (descending) | 16 | **16** |
| S1C 2026-03-15T14:24:00Z (ascending) | 8 | **8** |

Positions are dead-reckoned from the nearest report within two minutes. The
denominator is the vessels the detector was allowed to see: eight of the 24
in the first scene were berthed inside the coast buffer. `K_SIGMA` was chosen
on these numbers — 8 and 10 both give 24 of 24, 12 loses one, and 10 returns
a sixth fewer candidates than 8, so 10 it is.

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
`snr`, `area_px`, `length_m`, `orientation_deg`, `dist_to_land_km` — and the
ones that failed the shape test are kept too, with `vessel_sized` false and a
`reject_reason`. A looser detector can then be run over the table instead of
over 700 MB scenes, and the version in the path means the new answers sit
beside the old ones rather than on top of them.

The flag is `vessel_sized`, not `is_vessel`. Recall was measured; precision was
not, and cannot be from this data. Measured on the first run, over the whole
AOI: of 2,931 candidates in one scene, 1,633 were vessel-sized, 1,240 too
small and 58 too long — and 48% of the vessel-sized ones were within a
kilometre of the shore. A count of them is not a count of ships.

### What the map shows, and why

Measured 2026-11-07 by `python src/sar_survey.py --vh` (needs the test
requirements plus pandas) over every scene then on the Hub: 74 scenes with
status `ok` in 37 passes (09-09 to 11-06), 98,541 candidates, 56,816
vessel-sized. The first measurement, 2026-09-28 over 17 scenes in 9 passes,
gave the same picture; the numbers below are the later ones. The raw output is `docs/sar_survey.json`. Water is the scene
footprint from the STAC item intersected with the water mask beyond the 200 m
buffer; the per-scene totals agree with the stored `scored_water_km2` to a few
per cent.

| km from land | scored water, km² (summed over scenes) | vessel-sized | per 100 km² | SNR p10 / median / p90 | SNR > 20 |
|---|---:|---:|---:|---|---:|
| 0.2 – 1 | 27,541 | 27,072 | **98.3** | 11.3 / 14.8 / 33.3 | 27% |
| 1 – 3 | 50,103 | 7,207 | **14.4** | 11.2 / 14.8 / 74.1 | 32% |
| 3 – 10 | 131,832 | 8,106 | **6.2** | 11.3 / 17.8 / 248 | 47% |
| beyond 10 | 319,879 | 14,431 | **4.5** | 11.0 / 13.5 / 150 | 23% |

Finer, near the shore (09-28, 17 scenes): 167 per 100 km² at 0.2–0.5 km, 39 at 0.5–1, 17 at
1–2, 6.2 at 2–3, then 4.1 at 3–5 and 5.4 at 5–10 — the open-sea level. So
**3 km is where the shore stops adding detections**, and that is the map's
default (`MAP_MIN_SHORE_KM` in `src/sar_columns.py`, copied in `docs/map.js`).
Nearly half (48%) of all vessel-sized detections are in the 0.2–1 km band,
which is 5% of the water. The 3–10 km band runs a third above the open-sea
rate; part of that is the slot-bound recurrence described under precision. SNR does not separate the bands — the median is 13.5–18 in all of
them — so it cannot be used to clean the shore.

It does separate something else. The detector runs on VV; each scene also has
VH, whose noise is independent of VV's. A hull, a platform or a rock usually
stands out in both; speckle and sea clutter rarely do in VH. Re-reading VV and
VH for all 74 scenes (the shipped detector reproduced every stored table
exactly) and asking whether VH stands `> bg + 5·sd` within two pixels of each
detection, against the same question 500 m away:

| km from land | stratum | n | VH confirms | control | real scatterers, lower bound |
|---|---|---:|---:|---:|---:|
| 0.2 – 1 | all | 27,072 | 69% | 19% | 62% |
| 1 – 3 | SNR ≤ 20 | 4,875 | 31% | 8% | 25% |
| 1 – 3 | SNR > 20 | 2,332 | 83% | 8% | 81% |
| 3 – 10 | SNR ≤ 20 | 4,295 | 9.9% | 0.6% | 9.3% |
| 3 – 10 | SNR > 20 | 3,811 | **93%** | 0.6% | **93%** |
| beyond 10 | SNR ≤ 20 | 11,098 | **4.3%** | 0.5% | **3.9%** |
| beyond 10 | SNR > 20 | 3,333 | **84%** | 0.5% | **83%** |

The bound is f = p·q + (1−p)·c ≤ p + (1−p)·c, so p ≥ (f − c)/(1 − c) whatever
the VH detectability q of a real target is. It assumes a VV detection that is
not a real scatterer passes the VH test at the random-water rate c; where VV
false alarms sit on things also bright in VH (surf, rain cells, wakes,
layover) they pass more often and the bound overstates. Near the shore c is
already 8–20%, so the 0.2–3 km bounds are optimistic, and they say little
anyway: terrain is a real scatterer too, so VH confirms it. Beyond 3 km c is
under 1%. Out at sea it splits the
detections cleanly — beyond 3 km, 89% of those above SNR 20 are confirmed
and 6% of those at or below it (on 09-28, beyond 10 km, 99.6% of those above
SNR 100 and 1.4% of those at SNR 10–12). The weak ones are either small craft that VH
does not see (small wooden and GRP boats are common here) or clutter; nothing
in these tables can say which. So the map draws, by default, vessel-sized
detections **3 km or more from shore with SNR over 20** (`MAP_MIN_SNR`), and
keeps the weak ones and the two near-shore bands as layers that are off until
asked for. On the 09-27 pass that is 252 by default, 1,400 weak, 1,344 nearer
the shore.

The map draws only the newest pass, — the newest one whose scene rows say
`ok`, so a pass that only clipped a corner is skipped — as its own layer with
its own status line and its own failure. The status line gives the share of
the box the pass saw, because the rest is unobserved, not empty. The page says that a circle is not a ship, that a
pass is one instant about every two days, and that within 1 km terrain and
hulls are mixed. The AIS 24-of-24 recall was measured off Dubai with the full
detector, not with the SNR 20 display cut; the cut may hide real small boats,
which is why it is a default and not a filter on the data.

### Precision in the strait, without AIS

Precision needs a truth for every detection, and in the strait there is none:
AIS has no receivers there, and even where it has, an unmatched detection may
be a ship with its transponder off. So the question is split into parts that
can each be answered from the radar alone.

1. **Is it a real scatterer, or noise?** The VH test above. It gives a lower
   bound on the real fraction per band and SNR stratum, with a measured
   chance level. Out at sea: at least 83–93% for SNR > 20, at least 3.9–9.3%
   for SNR ≤ 20.
2. **Is it fixed, or does it move?** A hull under way is elsewhere two days
   later; a platform, a rock or the layover of a ridge is not. For each
   detection, every *other* pass whose footprint covers it is asked for a
   detection within 100 m (the slices of one pass are one look), and the same
   is asked 500 m away in the same band for chance. Only detections covered
   by at least two other passes are scored. Over the 74 scenes and 37 passes
   of 2026-11-07 (all of them were):

   | km from land | n | seen again within 100 m | chance |
   |---|---:|---:|---:|
   | 0.2 – 1 | 27,072 | 87% | 49% |
   | 1 – 3 | 7,207 | 66% | 39% |
   | 3 – 10 | 8,106 | 28% | 10% |
   | beyond 10 | 14,431 | 11% | 4.7% |

   With four times the passes the chance level is four times higher, so only
   the excess over chance means anything. On 2026-09-28 (17 scenes, 9 passes)
   beyond 10 km read 5.5% against 0.9%, and "at most about 5% fixed" was
   written from it; with more looks and split by SNR, that does not hold for
   the strong detections (below).

   **Split by acquisition slot** (`sar_survey.geometry_of`; 7, 10, 10 and 10
   passes in 02:0x, 02:1x, 14:1x and 14:2x). Passes in one slot share the
   orbit and the incidence angle, so anything tied to the viewing geometry
   recurs within a slot and moves between slots; an object on the water
   recurs in both. Seen again within 100 m, against chance:

   | km from land | SNR | n | same slot | chance | other slots | chance |
   |---|---|---:|---:|---:|---:|---:|
   | 3 – 10 | ≤ 20 | 4,295 | 7.1% | 2.0% | 3.5% | 3.5% |
   | 3 – 10 | > 20 | 3,811 | **42%** | 6.3% | 14% | 10% |
   | beyond 10 | ≤ 20 | 11,098 | 3.7% | 1.9% | 2.6% | 2.1% |
   | beyond 10 | > 20 | 3,333 | **21%** | 2.2% | **15%** | 5.2% |

   The weak detections out at sea do not recur beyond chance: they are
   transient, whatever they are. The strong ones do. At 3–10 km the excess is
   almost all within the slot (+36 points against +4 across slots) and stays
   so at a 300 m radius (+33 against +5): something there is tied to the
   viewing geometry, not to a place on the water. Beyond 10 km both kinds
   recur, and at 300 m the gap narrows (+23 within, +18 across) — part of
   what looked slot-bound at 100 m is a tall object whose bright point shifts
   between ascending and descending views. The recurring strong detections
   are clustered: beyond 10 km, the ten busiest 5 km cells hold 40% of the
   slot-bound ones and 59% of the cross-slot ones, the largest off Fujairah
   (56.6 E, 25.6 N) and west of the strait (55.7–56.0 E, 26.2–26.4 N). An
   anchorage recurs too, with different ships, so **recurring is not the
   same as not a ship**. What makes the 3–10 km band slot-bound is not
   identified. An azimuth ambiguity (a ghost of a bright target about 5 km
   along track, in the same place for the same geometry) was the first guess;
   a quick test — is there land 4.5–6.5 km north or south of the detection —
   found 30% for the slot-bound ones against 23% for those never seen again,
   which does not settle it.

   Of the map's default layer (3 km or more, SNR over 20; 7,144 detections
   over the 37 passes), 24% recur only within their slot at 100 m and 15%
   across slots.

3. **Is a transient real scatterer a vessel?** Not answerable from radar
   alone. What would answer it, in order of cost: AIS where coverage returns
   (a match is a vessel; no match stays unknown); a Sentinel-2 image of the
   same day for the fixed and slow ones (it passes hours before or after
   the radar, so moving ships will have moved); a wake beside the target.
   None of these is implemented.

So, on what is measured: out at sea, the default layer is mostly real
scatterers (89% confirmed in VH beyond 3 km), and 39% of it is seen again
within 100 m on another pass (some of that by chance) — anchorages,
platforms, and something tied to the viewing geometry that is not yet
identified. That is not the same as "89% are ships", and nothing on the page or in the data says it is.

### Catching up

`sar-collect.yml` looks back three weeks and does at most six scenes a run,
**oldest first**, against about 1.4 scenes a day reaching the AOI.

It used to look back 72 hours and take four, newest first, and that lost a
scene for good: the first real run on 2026-09-17 found five, did the newest
four, and by the next day's run the fifth — S1D 2026-09-15T02:14:05Z, the
northern slice of that pass — was older than the window. Nothing hunts for
gaps, because the ledger is the set of files that exist, so a scene that
leaves the window unprocessed is simply never looked at. Oldest first means
the scene about to leave is the one taken; a three-week window means an
outage of up to about two weeks heals by itself. Searching costs one request
whatever the window, and scenes already done are skipped. The search warns if
it ever returns a full page, because the endpoint lists newest first and a
truncated page drops exactly the oldest.

Checked 2026-09-28 against the Hub and the run list: `sar-collect.yml` ran
every day from 09-17 to 09-28 (three manual runs on 09-17, then one scheduled
run a day, all green; the 04:41 cron actually fires between about 09:00 and
11:15 UTC). Planetary Computer lists 21 slices from 09-15 on; the Hub holds
20. The missing one is the slice above. The wider window also reaches the
09-09, 09-10, 09-12 and 09-13 passes that predate the first run: ten slices
to do, the lost one tenth oldest, so it lands on the second run after the
change, while it is about fifteen days old and still inside the window.

### Running it by hand

```bash
pip install -r requirements-sar.txt

# what the next scheduled run would process, without writing to the Hub
python src/sar_collect.py --dry-run

# everything in the window again, overwriting (about 30 slices, 30 s each)
python src/sar_collect.py --redo --max-scenes 40

# one real scene, no credentials, asserting what was measured
python scripts/sar_smoke.py
```
