"""Run two detector settings on the same real scenes and list how they differ.

    # the shipped detector against what it stored on the Hub (should be empty)
    python scripts/sar_diff.py --stored --scenes 4

    # the shipped settings (A) against a candidate (B)
    python scripts/sar_diff.py --b k_sigma=12 --scenes 6
    python scripts/sar_diff.py --a coast_buffer_m=200 --b coast_buffer_m=400 \
        --scene S1C_IW_GRDH_1SDV_20260916T020601_20260916T020631_009466_012D3D

No credentials. Scenes come from the Hub's `sar/scenes/v1/` (newest first)
and are read from Planetary Computer the way the collector reads them, about
a minute each. The report (stdout, JSON) is the fingerprint list of
`src/sar_diff.py`: what kind of difference, at which stage, in which distance
band / SNR / slot, how many, and a few examples. Decide what difference you
expect *before* running it, and write that down with the result.

Set HF_HUB_DISABLE_XET=1 if the Hub download stalls.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import sar_detect  # noqa: E402
import sar_diff  # noqa: E402

PARAMS = {"k_sigma": float, "coast_buffer_m": float,
          "min_area_px": int, "max_length_m": float}
STAGE_PARAMS = ("k_sigma", "coast_buffer_m")


def parse_params(items: list[str]) -> dict:
    out = {}
    for item in items or []:
        name, _, value = item.partition("=")
        if name not in PARAMS or not value:
            raise SystemExit(f"--a/--b take NAME=VALUE with NAME in {sorted(PARAMS)}: {item!r}")
        out[name] = PARAMS[name](value)
    return out


def run(image, land, transform, pixel_m, params: dict):
    st = sar_detect.stages(image, land, pixel_m,
                           **{k: v for k, v in params.items() if k in STAGE_PARAMS})
    rows, stats = sar_detect.measure(st, transform, pixel_m,
                                     **{k: v for k, v in params.items()
                                        if k not in STAGE_PARAMS})
    return st, rows, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", nargs="*", default=[], help="reference settings, NAME=VALUE")
    ap.add_argument("--b", nargs="*", default=[], help="candidate settings, NAME=VALUE")
    ap.add_argument("--stored", action="store_true",
                    help="A is the table stored on the Hub, B the shipped detector now")
    ap.add_argument("--scenes", type=int, default=4, help="newest N scenes")
    ap.add_argument("--scene", action="append", default=[], help="a scene id (repeatable)")
    ap.add_argument("--data", default="sar-survey-data")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if args.stored and (args.a or args.b):
        raise SystemExit("--stored compares the shipped settings; drop --a/--b")
    pa, pb = parse_params(args.a), parse_params(args.b)
    if not args.stored and pa == pb:
        raise SystemExit("A and B are the same settings; give --b (or --stored)")

    import pandas as pd

    import sar_scene
    import sar_survey
    from sar_columns import DETECTOR_VERSION

    dest = Path(args.data)
    sar_survey._download(dest)
    scenes = pd.concat([pd.read_parquet(f) for f in
                        sorted((dest / "sar" / "scenes" / DETECTOR_VERSION).glob("*.parquet"))])
    scenes = scenes[scenes.status == "ok"].sort_values("acq_time", ascending=False)
    ids = args.scene or list(scenes.scene_id[:args.scenes])
    unknown = set(ids) - set(scenes.scene_id)
    if unknown:
        raise SystemExit(f"not an ok scene on the Hub: {sorted(unknown)}")
    acq = dict(zip(scenes.scene_id, scenes.acq_time.astype(str)))
    feats = sar_survey._footprints(ids)

    land = sar_scene.load_land_mask()
    transform = sar_scene.aoi_grid()[0]
    pixel_m = sar_scene.pixel_metres()
    records, checkpoints = [], []
    for sid in ids:
        image, _ = sar_scene.read_scene(feats[sid]["assets"]["vv"]["href"])
        st_b, rows_b, stats_b = run(image, land, transform, pixel_m, pb)
        if args.stored:
            det = pd.read_parquet(dest / "sar" / "det" / DETECTOR_VERSION / f"{sid}.parquet")
            rows_a = det.to_dict("records")
            recs = sar_diff.compare_to_stored(rows_a, rows_b, st_b)
            stats_a = {"n_candidates": len(rows_a),
                       "n_vessel_sized": int(det.vessel_sized.sum())}
        else:
            st_a, rows_a, stats_a = run(image, land, transform, pixel_m, pa)
            recs = sar_diff.diff_rows(rows_a, rows_b, st_a, st_b)
            del st_a
        slot = sar_survey.geometry_of(acq[sid])
        for r in recs:
            r["scene_id"], r["slot"] = sid, slot
        records += recs
        # Scene-level checkpoints: if these already differ, the per-row list
        # is downstream of that and should be read with it.
        checkpoints.append({"scene_id": sid, "slot": slot,
                            **{f"a_{k}": stats_a.get(k) for k in
                               ("scored_water_km2", "sea_median_dn",
                                "n_candidates", "n_vessel_sized")},
                            **{f"b_{k}": stats_b.get(k) for k in
                               ("scored_water_km2", "sea_median_dn",
                                "n_candidates", "n_vessel_sized")},
                            "differences": len(recs)})
        del image, st_b

    report = {"a": "stored on the Hub" if args.stored else {**pa} or "shipped",
              "b": {**pb} or "shipped", "detector_version": DETECTOR_VERSION,
              "match_px": sar_diff.MATCH_PX, "checkpoints": checkpoints,
              **sar_diff.summarise(records, len(ids))}
    print(json.dumps(report, indent=2, default=str))
    return 1 if args.stored and records else 0


if __name__ == "__main__":
    sys.exit(main())
