# What is at the places strong detections keep coming back to? — preregistration

Written 2026-11-23, **before** any Sentinel-2 image was fetched. Results are
added underneath; anything changed after seeing them is listed there as a
deviation.

## What is measured

**Construct.** At a site where strong radar detections recur across passes,
whether optical imagery on other days shows:

- **fixed**: the same object in the same place on every usable date
  (platform, rig, buoy, pier head, rock)
- **intermittent**: objects present on some dates, or a different shape or
  orientation between dates (an anchorage — ships come and go)
- **nothing**: no object on any usable date

It is a label of the site, not of a radar detection. It does not say any one
detection was a ship.

**Why.** `PIPELINE.md` shows strong detections beyond 3 km recur and cluster,
and that "recurring" cannot tell a structure from an anchorage. Sentinel-2
(10 m, free on Planetary Computer) can, by looking at several dates.

## Sites and controls (fixed in advance)

- **Sites**: from the Hub's `sar/det/v1/`, the `vessel_sized` detections with
  SNR > 20 and `dist_to_land_km` ≥ 3. These are grouped by single-linkage at
  100 m into places. The **15 places seen in the most distinct passes** are
  taken (ties broken by mean SNR), each needing at least 3 passes. A place's
  position is the median of its detections.
- **Controls**: 15 points, one per site. Each is drawn at random (fixed seed)
  in the same 5 km cell, at least 3 km from land and at least 1 km from any
  strong or weak `vessel_sized` detection ever made. This is the do-nothing
  arm: how often an optical chip of open water there shows an object at all.

## Imagery

- Sentinel-2 L2A on Planetary Computer, 2026-09-01 to 2026-11-22.
- Per site, the **4 dates with the least cloud over the chip**. Usable means
  under 10% of the chip's pixels classed cloud or cloud shadow in SCL.
- Chip: 800 m × 800 m true colour (B04, B03, B02) at 10 m, all four dates
  side by side in one image, stretched the same way for every chip.
- If a point has fewer than 2 usable dates it is reported as "no imagery",
  not dropped silently.

## Labelling (blind)

- The 30 montages (15 sites, 15 controls) are given random codes and shuffled.
  The mapping is written to a file that is not read until both label passes
  are saved.
- One labeller (this session) labels each montage fixed / intermittent /
  nothing, looking only at the centre 300 m (marked) and with the code alone
  visible. A second pass uses new codes and a new order, with each montage
  mirrored left–right, and is labelled without looking at the first pass.
- **Agreement** between the two passes is reported (raw share and Cohen's κ).
  This is the same labeller twice, not two people; it measures consistency,
  not correctness.

## Reported

1. Sites by label (first-pass labels as the result; disagreements listed).
2. Controls by label: the chance of "fixed" or "intermittent" in open water.
3. Agreement between passes.
4. Per site: position, passes seen, slots seen, mean SNR, label.

## What would count as what

- If sites are "fixed" much more often than controls (non-overlapping 95%
  intervals), a recurrence flag on the map would mostly mark structures.
- If they are mostly "intermittent", recurrence mostly marks anchorages, and
  a flag must not read "not a ship".
- No map change is made on this alone ("次にやること" 4 decides that).

---

## Results (2026-11-23; raw output `docs/sar_recur_s2.json`)

All 30 points had 4 usable dates, so none was dropped for want of imagery.
The first site's dates were 09-09, 09-29, 10-09 and 11-15; each point has its
own 4 dates. Labels were made blind under random codes. The key was read only
after both passes were saved.

| | fixed | intermittent | nothing |
|---|---:|---:|---:|
| **sites** (15) | **11** (45–92%) | 3 (4–48%) | 1 (0–32%) |
| controls (15) | 0 (0–22%) | 0 (0–22%) | 15 (78–100%) |

The two label passes agreed on all 30 (κ = 1.0). This was the same labeller
twice, and it remembered the first pass, so this is an upper bound on
consistency, not evidence of correctness.

Per site, in order of passes seen:

| lon, lat | passes | slots | mean SNR | km to land | label |
|---|---:|---|---:|---:|---|
| 56.267, 26.289 | 33 | 02:0, 02:1, 14:1 | 139 | 5.1 | intermittent |
| 56.055, 26.244 | 31 | 02:1, 14:1, 14:2 | 104 | 13.1 | fixed |
| 55.979, 26.381 | 31 | 02:1, 14:1, 14:2 | 98 | 26.7 | fixed |
| 55.709, 26.194 | 31 | 02:1, 14:1, 14:2 | 70 | 40.4 | fixed |
| 55.706, 26.180 | 31 | 02:1, 14:1, 14:2 | 69 | 40.2 | fixed |
| 55.973, 26.388 | 29 | 02:1, 14:1, 14:2 | 58 | 27.0 | fixed |
| 55.697, 26.172 | 28 | 02:1, 14:1, 14:2 | 64 | 40.6 | fixed |
| 55.965, 26.396 | 28 | 02:1, 14:1, 14:2 | 58 | 25.8 | fixed |
| 55.967, 26.414 | 27 | 02:1, 14:1, 14:2 | 52 | 24.0 | fixed |
| 55.732, 26.195 | 26 | 02:1, 14:1, 14:2 | 50 | 38.3 | fixed |
| 56.553, 26.965 | 24 | 02:0, 02:1, 14:1 | 313 | 10.4 | nothing |
| 55.762, 26.176 | 24 | 02:1, 14:1, 14:2 | 63 | 34.8 | fixed |
| 55.755, 26.166 | 23 | 02:1, 14:1, 14:2 | 88 | 35.1 | fixed |
| 56.231, 26.856 | 22 | 02:1, 14:1 | 108 | 7.6 | intermittent |
| 56.415, 25.589 | 20 | 02:0, 02:1 | 277 | 5.7 | intermittent |

### What this says

- **The places detections come back to most are mostly fixed structures.**
  Sites were labelled "fixed" 11 of 15 times against 0 of 15 for controls, and
  the intervals do not overlap. Under the plan's rule, a flag for recurrence
  seen across slots would mostly mark structures.
- **All 11 fixed sites are in one patch**, 55.70–56.06 E, 26.17–26.41 N, 13–41
  km offshore west of the strait. Each is a small, identical bright point in
  every date and is seen from three slots — an offshore field of platforms.
  The 15 places are not 15 independent draws; they are mostly one field.
- **The 3 intermittent sites are anchorages.** Ships with a different heading
  or position on each date sit off Khasab (56.27 E, 26.29 N), south of Larak
  and Qeshm (56.23 E, 26.86 N) and off Fujairah (56.42 E, 25.59 N). They recur
  because the anchorage stays while the ships change: **recurring is not "not
  a ship"**, here directly seen.
- **One site has nothing on any date**: 56.553 E 26.965 N, 10 km from land,
  mean SNR 313, seen in 24 passes from three slots. No object 10 m optics can
  see. Not explained; left as is.

### Limits and deviations

- These are the 15 most-recurring places, which recur **across** slots. They
  do not answer step 1's question about the 3–10 km detections that recur
  only within their slot. Only 1 of the 15 is in that band and slot-bound.
- One labeller, the same twice; not two people.
- The chip build first hit a 429 rate limit from Planetary Computer's signing
  endpoint. It was changed to one token per collection with backoff; the
  sampling did not change.
- In the first pass one control had a ship just outside the 300 m box on every
  date. By the plan's centre-box rule it was labelled "nothing".
