"""Where two runs of the detector part ways, grouped by kind of difference.

A detector change is judged by what it changes, not by how many detections
it ends up with: "+40 vessel-sized" could be forty ships that were missed or
forty pieces of surf, and the count alone does not say which. So the two runs
(A = the reference, B = the candidate) are matched detection by detection on
the same scene, and every difference gets a *fingerprint*:

    (kind, stage, band, snr, slot)

* `kind`   only_a | only_b | flipped (matched, `vessel_sized` differs) |
           changed (matched, a measured field moved beyond its tolerance)
* `stage`  for an unmatched detection, the first step of the *other* run that
           lost it, read off that run's own arrays (`sar_detect.stages`):
           `water` (not scored water there), `threshold` (not above
           bg + k*sd there), `blob` (above the threshold, but merged into or
           split from another blob, so its centroid moved). For `flipped`,
           the reject reason; for `changed`, the first field that moved, in
           table order.
* `band`, `snr`, `slot` — distance band, SNR ≤20 / >20, acquisition slot: the
  strata every SAR number in this repo is reported in.

One bug (or one deliberate change) leaves the same fingerprint on many
detections, so the report is a short list of fingerprints with counts and a
few examples each, rather than thousands of rows. The idea is from
Anthropic's write-up of moving the Messages API to Rust (2026-11): compare
against what production did, and record where the two first differ.

The reference can also be the table on the Hub (`compare_to_stored`): the
shipped detector re-run on the same scene should give back exactly the rows
it stored. That is the check `sar_survey --vh` does by count
(`not_reproduced`); this one says which rows and why.
"""

import numpy as np
from scipy.spatial import cKDTree

from sar_columns import MAP_MIN_SNR

BAND_EDGES = (0.2, 1.0, 3.0, 10.0)
BAND_LABELS = ("<0.2", "0.2-1", "1-3", "3-10", "10+")

MATCH_PX = 1.5          # centroids within this many pixels are the same blob

# Fields compared on a matched pair, in table order, with the absolute change
# that is allowed before it counts, on top of RTOL. Floating point through a
# different code path moves the last bits; a real change moves more than this.
# The Hub stores these columns as float32 (about 7 significant digits): the
# first run against it flagged 236 of 236 differences as dist_to_land_km
# moving by ~1e-6 km at 30-47 km, which is float32 rounding, not a change.
FIELDS = (
    ("dist_to_land_km", 1e-6), ("area_px", 0), ("length_m", 1e-3),
    ("width_m", 1e-3), ("orientation_deg", 1e-3), ("peak_dn", 1e-3),
    ("mean_dn", 1e-3), ("bg_median_dn", 1e-3), ("bg_mad_dn", 1e-3),
    ("snr", 1e-3),
)

RTOL = 1e-6             # float32 keeps ~6e-8; this leaves room for one more step
EXAMPLES = 3


def band_label(dist_km: float) -> str:
    return BAND_LABELS[int(np.searchsorted(BAND_EDGES, dist_km, side="right"))]


def snr_label(snr: float) -> str:
    return f">{MAP_MIN_SNR:g}" if snr > MAP_MIN_SNR else f"<={MAP_MIN_SNR:g}"


def match(a: list[dict], b: list[dict], tol_px: float = MATCH_PX
          ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair detections by grid position, nearest first, one to one."""
    if not a or not b:
        return [], list(range(len(a))), list(range(len(b)))
    pa = np.array([(r["grid_row"], r["grid_col"]) for r in a], dtype=float)
    pb = np.array([(r["grid_row"], r["grid_col"]) for r in b], dtype=float)
    candidates = sorted(
        (float(np.hypot(*(pa[i] - pb[j]))), i, j)
        for i, js in enumerate(cKDTree(pb).query_ball_point(pa, tol_px))
        for j in js)
    used_a, used_b, pairs = set(), set(), []
    for _, i, j in candidates:
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            pairs.append((i, j))
    return (sorted(pairs), [i for i in range(len(a)) if i not in used_a],
            [j for j in range(len(b)) if j not in used_b])


def lost_at(st: dict | None, row: int, col: int) -> str:
    """Which step of a run did not produce a detection at (row, col)."""
    if st is None:
        return "water"
    r0, r1 = max(row - 1, 0), row + 2
    c0, c1 = max(col - 1, 0), col + 2
    if not st["water"][r0:r1, c0:c1].any():
        return "water"
    if not st["hot"][r0:r1, c0:c1].any():
        return "threshold"
    return "blob"


def first_change(ra: dict, rb: dict) -> str | None:
    for name, tol in FIELDS:
        va, vb = ra.get(name), rb.get(name)
        if va is None or vb is None:
            continue
        va, vb = float(va), float(vb)
        if not (abs(va - vb) <= tol + RTOL * max(abs(va), abs(vb))):  # NaN: changed
            return name
    return None


def diff_rows(a: list[dict], b: list[dict], st_a=None, st_b=None) -> list[dict]:
    """One record per difference between two detection lists of one scene.

    `st_a`/`st_b` are the `sar_detect.stages` of each run; without them an
    unmatched detection's stage is `unknown`.
    """
    pairs, only_a, only_b = match(a, b)
    out = []

    def rec(kind, stage, r, **extra):
        out.append({"kind": kind, "stage": stage,
                    "band": band_label(r["dist_to_land_km"]),
                    "snr": snr_label(r["snr"]),
                    "vessel_sized": bool(r["vessel_sized"]),
                    "latitude": r["latitude"], "longitude": r["longitude"],
                    "grid_row": r["grid_row"], "grid_col": r["grid_col"], **extra})

    for i in only_a:
        r = a[i]
        rec("only_a", lost_at(st_b, r["grid_row"], r["grid_col"])
            if st_b is not None else "unknown", r)
    for j in only_b:
        r = b[j]
        rec("only_b", lost_at(st_a, r["grid_row"], r["grid_col"])
            if st_a is not None else "unknown", r)
    for i, j in pairs:
        ra, rb = a[i], b[j]
        if bool(ra["vessel_sized"]) != bool(rb["vessel_sized"]):
            rec("flipped", (ra["reject_reason"] or "ok") + "->" +
                (rb["reject_reason"] or "ok"), ra)
            continue
        field = first_change(ra, rb)
        if field is not None:
            rec("changed", field, ra, a_value=float(ra[field]), b_value=float(rb[field]))
    return out


def summarise(records: list[dict], n_scenes: int) -> dict:
    """Fingerprints with counts and a few examples, most frequent first.

    Vessel-sized differences are listed apart from the rest: the rejects are
    stored too, and a change that only reshuffles `too_small` specks would
    otherwise bury one that moves the map.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        key = (r["kind"], r["stage"], r["band"], r["snr"], r.get("slot", ""),
               r["vessel_sized"])
        groups.setdefault(key, []).append(r)
    fps = []
    for key, rs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        fps.append({
            "kind": key[0], "stage": key[1], "band_km": key[2], "snr": key[3],
            "slot": key[4], "vessel_sized": key[5], "count": len(rs),
            "scenes": len({r.get("scene_id") for r in rs}),
            "examples": [{k: r[k] for k in ("scene_id", "latitude", "longitude",
                                            "a_value", "b_value") if k in r}
                         for r in rs[:EXAMPLES]]})
    vs = [r for r in records if r["vessel_sized"]]
    return {
        "scenes": n_scenes,
        "differences": len(records),
        "vessel_sized_differences": len(vs),
        "by_kind": {k: sum(1 for r in records if r["kind"] == k)
                    for k in ("only_a", "only_b", "flipped", "changed")},
        "fingerprints": fps,
    }


def compare_to_stored(stored: list[dict], rows: list[dict], st=None) -> list[dict]:
    """The stored table as A, a fresh run as B. Any record is a regression."""
    return diff_rows(stored, rows, st_a=None, st_b=st)
