"""Render the two start-page images (frontend/img/barnard-*.webp) from SPHEREx data.

The start page blinks the first and the latest all-detector epoch stacks of
Barnard's Star, cropped to 5 arcmin and drawn with the viewer's default
display (asinh stretch, cubic B-spline enlargement, north up, east left).

Usage (repository root, virtualenv active; needs Pillow):

    python scripts/make_hero.py
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spherex_viewer import pipeline  # noqa: E402
from spherex_viewer.resolve import resolve  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "img"
FIELD_ARCMIN = 5.0
SIZE = 720
ZOOM = 16
CONTRAST = 55                      # the viewer's default slider position


def main() -> int:
    target = resolve("barnard")
    result = pipeline.build_frames({"ra": target["ra_now"], "dec": target["dec_now"], "fov": 8.0},
                                   lambda *_: None)
    frames = sorted((f for f in result["frames"] if f["band"] == "ALL"), key=lambda f: f["mjd"])
    if len(frames) < 2:
        print("need at least two epochs")
        return 1
    npix = result["target"]["npix"]
    scale = result["target"]["scale_arcsec"]

    # one display range for both images, as in the viewer, so the blink does not flicker
    rms = float(np.median([f["stats"]["rms"] for f in frames]))
    mid = float(np.median([f["stats"]["p50"] for f in frames]))
    lo, hi = mid - 1.5 * rms, mid + 10 ** (3.6 - 3.2 * CONTRAST / 100) * rms

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for frame in (frames[0], frames[-1]):
        data = np.frombuffer(base64.b64decode(frame["data"]), dtype="<f4").reshape(npix, npix).astype(np.float64)
        data = np.where(np.isfinite(data), data, mid)
        big = ndimage.zoom(data, ZOOM, order=3, prefilter=False, mode="nearest", grid_mode=True)
        half = int(round(FIELD_ARCMIN * 60.0 / scale * ZOOM / 2.0))
        centre = big.shape[0] // 2
        crop = big[centre - half:centre + half, centre - half:centre + half]
        t = np.clip((crop - lo) / (hi - lo), 0.0, 1.0)
        grey = np.arcsinh(10.0 * t) / np.arcsinh(10.0)
        image = Image.fromarray(np.round(grey[::-1] * 255.0).astype(np.uint8), mode="L")   # FITS row 0 is the bottom
        image = image.resize((SIZE, SIZE), Image.LANCZOS)
        year = frame["date"][:4]
        path = OUT_DIR / f"barnard-{year}.webp"
        image.save(path, "WEBP", quality=84, method=6)
        print(f"{path.name}: epoch {frame['epoch']}, {frame['date']}, {frame['n_exposures']} exposures, "
              f"{path.stat().st_size / 1000:.0f} kB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
