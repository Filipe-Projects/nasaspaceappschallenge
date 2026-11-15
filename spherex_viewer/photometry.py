"""Quick-look aperture photometry: spectra and light curves on the fly.

Every SPHEREx exposure sees a source through a different slice of the linear
variable filter, so measuring the same source in all the exposures of an
epoch yields its 0.75-5 um spectrum, and the same measurements against time
give a light curve.  This is a visualisation aid, not science photometry: a
fixed circular aperture, no aperture correction, no PSF fitting.
"""
from __future__ import annotations

import math

import numpy as np

from . import config
from .cutouts import Cutout, world_to_pixel
from .processing import robust_rms, sigma_clipped_median

ARCSEC_PER_RAD = 206264.80624709636
PIXEL_SR = (config.PIXEL_SCALE_ARCSEC / ARCSEC_PER_RAD) ** 2
MJYSR_TO_MJY = PIXEL_SR * 1e9          # 1 MJy/sr over one native pixel, in mJy


def aperture_weights(shape: tuple[int, int], x: float, y: float, radius: float, sub: int = 5) -> np.ndarray:
    """Fraction of every pixel inside a circle (sub-sampled pixel overlap)."""
    ny, nx = shape
    weights = np.zeros(shape, dtype=np.float64)
    r = int(math.ceil(radius)) + 1
    x0, x1 = max(0, int(round(x)) - r), min(nx, int(round(x)) + r + 1)
    y0, y1 = max(0, int(round(y)) - r), min(ny, int(round(y)) + r + 1)
    if x1 <= x0 or y1 <= y0:
        return weights
    offsets = (np.arange(sub) + 0.5) / sub - 0.5
    yy = (np.arange(y0, y1)[:, None] + offsets[None, :]).ravel()
    xx = (np.arange(x0, x1)[:, None] + offsets[None, :]).ravel()
    inside = (xx[None, :] - x) ** 2 + (yy[:, None] - y) ** 2 <= radius ** 2
    frac = inside.reshape(y1 - y0, sub, x1 - x0, sub).mean(axis=(1, 3))
    weights[y0:y1, x0:x1] = frac
    return weights


def measure(cutout: Cutout, ra: float, dec: float, r_ap: float = 2.0,
            r_in: float = 4.0, r_out: float = 8.0) -> dict | None:
    """Background-subtracted aperture flux of (ra, dec) in one cutout.

    Returns None when the aperture does not fit inside the cutout or falls on
    missing data.  The uncertainty comes from the scatter of the sky annulus,
    so it is a floor: it ignores source photon noise and calibration errors.
    """
    xs, ys = world_to_pixel(cutout.wcs, [ra], [dec])
    x, y = float(xs[0]), float(ys[0])
    ny, nx = cutout.shape
    if not (np.isfinite(x) and np.isfinite(y)):
        return None
    if x < r_ap or y < r_ap or x > nx - 1 - r_ap or y > ny - 1 - r_ap:
        return None
    img = cutout.image.astype(np.float64)
    finite = np.isfinite(img)
    weights = aperture_weights(img.shape, x, y, r_ap)
    in_aperture = weights > 0
    yy, xx = np.indices((ny, nx))
    dist = np.hypot(xx - x, yy - y)
    annulus = (dist >= r_in) & (dist <= r_out) & finite
    if annulus.sum() < 12:
        return None
    missing = float(weights[in_aperture & ~finite].sum())
    area = float(weights.sum())
    if area <= 0 or missing > 0.2 * area:
        return None
    background = sigma_clipped_median(img[annulus])
    sky_rms = robust_rms(img[annulus])
    if not np.isfinite(sky_rms):
        sky_rms = 0.0
    usable = in_aperture & finite
    flux = float(np.sum(weights[usable] * (img[usable] - background)))
    if missing > 0:                                   # scale up for the missing fraction
        flux *= area / (area - missing)
    err = float(sky_rms * math.sqrt(area + area ** 2 / float(annulus.sum())))

    wavelength = bandwidth = None
    if cutout.wave is not None:
        wl, bw = cutout.wavelength(x, y)
        wavelength, bandwidth = float(np.ravel(wl)[0]), float(np.ravel(bw)[0])

    flag_value = None
    if cutout.flags is not None:
        flag_value = int(np.bitwise_or.reduce(cutout.flags[dist <= r_ap], axis=None))

    exp = cutout.exposure
    return {
        "obs_id": exp.obs_id,
        "detector": exp.detector,
        "band": exp.band,
        "collection": exp.collection,
        "mjd": exp.mjd,
        "date": exp.date_iso,
        "wavelength_um": wavelength,
        "bandwidth_um": bandwidth,
        "flux_mjy": flux * MJYSR_TO_MJY,
        "err_mjy": err * MJYSR_TO_MJY,
        "peak_mjysr": float(np.nanmax(np.where(usable, img, np.nan)) - background),
        "background_mjysr": float(background),
        "missing_fraction": missing / area,
        "x": x + cutout.x0,
        "y": y + cutout.y0,
        "flag_value": flag_value,
    }
