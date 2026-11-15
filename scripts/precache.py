"""Pre-compute the challenge test targets so the demo never waits on IRSA.

Usage (from the repository root, with the virtualenv active):

    python scripts/precache.py                 # all five targets, default FOVs
    python scripts/precache.py barnard rdra    # a subset
    python scripts/precache.py --fov 5 --fov 12

Every cutout is stored in the disk cache (SPHEREX_CACHE_DIR, default ./cache),
so later requests for the same positions are served in a second or two.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spherex_viewer import pipeline  # noqa: E402
from spherex_viewer.resolve import PRESETS, resolve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", help="preset ids (default: all)")
    parser.add_argument("--fov", action="append", type=float, help="field of view in arcmin (repeatable)")
    parser.add_argument("--spectrum", action="store_true", help="also measure the target spectrum")
    args = parser.parse_args()

    wanted = [p for p in PRESETS if not args.targets or p["id"] in args.targets]
    if not wanted:
        print("no matching presets; known ids:", ", ".join(p["id"] for p in PRESETS))
        return 1

    def progress(done, total, message):
        if total and (done == total or done % 25 == 0):
            print(f"    {message}", flush=True)

    for preset in wanted:
        target = resolve(preset["id"])
        fovs = args.fov or [preset["fov"]]
        for fov in fovs:
            t0 = time.time()
            print(f"== {preset['name']} · {fov}' field  ({target['ra_now']:.5f}, {target['dec_now']:.5f})")
            params = pipeline.normalise_frame_params({"ra": target["ra_now"], "dec": target["dec_now"], "fov": fov})
            try:
                result = pipeline.build_frames(params, progress)
            except Exception as exc:  # noqa: BLE001
                print(f"   FAILED: {exc}")
                continue
            analysis = result.get("analysis") or {}
            print(f"   {result['n_used']} exposures, {len(result['epochs'])} epochs, "
                  f"{len(result['frames'])} frames, {result['n_errors']} errors, "
                  f"{result['transfer']['requests']} requests / {result['transfer']['megabytes']} MB, "
                  f"{time.time() - t0:.0f}s{' (cached)' if result.get('cached') else ''}")
            if analysis.get("sources"):
                print(f"   {analysis['n_sources']} sources measured: {analysis['n_moving']} moving, "
                      f"{analysis['n_variable']} variable")
            for warning in result.get("warnings") or []:
                print(f"   WARNING: {warning}")
        if args.spectrum:
            params = pipeline.normalise_spectrum_params({
                "ra": target["ra"], "dec": target["dec"], "pmra": target.get("pmra"),
                "pmdec": target.get("pmdec"), "epoch": target.get("epoch", 2000.0),
                "field": {"ra": target["ra_now"], "dec": target["dec_now"], "fov": fovs[0]}})
            try:
                result = pipeline.build_spectrum(params, progress)
                print(f"   spectrum: {len(result['points'])} measurements")
            except Exception as exc:  # noqa: BLE001
                print(f"   spectrum FAILED: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
