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
  → rebuild a throwaway SQLite database from the recent parquet files
  → snapshot.py / heatmap.py / stats_report.py, unchanged
  → commit docs/ back to this repository
```

Dataset: [yasumorishima/hormuz-ais](https://huggingface.co/datasets/yasumorishima/hormuz-ais)

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

- Fleet composition, anchored ratio, port and flag breakdowns are unaffected —
  they are counts over vessels present, and the sample keeps them.
- Gate-crossing transit detection is weaker. A crossing is inferred from
  consecutive positions on either side of a gate line; with an unobserved gap
  between windows, some crossings are only bracketed, not traced.
- Speed and course come from the transponder, not from differencing our own
  positions, so they are unaffected.

## Why not a Hugging Face Space

A Space could hold the connection open the way the Pi did. Docker and Gradio
Spaces run on compute and require a paid plan for personal accounts, so that
route is not free and is not used here. Static Spaces are free, which is the
option available for serving a map.

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
