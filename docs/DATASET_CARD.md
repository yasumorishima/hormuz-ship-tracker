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
      - split: archive
        path: positions.parquet
      - split: daily
        path: daily/*.parquet
      - split: recent
        path: raw/*/*.parquet
  - config_name: transit_events
    data_files:
      - split: archive
        path: transit_events.parquet
---

# Strait of Hormuz AIS

Vessel positions in the Persian Gulf, the Strait of Hormuz and the Gulf of
Oman, received from [aisstream.io](https://aisstream.io/) and filtered against
a land mask. Bounding box `[[22.0, 48.0], [30.5, 60.0]]` — Kuwait to Muscat.

Collected by [yasumorishima/hormuz-ship-tracker](https://github.com/yasumorishima/hormuz-ship-tracker),
which is also where the code that wrote every row lives.

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
  consecutive rows** without accounting for this. The archive split is
  continuous; the others are not.

## Splits

| split | what it is |
|---|---|
| `archive` | `positions.parquet` — the continuous Raspberry Pi collection, **176,033 rows**, 6.9 MB. Two-minute sampling per vessel, no gaps between windows because there were no windows. |
| `daily` | `daily/<YYYY-MM-DD>.parquet` — one file per finished UTC day of the sampled era. |
| `recent` | `raw/<YYYY-MM-DD>/<HHMMSS>.parquet` — one file per collection window for days not yet compacted. Merged into `daily/` once the day is over. |

A second config, `transit_events`, holds gate crossings inferred during the
Raspberry Pi era. **Nothing appends to it now** — the current pipeline does
not run the transit detector.

## Columns

| column | type | notes |
|---|---|---|
| `mmsi` | int64 | Maritime Mobile Service Identity — the vessel key |
| `timestamp` | string | when the transponder reported, ISO 8601, **naive UTC** |
| `latitude`, `longitude` | float64 | WGS 84 degrees |
| `speed` | float64 | knots. **102.3 is the AIS "not available" sentinel**, and values above 40 are receiver glitches — filter both |
| `course`, `heading` | float64 | degrees |
| `ship_name` | string | may be empty |
| `ship_type` | float64 | AIS type code, nullable — hence float rather than int |
| `destination` | string | as keyed in by the crew, then normalized; often empty or wrong |
| `draught` | float64 | metres |
| `length`, `width` | float64 | metres, summed from the AIS dimension fields |
| `flag` | string | ISO country code **derived from the MMSI prefix**, not broadcast |
| `received_at` | string | when we received it, ISO 8601 **with a UTC offset** |

Note the asymmetry in the last two: `timestamp` carries no offset and
`received_at` does. Both are UTC.

## Static fields arrive in their own frames

Name, type, destination and dimensions are broadcast separately from
positions, every few minutes. In a 180-second window a vessel's position
often arrives without them, so **rows in `recent` and `daily` carry more
nulls in those columns than the archive does**. The collector's consumer
fills them per vessel from the most recent non-empty value in the range it
loads (`src/enrich.py` in the repository); the stored rows are left as
received.

## Coverage is terrestrial

The receivers are shore-based. The middle of the Strait of Hormuz — roughly
35 nautical miles across — is **not covered**: a vessel can cross it without
appearing here at all. This is a property of terrestrial AIS, not of the
collection, and satellite AIS is not free. Absence of a crossing in this
dataset is not evidence that no crossing happened.

## Loading

```python
from datasets import load_dataset

# the continuous Raspberry Pi collection
archive = load_dataset("yasumorishima/hormuz-ais", split="archive")

# everything the sampled pipeline has written
recent = load_dataset("yasumorishima/hormuz-ais", split="recent")
```

Or read the files directly, which is lighter:

```python
import pandas as pd
df = pd.read_parquet("hf://datasets/yasumorishima/hormuz-ais/positions.parquet")
```

## Provenance and terms

Positions are AIS broadcasts, received through aisstream.io's free WebSocket
feed. **Check [aisstream.io](https://aisstream.io/) for their terms before
redistributing**; the `license: other` above is a placeholder for exactly that
reason and is not a grant from us. The land mask is Natural Earth 10m (public
domain).

AIS is self-reported. Vessels can and do transmit wrong names, wrong
destinations, wrong dimensions, and can switch their transponder off.
