"""Save the test targets inside the package, for the days IRSA is out of reach.

Writes spherex_viewer/snapshots/*.json.gz: the finished frame set of each of
the five targets at its default field of view, the spectrum at the target's
position, and the same for the field at the coordinates the challenge quotes
for WISE 0855 (see spherex_viewer/snapshots.py).  Run it with IRSA reachable or
the cache warm (scripts/precache.py), and commit the files it writes.

    python scripts/snapshot.py              # everything
    python scripts/snapshot.py barnard rdra challenge-wise0855
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spherex_viewer import pipeline, snapshots  # noqa: E402
from spherex_viewer.resolve import PRESETS, resolve  # noqa: E402

# Fields worth keeping besides the presets: the coordinates printed in the challenge text.
EXTRA_FIELDS = [{"id": "challenge-wise0855", "name": "Challenge coordinates of WISE 0855",
                 "query": "08h55m10.831s -7d14m42.53s", "fov": 8.0}]


def quiet(*_args) -> None:
    pass


def main() -> int:
    fields = [{"id": p["id"], "name": p["name"], "query": p["id"], "fov": p["fov"]} for p in PRESETS] + EXTRA_FIELDS
    wanted = [f for f in fields if not sys.argv[1:] or f["id"] in sys.argv[1:]]
    for item in wanted:
        target = resolve(item["query"])
        params = pipeline.normalise_frame_params({"ra": target["ra_now"], "dec": target["dec_now"], "fov": item["fov"]})
        try:
            frames = pipeline.build_frames(params, quiet, fallback=False)
        except Exception as exc:  # noqa: BLE001
            print(f"{item['name']}: frames FAILED: {exc}", flush=True)
            continue
        if frames["n_errors"] or frames["warnings"]:
            print(f"{item['name']}: skipped, the frame set is incomplete ({frames['n_errors']} read errors)", flush=True)
            continue
        path = snapshots.save("frames", item["id"], params, frames)
        print(f"{item['name']}: {len(frames['frames'])} frames, {frames['n_used']} exposures, "
              f"{path.stat().st_size / 1e6:.1f} MB", flush=True)

        field = frames["target"]
        spec_params = pipeline.normalise_spectrum_params({
            "ra": target["ra"], "dec": target["dec"], "pmra": target.get("pmra"), "pmdec": target.get("pmdec"),
            "epoch": target.get("epoch", 2000.0), "r_ap": 2.0,
            "field": {"ra": field["ra"], "dec": field["dec"], "fov": field["fov_arcmin"]}})
        try:
            spectrum = pipeline.build_spectrum(spec_params, quiet, fallback=False)
        except Exception as exc:  # noqa: BLE001
            print(f"   spectrum FAILED: {exc}", flush=True)
            continue
        if spectrum["n_errors"]:
            print(f"   spectrum skipped: {spectrum['n_errors']} read errors", flush=True)
            continue
        path = snapshots.save("spectrum", item["id"], spec_params, spectrum)
        print(f"   spectrum: {len(spectrum['points'])} points, {path.stat().st_size / 1e3:.0f} kB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
