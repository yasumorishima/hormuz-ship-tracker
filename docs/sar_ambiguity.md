# Are the strong 3–10 km detections azimuth ghosts? — preregistration

Written 2026-11-23, **before** any image was sampled for this. Results go in a
section added underneath; anything changed after seeing them is listed there
as a deviation.

## What is measured

**Construct.** For a `vessel_sized` detection with SNR > 20 between 3 and
10 km from land, whether there is a much brighter object (land or sea, in the
same image) displaced from it **along the satellite's azimuth direction** by
the distance at which Sentinel-1 IW puts azimuth ambiguities. If there is one
far more often than in the control directions and distances, the detection is
plausibly that object's ghost. It is a test of one explanation for the
observation in `PIPELINE.md`: strong 3–10 km detections recur within their
acquisition slot (42% against 6.3% chance) far more than across slots (14%
against 10%).

**Why this distance.** An azimuth ambiguity is displaced by
Δ = λ·R·PRF / (2·V). With λ = 5.55 cm, V ≈ 7.1 km/s and the IW sub-swaths'
nominal PRF (about 1717, 1451 and 1685 Hz) and slant range (about 800, 870
and 940 km), Δ is about 5.4, 4.9 and 6.2 km. Every pass in one slot flies the
same relative orbit, so the same bright source throws its ghost on the same
spot each time — and somewhere else in the other slots. The ghost is weaker
than the source by the ambiguity ratio. Nominal ratios are around −20 to −25
dB in power, roughly 10–18× in amplitude.

## Data

- Detections: `sar/det/v1/` and `sar/scenes/v1/` on the Hub, all `ok` scenes
  (74 scenes, 37 passes, 09-09 to 11-06), unchanged.
- Images: the VV band of **the three most recent passes in each of the four
  slots** (02:0x, 02:1x, 14:1x, 14:2x), all their slices, read with
  `sar_scene.read_scene` on the production AOI grid, land included. Twelve
  passes are chosen by date, not by content.
- Azimuth direction: for each scene, the long axis of the minimum rotated
  rectangle of its STAC footprint, in km on the ground.

## Per detection

For each scored detection (SNR > 20, `vessel_sized`, 3–10 km, in an imaged
scene), take a box 300 m wide (across the offset direction) by 100 m long at
the point displaced by D, on both sides. Record the largest amplitude in it
for D from 2.0 to 9.0 km in 0.1 km steps:

- **along**: displaced along the azimuth direction
- **across**: displaced perpendicular to it (the control direction)

A **source** at D is a box maximum ≥ **10 ×** the detection's own `peak_dn`.

## Reported, in this order

1. **Primary.** The share of detections with a source along-azimuth at
   D ∈ [4.5, 6.5] km, against:
   - (a) the share with a source across at the same D range
   - (b) the share with a source along-azimuth at D ∈ [2.0, 4.0] km
   - (c) the same at D ∈ [7.0, 9.0] km

   (b) and (c) look in the same direction as the primary, but off the
   predicted distance. All three windows are 2 km wide, so they are compared
   like for like.

   Each share has a Clopper–Pearson 95% interval.
2. The same, split by recurrence class from all 37 passes (100 m radius, at
   least 2 other passes covering, as `sar_survey`):
   - same-slot recurring (seen again in another pass of its slot)
   - not recurring
3. The distribution of D at which the brightest along-azimuth source sits,
   for detections that have one. The prediction is clusters near 4.9–6.2 km.
4. Sensitivity: source thresholds 5 × and 20 ×.
5. The same for 10 km+, SNR > 20, as a second band. It is shown, not tested.

## What would count as what

- **Supported** as an explanation of same-slot recurrence: the along [4.5, 6.5]
  share is at least twice all three controls with non-overlapping intervals, *and*
  it is higher among same-slot-recurring detections than among non-recurring
  ones (non-overlapping intervals).
- **Not supported**: the along share is within the controls' intervals.
- Anything else is **inconclusive** and is written up as that.

Nothing on the map changes on this result alone. A flag would be the next
decision (CLAUDE.md "次にやること" 4).
