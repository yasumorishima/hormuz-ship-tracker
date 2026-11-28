# Detector diffs: what a change does, by kind

`scripts/sar_diff.py` (logic in `src/sar_diff.py`, tests in `tests/test_sar_diff.py`).

**What it measures.** Not ships and not a rate: the *differences* between two
runs of the detector on the same real scenes. A is the reference (the shipped
settings, or the table stored on the Hub), B the candidate. Detections are
matched by grid position (within 1.5 px, one to one); every unmatched or
changed one gets a fingerprint `(kind, stage, band, SNR, slot, vessel_sized)`:

| kind | stage |
|---|---|
| `only_a` / `only_b` | which step of the *other* run lost it, read off that run's own arrays (`sar_detect.stages`): `water` (not scored water there), `threshold` (not above bg + k·sd), `blob` (above the threshold, but merged or split so the centroid moved) |
| `flipped` | matched, `vessel_sized` differs; stage = `reason_a->reason_b` |
| `changed` | matched, a measured field moved beyond tolerance; stage = the first such field in table order |

One cause leaves one fingerprint on many detections, so the report is a short
list with counts and examples. The idea is from Anthropic's account of moving
the Messages API from Python to Rust (2026-11): use what production did as the
reference, and record where the two runs first part ways. The language move
itself does not apply here — the heavy work is already numpy/scipy/GDAL and the
wall clock is network — so the code stays Python.

To make the stages readable without a second copy of the detector,
`sar_detect.detect` was split into `stages` (water, background, threshold,
labels) and `measure` (rows). `detect` is `measure(stages(...))`; a test holds
the two equal, and the stored-table run below shows the split changed nothing.

## Run 1 — the shipped detector against the Hub (2026-11-28)

Expected before running: no differences.

- 4 newest `ok` scenes (S1C 2026-11-27 02:06Z ×2, S1D 2026-11-26 02:14Z ×2),
  detector v1, `K_SIGMA` 10, coast buffer 200 m, ESA WorldCover mask.
- First run: candidate and vessel-sized counts equal in every scene
  (6,669 / 3,952), but 236 `changed` — **all one fingerprint, `dist_to_land_km`**,
  ~1e-6 km at 30–47 km. The Hub stores the measured columns as float32; the
  absolute tolerance was tighter than float32 keeps at that size. Fixed by a
  relative tolerance of 1e-6 (`sar_diff.RTOL`); test
  `test_float32_storage_is_not_a_change`.
- Second run: **0 differences**. The re-run reproduces every stored row.

## Run 2 — `K_SIGMA` 12 against 10, same 4 scenes (2026-11-28)

An illustration of the report, not a proposal to change `K_SIGMA` (12 lost one
of 24 AIS-matched hulls when 10 was chosen; docs/PIPELINE.md). Expected before
running: losses only at `threshold` and only at SNR ≤ 20 (SNR is peak over
background in sd, so anything over 12 should survive); some blob reshaping.

- Vessel-sized 3,952 → 2,917 (−26%). 8,276 differences, 4,932 touching
  vessel-sized rows: `only_a` 3,574, `only_b` 1,863, `flipped` 926,
  `changed` 1,913.
- The largest fingerprints are all SNR ≤ 20: `only_a·threshold` at 0.2–1 km and
  10 km+, in both slots; then `only_b·blob` at 0.2–1 km (a blob that shrinks at
  the higher threshold moves its centroid more than 1.5 px, so it reads as one
  lost and one new — a limit of matching by centroid, not a new object);
  then `flipped ok->too_small`.
- **The map's default layer (3 km+, SNR > 20, vessel-sized) loses nothing at the
  threshold**: 260 `changed·area_px`, 77 `changed·dist_to_land_km` (the
  centroid moved by under 1.5 px), 8 `only_a·blob` / 14 `only_b·blob`, 1
  `flipped ok->too_small`. The prediction held.

Settings for both runs: `match_px` 1.5, `RTOL` 1e-6, field tolerances in
`sar_diff.FIELDS`. Raw reports were not committed (they hold coordinates of
every difference); rerun the commands above to get them.

## Using it

1. Write down what difference the change should make (kind, stage, which
   strata) before running it.
2. `--stored` first: if the current code does not reproduce the Hub, every
   other diff is measured on top of that.
3. Read the fingerprints, not the totals. A fingerprint you did not predict is
   either a bug or something to explain in the PR.
4. The runs here cover 2 slots and 4 scenes; a change that matters in one slot
   or band needs scenes from it (`--scene`).
