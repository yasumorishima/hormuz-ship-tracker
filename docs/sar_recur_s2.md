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
