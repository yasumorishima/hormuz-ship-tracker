# SAR recall on held-out scenes — preregistration

Written 2026-11-23, **before** the detector was run on any of the scenes below.
The results go in a section added underneath, without editing this part. Any
change made after the results were seen is listed there as a deviation.

## What is measured

**Construct.** The recall of the shipped detector (`DETECTOR_VERSION` v1,
`K_SIGMA` = 10, 200 m coast buffer, ESA WorldCover mask, `vessel_sized` true)
against vessels that were transmitting AIS. This is "of the AIS-transmitting
hulls the detector was allowed to see, how many did it put a vessel-sized
detection on". It is not the recall over all ships: a ship with its AIS off is
outside the denominator by construction. It says nothing about precision.

**Why again.** The 24/24 in `PIPELINE.md` was measured on the two scenes that
`K_SIGMA` was chosen on (S1C 2026-03-15T14:24:00Z and 2026-03-18T02:14:46Z).
It is a training-set number. This measurement uses only scenes that played no
part in choosing anything.

## Data

- AIS: the Pi-era archive `positions.parquet` on the Hub
  (2026-03-14 06:58Z to 2026-04-11 10:40Z, 175,773 rows), unchanged.
- Box: 54.8–55.6 E, 24.9–25.8 N (off Dubai; where the archive has its
  receivers). Not the strait — the strait has too few AIS rows to score.
- Scenes: every Sentinel-1 GRD IW slice on Planetary Computer touching the box
  between 2026-03-14 and 2026-04-12, **except both slices of the 03-15 and
  03-18 passes** (the selection data). Listed before running: 03-21 14:25,
  03-24 02:15 (×2), 03-27 14:24 (×2), 03-30 02:14 (×2), 04-02 14:25,
  04-05 02:15 (×2), 04-08 14:24 (×2), 04-11 02:14 (×2). Passes with no AIS
  vessel in scored water contribute nothing and are listed as such.
- Mask: WorldCover v200 built for the box at 10 m with the same rules as
  `scripts/generate_sar_water_mask.py` (classes 0 and 80 are water), reduced
  to the 0.0002° grid by maximum as `sar_scene.load_land_mask` does.

## Matching (fixed in advance; same as the 24/24)

- A vessel's position at acquisition time is dead-reckoned (speed, course)
  from its AIS report nearest in time, if that report is within ±2 minutes.
  No report within 2 minutes → the vessel is not in the denominator.
- Denominator: vessels whose dead-reckoned position falls on a valid scene
  pixel in scored water (beyond the 200 m buffer). Vessels inside the buffer
  or on mask land are counted and reported separately, not scored.
- Found: a `vessel_sized` detection within **300 m**. One detection may match
  more than one vessel (rafted hulls); that is counted and reported.
- Unit: one vessel per pass. A vessel scored in both slices of a pass counts
  once, found if found in either.

Implementation details fixed before running (`scripts/sar_recall_holdout.py`):
time is `received_at` (UTC); SOG 102.3 / COG ≥ 360 are "not available" and a
reported SOG above 40 kn is treated as unknown — such a vessel keeps its
reported position instead of being dead-reckoned.

## Reported, in this order

1. **Primary**: found / scored over all held-out passes, with an exact
   (Clopper–Pearson) 95% interval, and the per-pass counts beside it (the
   vessels in one pass are not independent; a pass-level view is shown so a
   single bad pass is visible).
2. **Chance arm**: the same matching applied to the scored positions shifted
   2 km in eight fixed compass directions (positions that land outside scored
   water dropped). This is how often a random point near traffic has a
   vessel-sized detection within 300 m. Recall is only meaningful as its
   distance from this.
3. **Production cut**: the same, counting only detections that pass the map
   default (`dist_to_land_km` ≥ 3 and `snr` > 20), over vessels ≥ 3 km from
   land. This is the condition the map is actually shown under.
4. **Strata, all shown even when they do not differ**: distance to land
   (0.2–1 / 1–3 / 3–10 / 10+ km), moving (> 1 kn) vs not, AIS length
   (< 50 m / 50–150 m / > 150 m / unknown).
5. Every miss listed with its MMSI-free description (length, speed, distance,
   nearest detection of any kind and its distance) — explained, not removed.

## What would count as what

- No threshold or setting is changed by this result. `K_SIGMA` stays 10.
- If the lower end of the held-out 95% interval is below 86% (the lower end the
  24/24 itself allowed), the docs stop quoting "24/24" alone and quote the
  held-out figure with its interval instead.
- Either way, the docs quote the held-out figure as the recall, with the
  24/24 kept only as the selection-time number.

---

## Results (2026-11-23; raw output `docs/sar_recall_holdout.json`)

Five held-out passes had AIS vessels in scored water (03-21, 03-27, 03-30,
04-05, 04-11). 03-24 and 04-08 had no AIS within 2 minutes and were not read;
04-02 had one vessel, and it was outside scored water. 28 vessel-passes were
not scored (9 in the buffer or on mask land, 19 off the scene's valid pixels).

**Primary, as preregistered: 20 of 35 = 57% (95% CI 39–74%).** The chance arm
(2 km shifts) gives 9.1% (5.7–13.6%), so the detector is far above chance, but
the 24/24 does not carry over.

| pass | found / scored |
|---|---:|
| 2026-03-21 14:25 | 8 / 21 |
| 2026-03-27 14:24 | 9 / 9 |
| 2026-03-30 02:14 | 2 / 2 |
| 2026-04-05 02:15 | 1 / 1 |
| 2026-04-11 02:14 | 0 / 2 |

Strata (all shown; the 3–10 km band had nobody in it):

| | found / scored |
|---|---:|
| 0.2–1 km from land | 7 / 8 |
| 1–3 km | 10 / 10 |
| 3–10 km | 0 / 0 |
| 10 km + | 3 / 17 |
| moving > 1 kn | 4 / 5 |
| not moving | 16 / 30 |
| AIS length < 50 m | 13 / 16 |
| 50–150 m | 3 / 7 |
| > 150 m | 4 / 10 |
| unknown | 0 / 2 |

**Production cut** (detections ≥ 3 km and SNR > 20, vessels ≥ 3 km): 3 / 17
(18%, 4–43%).

Three detections were each matched to more than one vessel.

### The ground truth was wrong in one place

The misses are almost all in the 03-21 pass, 13 km off Ajman, where
nothing at all was detected within 1.75 km. Eleven of those vessels, from
21 m to 330 m long, reported positions within a few metres of a single point,
**25.5516 N 55.3814 E**. Across the whole archive, **323 different MMSIs
reported that exact point**, almost all of them on 03-19 to 03-21. Their other
reports put them elsewhere in the box and beyond. That
pattern is false positions (GNSS spoofing or a placeholder fix), not ships. An
AIS position is not a fact about where a hull is.

The selection data are not affected: the point first appears at 03-19 18:08Z,
after both selection passes.

### Deviations from the plan, all decided after seeing the results

1. **A co-location exclusion.** Vessels with two or more others within 30 m at
   the same instant are reported separately. That rule caught 8 of the 11. The
   30 m figure was picked after the fact, and the 3 it missed sit within 1 km
   of the point.
   - excluding co-located: **20 / 27 = 74% (54–89%)**, against a chance arm
     of 12.6% (8–19%) over the same vessels
   - the co-located vessels alone: 0 / 8
   - production cut, excluding co-located: 3 / 9 (8–70%)
   - sensitivity, not a result: also dropping the other 4 misses within 1 km
     of the point gives 20 / 23 (87%, 66–97%). It is listed so nobody has to
     recompute it, but it removes misses because they are misses.
2. **A bug fix in the length strata.** A missing AIS length was read as known
   (NaN is truthy). The fix moved 2 vessels to "unknown". The primary result
   did not change.

### What this changes

The decision rule in the plan applies: the lower end of the primary interval
(39%) is below 86%. The docs now quote the held-out figure with its interval,
and keep 24/24 only as the number `K_SIGMA` was chosen on.

The remaining honest misses, outside the spoofed cluster:
- two just past the radius (304 m and 308 m)
- one 79 m vessel 13 km out with nothing within 2.6 km
- one 56 km out with nothing within 3 km

None of these change `K_SIGMA`.

What this still does not measure:
- the strait
- the 3–10 km band, which had no AIS vessel in it
- precision

The production cut was scored on 9–17 vessels, too few to say more than
"low, with a wide interval".
