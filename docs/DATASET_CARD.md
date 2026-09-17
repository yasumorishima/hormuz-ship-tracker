---
pretty_name: Strait of Hormuz AIS
license: other
license_name: see-source-terms
license_link: https://aisstream.io/
language:
  - en
tags:
  - maritime
  - ais
  - vessel-tracking
  - persian-gulf
  - strait-of-hormuz
configs:
  - config_name: default
    data_files:
      # Keep a split with a concrete path first. The other two are globs that
      # match nothing until collection starts, and the feature inference walks
      # these in order — an all-empty prefix fails the whole config.
      # `train` rather than `archive`: this split already exists and is being
      # downloaded, so renaming it would break every split="train" call.
      - split: train
        path: positions.parquet
      - split: daily
        path: daily/*.parquet
      - split: recent
        path: raw/*/*.parquet
  - config_name: transit_events
    data_files:
      - split: train
        path: transit_events.parquet
---

# Strait of Hormuz AIS

Vessel positions in the Persian Gulf, the Strait of Hormuz and the Gulf of
Oman, received from [aisstream.io](https://aisstream.io/) and filtered against
a land mask. Bounding box `[[22.0, 48.0], [30.5, 60.0]]` — Kuwait to Muscat.

Collected by [yasumorishima/hormuz-ship-tracker](https://github.com/yasumorishima/hormuz-ship-tracker),
which is also where the code that wrote every row lives.

## Collection is stalled, and it is not the collector

aisstream.io accepts the subscription and then sends nothing for this area.
Measured 2026-09-16: three minutes of the **whole world** on the same
connection returned **19,261 positions from 12,312 vessels, none of them
inside the strait**; the busiest cells were the North Sea (7,543) and the
Baltic (1,978). Thirteen minutes subscribed directly to the strait produced
two position reports. **The feed has no receivers in this water at present.**

Measured again 2026-09-17: 9,547 vessels worldwide, none inside the strait.

So the AIS splits below hold what the Raspberry Pi collected and nothing
newer. The collector still runs every fifteen minutes, so `raw/` fills by
itself if coverage returns. This is also the likeliest reason the archive
stops on 2026-04-11.

Since 2026-09-17 the same water is watched a second way, with radar — see
**Radar detections** below. Radar needs nobody to be listening and the ship
does not have to be transmitting, so the two are not the same measurement and
are kept in separate files.

## Read this before using it: the rows are a sample, not a track

Until mid-2026 a Raspberry Pi held the AIS stream open around the clock and
stored one position per vessel every two minutes. That machine is gone. The
collector now runs as a scheduled job that **opens the stream for 180 seconds
about every 15 minutes** and keeps one position per vessel per window.

So:

- **the gap between windows is not observed at all.** A vessel that entered,
  crossed and left between two windows leaves no trace.
- **positions are not evenly spaced**, and scheduled jobs are delayed or
  dropped under load, so the spacing is not even nominally regular.
- **speed and course come from the transponder**, not from differencing our
  own positions, so they are unaffected by the sampling.
- **do not compute tracks, transit counts or dwell times by joining
  consecutive rows** without accounting for this. The `train` split is
  continuous; the others are not.

## Splits

| split | what it is |
|---|---|
| `train` | `positions.parquet` — the continuous Raspberry Pi collection: **175,773 rows**, 6.9 MB, **2026-03-14 to 2026-04-11** (28.2 days), **619 vessels**. One position per vessel every two minutes, with no windows to have gaps between. About **9% of rows are anomalous** on the speed filter described below. The name is historical: it is what the auto-detected split was called before this card existed, and renaming it would break existing callers. |
| `daily` | `daily/<YYYY-MM-DD>.parquet` — one file per finished UTC day of the sampled era. |
| `recent` | `raw/<YYYY-MM-DD>/<HHMMSS>.parquet` — one file per collection window for days not yet compacted. Merged into `daily/` once the day is over. |

A second config, `transit_events`, holds the 260 gate crossings inferred during
the Raspberry Pi era. Until this card is published as the dataset's README, the
viewer attributes that file to the same split as the positions, which is why
the row count it reports is 176,033 rather than 175,773. **Nothing appends to it now** — the current pipeline does
not run the transit detector.

## Columns

| column | type | notes |
|---|---|---|
| `mmsi` | int64 | Maritime Mobile Service Identity — the vessel key |
| `timestamp` | string | when the transponder reported, ISO 8601, **naive UTC** |
| `latitude`, `longitude` | float64 | WGS 84 degrees |
| `speed` | float64 | knots, nullable. **102.3 is the AIS "not available" sentinel**, and 40 kn and above are receiver glitches — the published figures filter `speed >= 40`, which subsumes the sentinel |
| `course`, `heading` | float64 | degrees, nullable |
| `ship_name` | string | **empty string when unknown, never null** |
| `ship_type` | float64 | AIS type code, nullable — hence float rather than int |
| `destination` | string | as keyed in by the crew, then normalized. **Empty string when unknown**; often wrong even when present |
| `draught` | float64 | metres, nullable |
| `length`, `width` | float64 | metres, summed from the AIS dimension fields, nullable |
| `flag` | string | ISO country code **derived from the MMSI prefix**, not broadcast. **Empty for prefixes we do not recognise** |
| `received_at` | string | when we received it, ISO 8601 **with a UTC offset** |

Note the asymmetry in the last two: `timestamp` carries no offset and
`received_at` does. Both are UTC.

## Static fields arrive in their own frames

Name, type, destination and dimensions are broadcast separately from
positions, every few minutes. In a 180-second window a vessel's position
often arrives without them, so **rows in `recent` and `daily` carry more
missing values in those columns than the archive does**. Missing looks
different per column: `ship_name` and `destination` come back as **empty
strings**, never null, while `ship_type`, `draught`, `length` and `width` are
**null**. `WHERE ship_name IS NULL` finds nothing. The collector's consumer
fills them per vessel from the most recent non-empty value in the range it
loads (`src/enrich.py` in the repository); the stored rows are left as
received.

## Coverage is terrestrial

The receivers are shore-based, and terrestrial AIS reaches only as far as the
horizon from the antenna — tens of nautical miles, not hundreds. The Strait of
Hormuz is about 33 km (21 miles) wide at its narrowest and considerably wider
along most of its length.

**We have not measured how much of the strait this dataset actually sees.**
What can be said is the direction of the error: a shore-based network
under-reports the middle of a strait, satellite AIS would fix it and is not
free, and vessels can switch their transponder off. So **treat an absent
crossing as unknown, not as evidence that no crossing happened** — and do not
read a transit count here as a count of transits.

## Loading

```python
from datasets import load_dataset

# the continuous Raspberry Pi collection
archive = load_dataset("yasumorishima/hormuz-ais", split="train")

# finished days of the sampled era
daily = load_dataset("yasumorishima/hormuz-ais", split="daily")

# only the windows not compacted into a day yet — today's, usually.
# `daily` + `recent` together are everything the sampled pipeline has written.
recent = load_dataset("yasumorishima/hormuz-ais", split="recent")
```

Or read the files directly, which is lighter:

```python
import pandas as pd
df = pd.read_parquet("hf://datasets/yasumorishima/hormuz-ais/positions.parquet")
```

## Radar detections

Sentinel-1 covers the centre of the strait about once every two days. Each
scene is cut down to the strait, bright compact objects are picked out against
a local background, and the result is written as two tables under the
detector's version:

```
sar/det/v1/<scene_id>.parquet      one row per candidate
sar/scenes/v1/<scene_id>.parquet   one row for the scene, written even when
                                   nothing was found
```

They are deliberately **not** joined to the AIS rows and carry no `mmsi`: a
radar return is a bright object, not an identity. Matching the two by time and
position is a piece of analysis, not a fact in the data.

What the detections are worth, measured against this dataset's own AIS: on two
scenes off Dubai — the one patch of water where the archive is dense — 24 of
the 24 vessels AIS placed in scored water were found within 300 m. That is
recall and nothing else. The archive samples transmitting vessels, so a
detection with no AIS beside it is **not** a false alarm; it may be a buoy, a
rig, or a ship with its transponder off. Each row keeps what it was measured
on (`bg_median_dn`, `bg_mad_dn`, `snr`, `area_px`, `length_m`,
`dist_to_land_km`) and the candidates that failed the shape test are kept too,
with `is_vessel` false and a `reject_reason`, so the judgement can be redone
without the imagery.

Detections within a kilometre of the shore are mixed: terrain is not corrected
in these products, so a ridge is laid over toward the satellite by a kilometre
or more. `dist_to_land_km` is on every row precisely so that this can be
filtered rather than hidden.

## Provenance and terms

Positions are AIS broadcasts, received through aisstream.io's free WebSocket
feed. **Check [aisstream.io](https://aisstream.io/) for their terms before
redistributing**; the `license: other` above is a placeholder for exactly that
reason and is not a grant from us. The land mask is Natural Earth 10m (public
domain).

AIS is self-reported. Vessels can and do transmit wrong names, wrong
destinations, wrong dimensions, and can switch their transponder off.

The radar tables are derived from **modified Copernicus Sentinel data (2026)**,
processed through Microsoft Planetary Computer. The water mask used to build
them is **ESA WorldCover 10m v200 (2021), CC BY 4.0** — © ESA WorldCover
project 2021 / Contains modified Copernicus Sentinel data (2021) processed by
ESA WorldCover consortium.
