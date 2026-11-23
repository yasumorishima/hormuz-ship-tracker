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

---

## Results (2026-11-23; raw output `docs/sar_ambiguity.json`)

**Deviation, in the data, not in the method.** The Hub had grown since the
plan's numbers were written: it now holds 94 `ok` scenes in 47 passes
(09-09 to 11-21), not 74 in 37. Recurrence was computed over all 47 passes. The
"newest three per slot" are 11-05 to 11-21. Rule and code are unchanged.

The azimuth direction came out at 349.5–349.8° on ascending scenes and
190.1–190.6° on descending ones, within a degree of the prior in every scene.

The run imaged 24 scenes from 12 passes, giving **1,064 strong detections at
3–10 km** (501 same-slot recurring, 563 not) and 885 at 10 km+.

### Primary (3–10 km, source ≥ 10 × the detection's peak)

| window | share with a source |
|---|---:|
| **along-azimuth, 4.5–6.5 km** | **4.8%** (51; 3.6–6.3%) |
| across, 4.5–6.5 km | 1.0% (11; 0.5–1.8%) |
| along, 2.0–4.0 km | 0.4% (4; 0.1–1.0%) |
| along, 7.0–9.0 km | 0.9% (10; 0.5–1.7%) |

Where the brightest along-azimuth source sits, for the 55 detections with one:

| D, km | count |
|---|---:|
| 4.5–5.0 | 3 |
| 5.0–5.5 | 13 |
| 5.5–6.0 | 32 |
| any other bin | 0–2 |

That is the predicted distance, and the peak is sharp. At 5 × and 20 × the
picture is the same: along 7.1% and 3.1%, every control at 3.1% or below.

### By recurrence class (3–10 km, 10 ×)

| | along 4.5–6.5 | across | along 2–4 | along 7–9 |
|---|---:|---:|---:|---:|
| same-slot recurring (501) | **6.0%** (4.1–8.4%) | 1.2% | 0.0% | 1.2% |
| not recurring (563) | **3.7%** (2.3–5.6%) | 0.9% | 0.7% | 0.7% |

### 10 km + (shown, not tested)

| | along 4.5–6.5 km | across, 4.5–6.5 km |
|---|---:|---:|
| all (885) | 4.7% | 0.2% |
| same-slot recurring (185) | 2.7% | 0.5% |
| not recurring (700) | 5.3% | 0.1% |

The along-azimuth sources sit at D 5.5–6.0 km (33 of 42) and 4.5–5.0 km (7).

### Verdict under the plan's rule

- **First condition, met.** The along 4.5–6.5 km share is 4.8%, against at
  most 1.0% in every control. That is more than twice each, and the intervals
  do not overlap. Azimuth ghosts are really in the table.
- **Second condition, not met.** Same-slot recurring 6.0% (4.1–8.4%) against
  non-recurring 3.7% (2.3–5.6%): the intervals overlap.
- **So, by the rule written in advance: inconclusive as the explanation of
  same-slot recurrence.**

The sizes say more than the verdict. At most about 6% of the same-slot
recurring strong detections have a ghost source; the excess to be explained
is 42% against 6.3% chance. Ghosts are real, and they account for about one
strong offshore detection in twenty in both bands, but they are not most of
what recurs within a slot. The remaining same-slot recurrence needs another
cause.

What this cannot see:
- a source that is not 10 × brighter (or 5 ×)
- a source outside the AOI image
- range ambiguities, which are displaced across track by a different rule
