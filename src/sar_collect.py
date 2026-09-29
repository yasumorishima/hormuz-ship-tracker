"""Process every Sentinel-1 scene over the strait that has not been done yet.

    python src/sar_collect.py --hours 504
    python src/sar_collect.py --hours 504 --dry-run    # no Hub write
    python src/sar_collect.py --hours 504 --redo       # do them again

Runs to completion whatever the sea is doing: a scene that only clips the
corner of the AOI, or one with no vessels in it, still gets a scene row. The
only way a scene comes round again is if this never finished for it.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

import sar_detect
import sar_scene
import sar_store
from sar_columns import MASK_SOURCE

logger = logging.getLogger("sar_collect")

# Under this there is not enough of the AOI in the scene to be worth the
# arithmetic, but it is still recorded so it is not fetched again.
MIN_COVERED = 0.02

# Three weeks. The window used to be 72 hours with four scenes a run, newest
# first, and that lost a scene for good: the first real run on 2026-09-17 found
# five, did the newest four, and by the next day the fifth
# (S1D 2026-09-15T02:14:05Z) was older than the window. Searching costs one
# request whatever the window, and scenes already done are skipped, so a long
# window only costs anything when there is something to catch up on.
DEFAULT_HOURS = 504.0
DEFAULT_MAX_SCENES = 6


def select(scenes: list[dict], already: set[str], max_scenes: int) -> list[dict]:
    """The scenes to do this run: not done yet, oldest first, at most `max_scenes`.

    Oldest first because the oldest is the one about to leave the window. Taking
    the newest would starve it whenever a run has more to do than it may, which
    is exactly the day after an outage. A scene that can never be read costs one
    slot per run until it ages out; it does not block the others, because a
    failure moves on to the next scene.
    """
    todo = [s for s in scenes if s["scene_id"] not in already]
    todo.sort(key=lambda s: s["acq_time"] or "")
    return todo[:max_scenes]


def process(scene: dict, land, dry_run: bool = False, token: str | None = None) -> dict:
    started = time.monotonic()
    image, covered = sar_scene.read_scene(scene["href"])
    if covered < MIN_COVERED:
        row = sar_store.scene_row(scene, {}, covered, time.monotonic() - started,
                                  status="no_overlap", mask_source=MASK_SOURCE)
        detections = []
    else:
        detections, stats = sar_detect.detect(image, land, sar_scene.aoi_grid()[0],
                                              sar_scene.pixel_metres())
        row = sar_store.scene_row(scene, stats, covered, time.monotonic() - started,
                                  status="ok", mask_source=MASK_SOURCE)
        detections = sar_store.decorate(detections, scene)
    if not dry_run:
        sar_store.upload(scene["scene_id"], detections, row, token=token)
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=DEFAULT_HOURS,
                    help="how far back to look for scenes")
    ap.add_argument("--max-scenes", type=int, default=DEFAULT_MAX_SCENES,
                    help="stop after this many, so one run cannot sit for an hour")
    ap.add_argument("--dry-run", action="store_true", help="process but do not upload")
    ap.add_argument("--redo", action="store_true",
                    help="process scenes already done, overwriting their files")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=args.hours)).isoformat(timespec="seconds")
    scenes = sar_scene.search(start, now.isoformat(timespec="seconds"))

    # A dry run still reads the ledger (the listing needs no token), so it
    # shows what the scheduled run would do rather than the oldest scenes in
    # the window. Only --redo ignores it; to redo everything in the window,
    # pass a --max-scenes larger than the window holds (about 30 in three weeks).
    already = set() if args.redo else sar_store.processed(sar_store.list_files())
    todo = select(scenes, already, args.max_scenes)
    pending = len([s for s in scenes if s["scene_id"] not in already])
    logger.info("%d scenes in the window, %d already done, %d to do, %d left for later",
                len(scenes), len(scenes) - pending, len(todo), pending - len(todo))

    summary = {"window_hours": args.hours, "scenes_found": len(scenes),
               "scenes_processed": 0, "dry_run": args.dry_run,
               "rows": [], "failed": []}
    if not todo:
        # Not an error: the satellites pass every couple of days, so most runs
        # legitimately have nothing to do.
        print(json.dumps(summary, indent=2))
        return 0

    land = sar_scene.load_land_mask()
    for scene in todo:
        logger.info("processing %s (%s)", scene["scene_id"], scene["acq_time"])
        try:
            row = process(scene, land, dry_run=args.dry_run)
        except Exception as exc:                      # noqa: BLE001
            # Carry on to the next scene rather than ending the run. `todo` is
            # oldest first, so a scene that can never be read would otherwise
            # sit at the head of the queue and block everything behind it for
            # as long as it stays inside the window. Nothing is written for it,
            # so it is retried rather than recorded as done.
            logger.error("%s failed: %s", scene["scene_id"], exc)
            summary["failed"].append({"scene_id": scene["scene_id"],
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        summary["rows"].append({k: row[k] for k in (
            "scene_id", "acq_time", "orbit_state", "aoi_covered_frac",
            "scored_water_km2", "sea_median_dn", "n_candidates", "n_vessel_sized",
            "runtime_s", "status")})
        summary["scenes_processed"] += 1

    print(json.dumps(summary, indent=2, default=str))
    # Red when anything failed, even if other scenes went through: a run that
    # silently drops one scene a day is how a gap gets old enough to fall out
    # of the window for good.
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
