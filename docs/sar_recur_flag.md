# A "recurs here" flag on the map — decision record

Written 2026-11-23, before the flag was built or its effect on the map was
seen. If anything here is changed after seeing the result, the change is
listed at the bottom with the reason.

## What the flag says, and what it does not

It says: **this spot has had a strong radar detection in many passes, seen
from more than one acquisition geometry.** On the map it reads "recurs in
the same place — a structure or an anchorage".

It does not say "not a ship". The optical check (`docs/sar_recur_s2.md`) found
the most-recurring places are mostly platforms (11 of 15) but also anchorages
(3, plus 1 that is probably one). At an anchorage the ship in the latest pass
is a real ship.

## Rule (fixed here)

- **Detections that count:** `vessel_sized`, SNR > 20, `dist_to_land_km` ≥ 3
  — the map's default layer. Scenes with status `ok` only.
- **Place:** single-linkage of those detections at **100 m**, over every pass
  on the Hub (the same rule as `sar_recur_s2`). Its position is the median of
  its detections.
- **Flagged:** a place seen in **at least 10 distinct passes** and from **at
  least 2 acquisition slots** (`sar_survey.geometry_of`).
  - 10 is a judgement, not a fitted number. Every place the optical check
    looked at had 20–33 passes, and all were structures or anchorages. 10 is
    half that, and still far above chance (below).
  - Two slots keeps out what recurs only within one geometry: the 3–10 km
    slot-bound detections whose cause is still unknown (`sar_ambiguity`).
    Those must not be called a structure.
- **On the map:** a detection in the latest pass within **100 m** of a flagged
  place is drawn in a different colour. Its popup gives the place's pass count
  and slots, and the status line counts flagged detections separately.
  Nothing is hidden by the flag.

## Chance check (to be run once, reported below)

Each strong detection is moved 500 m in a random direction (fixed seed), and
the same linking and rule are applied. The number of flagged "places" that
gives is the chance level. If it is not near zero, the threshold is wrong and
the flag does not ship.

## Where it is computed

`src/sar_places.py` builds `sar/places/v1/places.parquet` from every
detection on the Hub. `sar-collect.yml` rebuilds it after any run that wrote
a scene. The map reads that one file rather than every pass, so its load does
not grow with the archive. If the file is missing or unreadable, the map
draws as before and says the flag is unavailable.
