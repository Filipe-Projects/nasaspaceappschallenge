"""What changed between epochs: sources that move and sources that vary.

Every bright source is centroided and measured in each epoch.  The field
stars then serve as the yardstick: they give the residual frame-to-frame
registration, the positional repeatability and the photometric repeatability
of *this* field, and a source is reported only when it departs significantly
from them.

Two detection images are searched: the all-detector stack, and a "red" stack
of bands 5 and 6 (3.8-5.0 um) where cold brown dwarfs, and any distant planet,
are brightest while ordinary stars fade.

Motion.  A straight line is fitted to the positions.  The scatter of a
source's own track around that line counts as its error, so a saturated star
whose centroid wanders from month to month is not mistaken for a mover, while
a star that advances steadily is.

Variability is judged band by band, because real variables often change
colour: RW Aurigae brightens at 1 um while it fades at 5 um, which cancels in
a broadband average.  Saturated stars are measured from the wings of their
image, which still scale with the star's brightness.

This is a pointer for human eyes, not a catalogue: blends, saturated stars
and image artefacts can all produce false candidates.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
from scipy.ndimage import maximum_filter

ARCSEC_PER_RAD = 206264.80624709636
APERTURE_R = 2.5
ANNULUS = (5.0, 8.0)
CENTROID_SIGMA = 1.0
MAX_SOURCES = 150
BAND_IDS = ["D1", "D2", "D3", "D4", "D5", "D6"]
RED_BANDS = ["D5", "D6"]

# thresholds
MOTION_SIGMA = 5.0            # significance of the fitted motion
MOTION_MIN_SHIFT_PX = 0.25    # displacement over the baseline
MOTION_LOO_SIGMA = 3.0        # still significant with any one epoch removed
MAX_ELLIPTICITY = 0.30        # rounder than this in the epoch stacks, or it is a blend
SATURATED_CENTROID_FLOOR = 0.08   # px: least error granted to a saturated star's centroid
VARIABLE_MIN_AMPLITUDE = 0.10     # peak-to-peak / mean, per band
VARIABLE_MIN_AMPLITUDE_SATURATED = 0.25
VARIABLE_MIN_BANDS = 2
# ... and in most of the bands in which the source is detected.  A source with
# a sharply structured spectrum (a T dwarf) can look different in two or three
# band stacks simply because each epoch samples its narrow peaks differently;
# a real variable changes nearly everywhere.
VARIABLE_BAND_MAJORITY = 0.6
# Outlier rejection inside a band removes cosmic rays, but it also trims the
# narrow spectral peaks of sources such as T dwarfs, by an amount that differs
# from epoch to epoch.  A band where more than this fraction of the samples at
# the source was rejected is not used to judge variability.
MAX_CLIPPED_FRACTION = 0.10


def _robust_sigma(values) -> float:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size < 3:
        return float("nan")
    med = np.median(vals)
    return float(1.4826 * np.median(np.abs(vals - med)))


def _nanmean(stack: list[np.ndarray]) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(np.stack(stack), axis=0)


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------
def find_peaks(image: np.ndarray, threshold: float, border: int = 4, size: int = 5,
               limit: int = MAX_SOURCES) -> list[tuple[int, int, float]]:
    """Local maxima above *threshold*, brightest first: ``[(x, y, value), ...]``."""
    filled = np.where(np.isfinite(image), image, -np.inf)
    peaks = (filled == maximum_filter(filled, size=size, mode="constant", cval=-np.inf)) & (filled > threshold)
    ny, nx = image.shape
    ys, xs = np.nonzero(peaks)
    keep = (xs >= border) & (xs < nx - border) & (ys >= border) & (ys < ny - border)
    xs, ys = xs[keep], ys[keep]
    order = np.argsort(filled[ys, xs])[::-1][:limit]
    return [(int(xs[i]), int(ys[i]), float(filled[ys[i], xs[i]])) for i in order]


def mark_blends(peaks, image: np.ndarray, sigma: float) -> list[bool]:
    """True for peaks whose centroid cannot be trusted because of a neighbour.

    A comparable peak (>= 30 % as bright) within 3.5 px, or a much brighter
    one (>= 5x) within 7 px, drags a windowed centroid around from epoch to
    epoch and mimics motion.
    """
    neighbours = find_peaks(image, 3.0 * sigma, border=1, size=3, limit=2000)
    if not neighbours:
        return [False] * len(peaks)
    nx_ = np.array([p[0] for p in neighbours], dtype=float)
    ny_ = np.array([p[1] for p in neighbours], dtype=float)
    nv = np.array([p[2] for p in neighbours], dtype=float)
    out = []
    for x, y, value in peaks:
        dist = np.hypot(nx_ - x, ny_ - y)
        other = dist > 0.5
        close = other & (dist <= 3.5) & (nv >= 0.3 * value)
        glare = other & (dist <= 7.0) & (nv >= 5.0 * value)
        out.append(bool(close.any() or glare.any()))
    return out


def _stamp(image: np.ndarray, x: float, y: float, radius: float):
    """Sub-image around (x, y) with its distance map."""
    ny, nx = image.shape
    r = int(math.ceil(radius)) + 1
    x0, x1 = max(0, int(round(x)) - r), min(nx, int(round(x)) + r + 1)
    y0, y1 = max(0, int(round(y)) - r), min(ny, int(round(y)) + r + 1)
    sub = image[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    return sub, np.hypot(xx - x, yy - y)


def local_background(image: np.ndarray, x: float, y: float, inner: float = ANNULUS[0],
                     outer: float = ANNULUS[1]) -> float:
    sub, dist = _stamp(image, x, y, outer)
    ring = sub[(dist >= inner) & (dist <= outer) & np.isfinite(sub)]
    return float(np.median(ring)) if ring.size >= 8 else 0.0


def windowed_centroid(image: np.ndarray, x: float, y: float, background: float = 0.0,
                      sigma: float = CENTROID_SIGMA, half: int = 3, iters: int = 15):
    """Iterative Gaussian-windowed centroid.  Returns ``(x, y, ok)``."""
    ny, nx = image.shape
    for _ in range(iters):
        ix, iy = int(round(x)), int(round(y))
        if ix - half < 0 or iy - half < 0 or ix + half >= nx or iy + half >= ny:
            return x, y, False
        sub = image[iy - half: iy + half + 1, ix - half: ix + half + 1]
        yy, xx = np.mgrid[iy - half: iy + half + 1, ix - half: ix + half + 1]
        val = np.where(np.isfinite(sub), sub - background, 0.0)
        w = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * sigma ** 2)) * val
        total = w.sum()
        if not np.isfinite(total) or total <= 0:
            return x, y, False
        dx = 2.0 * (w * (xx - x)).sum() / total
        dy = 2.0 * (w * (yy - y)).sum() / total
        step = math.hypot(dx, dy)
        if step > 1.0:                             # damp wild steps (blends, artefacts)
            dx, dy = dx / step, dy / step
        x, y = x + dx, y + dy
        if step < 1e-3:
            break
    return x, y, True


def ellipticity(image: np.ndarray, x: float, y: float, background: float, half: int = 4) -> float:
    """Elongation (0 = round) from Gaussian-weighted second moments about (x, y).

    A point source is round in every epoch stack, even when it moves; two
    stars too close to separate are elongated in all of them.
    """
    ny, nx = image.shape
    ix, iy = int(round(x)), int(round(y))
    if ix - half < 0 or iy - half < 0 or ix + half >= nx or iy + half >= ny:
        return float("nan")
    sub = image[iy - half: iy + half + 1, ix - half: ix + half + 1]
    yy, xx = np.mgrid[iy - half: iy + half + 1, ix - half: ix + half + 1]
    dx, dy = xx - x, yy - y
    w = np.where(np.isfinite(sub), sub - background, 0.0) * np.exp(-(dx ** 2 + dy ** 2) / (2.0 * 1.6 ** 2))
    w = np.clip(w, 0.0, None)
    total = w.sum()
    if total <= 0:
        return float("nan")
    mxx, myy, mxy = (w * dx * dx).sum() / total, (w * dy * dy).sum() / total, (w * dx * dy).sum() / total
    if mxx + myy <= 0:
        return float("nan")
    return float(math.sqrt((mxx - myy) ** 2 + 4.0 * mxy ** 2) / (mxx + myy))


def aperture_flux(image: np.ndarray, x: float, y: float, background: float, radius: float = APERTURE_R):
    """Sum of (image - background) inside *radius*: ``(flux, n_missing)``."""
    sub, dist = _stamp(image, x, y, radius)
    inside = dist <= radius
    finite = np.isfinite(sub)
    return float(np.sum(sub[inside & finite] - background)), int((inside & ~finite).sum())


def wing_flux(image: np.ndarray, x: float, y: float, hole: float):
    """Brightness of a saturated star from the wings of its image.

    Mean surface brightness in a ring just outside the saturated core, above
    the sky measured further out.  It scales with the star's flux, which is
    all a relative light curve needs.  Returns ``(value, noise)`` or NaNs when
    the rings do not fit in the image.
    """
    r1 = max(3.0, hole + 1.5)
    r2 = r1 + 3.5
    sky_in, sky_out = r2 + 2.5, r2 + 6.5
    ny, nx = image.shape
    if x - sky_out < 0 or y - sky_out < 0 or x + sky_out > nx - 1 or y + sky_out > ny - 1:
        return float("nan"), float("nan")
    sub, dist = _stamp(image, x, y, sky_out)
    finite = np.isfinite(sub)
    ring = sub[(dist >= r1) & (dist <= r2) & finite]
    sky = sub[(dist >= sky_in) & (dist <= sky_out) & finite]
    if ring.size < 20 or sky.size < 20:
        return float("nan"), float("nan")
    noise = _robust_sigma(sky) / math.sqrt(ring.size)
    return float(np.mean(ring) - np.median(sky)), float(noise if np.isfinite(noise) else 0.0)


def clipped_fraction(nexp: np.ndarray, clipped: np.ndarray | None, x: float, y: float, radius: float = 1.5) -> float:
    """Share of the samples within *radius* of (x, y) that the stacking rejected."""
    if clipped is None:
        return 0.0
    kept, dist = _stamp(nexp.astype(float), x, y, radius)
    lost, _ = _stamp(clipped.astype(float), x, y, radius)
    inside = dist <= radius
    total = float(kept[inside].sum() + lost[inside].sum())
    return float(lost[inside].sum() / total) if total > 0 else 0.0


def _weighted_slope(t: np.ndarray, v: np.ndarray, sig: np.ndarray):
    """Weighted least-squares line: ``(slope, slope_error, value_at_mean_time, mean_time, chi2)``."""
    w = 1.0 / sig ** 2
    tm = np.sum(w * t) / np.sum(w)
    vm = np.sum(w * v) / np.sum(w)
    denom = np.sum(w * (t - tm) ** 2)
    if denom <= 0:
        return 0.0, float("inf"), float(vm), float(tm), 0.0
    slope = float(np.sum(w * (t - tm) * (v - vm)) / denom)
    chi2 = float(np.sum(w * (v - vm - slope * (t - tm)) ** 2))
    return slope, float(1.0 / math.sqrt(denom)), float(vm), float(tm), chi2


def _motion_fit(t, x, y, sig):
    """Straight-line motion; errors are inflated by the track's own scatter."""
    mx, emx, xm, tm, chi_x = _weighted_slope(t, x, sig)
    my, emy, ym, _, chi_y = _weighted_slope(t, y, sig)
    dof = 2 * (len(t) - 2)
    inflate = math.sqrt(max(1.0, (chi_x + chi_y) / dof)) if dof > 0 else 1.0
    emx, emy = emx * inflate, emy * inflate
    significance = math.sqrt((mx / emx) ** 2 + (my / emy) ** 2) if np.isfinite(emx) and np.isfinite(emy) else 0.0
    return mx, my, emx, emy, xm, ym, tm, significance


# --------------------------------------------------------------------------
# Astrometry on one set of stacks
# --------------------------------------------------------------------------
def _track_sources(stacks: list[np.ndarray], nexp: list[np.ndarray], nsigma: float) -> dict | None:
    """Detect on the mean image, then centroid every source in every epoch."""
    n_ep = len(stacks)
    detection = _nanmean(stacks)
    det_sigma = _robust_sigma(detection)
    if not np.isfinite(det_sigma) or det_sigma <= 0:
        return None
    peaks = find_peaks(detection, nsigma * det_sigma)
    if not peaks:
        return None
    blended = np.array(mark_blends(peaks, detection, det_sigma))
    sky = [_robust_sigma(s) for s in stacks]
    npk = len(peaks)
    xs = np.full((npk, n_ep), np.nan)
    ys = np.full((npk, n_ep), np.nan)
    flux = np.full((npk, n_ep), np.nan)
    snr = np.full((npk, n_ep), np.nan)
    shape = np.full((npk, n_ep), np.nan)
    saturated = np.zeros(npk, dtype=bool)
    hole = np.zeros(npk)
    noise_area = math.sqrt(math.pi * APERTURE_R ** 2)
    typical = [float(np.median(n[n > 0])) if np.any(n > 0) else 0.0 for n in nexp]
    coverage = [n.astype(float) for n in nexp]

    for k, (px, py, _peak) in enumerate(peaks):
        cx, cy = float(px), float(py)
        for e, image in enumerate(stacks):
            bkg = local_background(image, cx, cy)
            x, y, ok = windowed_centroid(image, cx, cy, bkg)
            if not ok or math.hypot(x - px, y - py) > 4.0:
                continue
            f, missing = aperture_flux(image, x, y, bkg)
            noise = (sky[e] if np.isfinite(sky[e]) and sky[e] > 0 else det_sigma) * noise_area
            xs[k, e], ys[k, e], flux[k, e], snr[k, e] = x, y, f, f / noise
            shape[k, e] = ellipticity(image, x, y, bkg)
            cx, cy = x, y                              # follow the source from epoch to epoch
            if typical[e] > 0:                         # core missing in many exposures: saturated
                cover, dist = _stamp(coverage[e], x, y, 6.0)
                low = (cover < 0.5 * typical[e]) & (dist <= 6.0)
                hole[k] = max(hole[k], math.sqrt(low.sum() / math.pi))
                if missing > 0 or float(cover[dist <= 1.5].min()) < 0.6 * typical[e]:
                    saturated[k] = True
            elif missing > 0:
                saturated[k] = True

    good = np.isfinite(xs) & (snr >= 5.0)

    # unresolved blends: elongated in the epoch stacks (only judged where the signal allows)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        elong = np.nanmedian(np.where(snr >= 15.0, shape, np.nan), axis=1)
    blended = blended | ((np.nan_to_num(elong, nan=0.0) > MAX_ELLIPTICITY) & ~saturated)

    # residual registration: median shift of the bright, isolated, unsaturated stars
    ref = int(np.argmax(good.sum(axis=0)))
    off_x = np.zeros(n_ep)
    off_y = np.zeros(n_ep)
    scatter = np.full(n_ep, 0.02)
    registration = []
    for e in range(n_ep):
        n_ref = 0
        if e != ref:
            both = good[:, e] & good[:, ref] & (snr[:, e] >= 20) & (snr[:, ref] >= 20) & ~blended & ~saturated
            n_ref = int(both.sum())
            if n_ref >= 4:
                dx = xs[both, e] - xs[both, ref]
                dy = ys[both, e] - ys[both, ref]
                off_x[e], off_y[e] = np.median(dx), np.median(dy)
                sc = math.hypot(_robust_sigma(dx), _robust_sigma(dy)) / math.sqrt(2.0)
                scatter[e] = max(0.01, sc) if np.isfinite(sc) else 0.02
        registration.append({"dx": float(off_x[e]), "dy": float(off_y[e]), "scatter": float(scatter[e]), "n": n_ref})
    if n_ep > 1:
        scatter[ref] = float(np.median(np.delete(scatter, ref)))
    return {"peaks": peaks, "blended": blended, "saturated": saturated, "hole": hole, "good": good,
            "elongation": elong, "x": xs - off_x[None, :], "y": ys - off_y[None, :], "flux": flux, "snr": snr,
            "scatter": scatter, "registration": registration, "ref": ref}


def _fit_motions(track: dict, years: np.ndarray) -> list[dict]:
    rows = []
    for k in range(len(track["peaks"])):
        use = track["good"][k]
        n = int(use.sum())
        if n < 2:
            continue
        t = years[use]
        x, y = track["x"][k, use], track["y"][k, use]
        # centroid error: ~1.5 px / SNR for these PSFs, plus the field's own repeatability
        sig = np.sqrt((1.5 / np.maximum(track["snr"][k, use], 1.0)) ** 2 + track["scatter"][use] ** 2)
        if track["saturated"][k]:
            sig = np.maximum(sig, SATURATED_CENTROID_FLOOR)
        mx, my, emx, emy, xm, ym, tm, significance = _motion_fit(t, x, y, sig)
        span = float(t.max() - t.min())
        loo = significance
        if n >= 3:                               # must survive losing any single epoch
            for drop in range(n):
                keep = np.arange(n) != drop
                if np.ptp(t[keep]) <= 0:
                    loo = 0.0
                    continue
                loo = min(loo, _motion_fit(t[keep], x[keep], y[keep], sig[keep])[7])
        med_snr = float(np.median(track["snr"][k, use]))
        shift = math.hypot(mx, my) * span
        moving = bool(significance >= MOTION_SIGMA and shift >= MOTION_MIN_SHIFT_PX and med_snr >= 8.0
                      and not track["blended"][k]
                      and (n < 3 or loo >= MOTION_LOO_SIGMA)
                      and (n >= 3 or (significance >= 8.0 and not track["saturated"][k])))
        rows.append({"k": k, "use": use, "mx": mx, "my": my, "emx": emx, "emy": emy, "xm": xm, "ym": ym,
                     "tm": tm, "significance": significance, "loo": loo, "shift": shift, "span": span,
                     "snr": med_snr, "moving": moving})
    return rows


# --------------------------------------------------------------------------
# Variability, band by band
# --------------------------------------------------------------------------
def _band_photometry(rows: list[dict], track: dict, epochs: list[dict]) -> dict[str, dict]:
    """Flux and noise of every source in every epoch of every band.

    Unsaturated sources: aperture flux.  Saturated ones: brightness of the
    wings.  Each band's epochs are put on a common zero point with the bright
    unsaturated stars when there are enough of them.
    """
    n_ep, n_rows = len(epochs), len(rows)
    noise_area = math.sqrt(math.pi * APERTURE_R ** 2)
    sat = np.array([bool(track["saturated"][row["k"]]) for row in rows])
    bad = np.array([bool(track["blended"][row["k"]]) for row in rows])
    out = {}
    for band in BAND_IDS:
        present = [e for e, ep in enumerate(epochs) if band in ep["bands"]]
        if len(present) < 2:
            continue
        flux = np.full((n_rows, n_ep), np.nan)
        err = np.full((n_rows, n_ep), np.nan)
        trimmed = np.zeros(n_rows)
        for e in present:
            entry = epochs[e]["bands"][band]
            image, kept = entry[0], entry[1]
            lost = entry[2] if len(entry) > 2 else None
            sky = _robust_sigma(image)
            if not np.isfinite(sky) or sky <= 0:
                continue
            for r, row in enumerate(rows):
                k = row["k"]
                if not track["good"][k, e]:
                    continue
                x, y = track["x"][k, e], track["y"][k, e]
                if sat[r]:
                    flux[r, e], err[r, e] = wing_flux(image, x, y, track["hole"][k])
                else:
                    f, missing = aperture_flux(image, x, y, local_background(image, x, y))
                    if missing == 0:
                        flux[r, e], err[r, e] = f, sky * noise_area
                    trimmed[r] = max(trimmed[r], clipped_fraction(kept, lost, x, y))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            snr = flux / err
            ref = int(np.argmax(np.isfinite(flux).sum(axis=0)))
            for e in present:                               # zero point of each epoch from the bright stars
                if e == ref:
                    continue
                ok = np.isfinite(flux[:, e]) & np.isfinite(flux[:, ref]) & (snr[:, e] >= 20) & (snr[:, ref] >= 20) & ~sat & ~bad
                if ok.sum() >= 4:
                    zero = float(np.median(flux[ok, e] / flux[ok, ref]))
                    if zero > 0:
                        flux[:, e] /= zero
                        err[:, e] /= zero
        out[band] = {"flux": flux, "err": err, "trimmed": trimmed}
    return out


def _band_amplitudes(photometry: dict[str, dict], n_rows: int) -> dict[str, dict]:
    """Peak-to-peak amplitude of every source in every band, with its noise."""
    out = {}
    for band, data in photometry.items():
        flux, err = data["flux"], data["err"]
        amp = np.full(n_rows, np.nan)
        amp_err = np.full(n_rows, np.nan)
        trend = np.full(n_rows, np.nan)
        snr = np.full(n_rows, np.nan)
        count = np.zeros(n_rows, dtype=int)
        for r in range(n_rows):
            ok = np.isfinite(flux[r]) & np.isfinite(err[r])
            if ok.sum() < 2:
                continue
            f, s = flux[r, ok], err[r, ok]
            mean = float(np.mean(f))
            if mean <= 0:
                continue
            hi, lo = int(np.argmax(f)), int(np.argmin(f))
            amp[r] = float((f[hi] - f[lo]) / mean)
            amp_err[r] = float(math.hypot(s[hi], s[lo]) / mean)
            trend[r] = float(f[-1] / f[0] - 1.0) if f[0] > 0 else np.nan
            snr[r] = float(np.median(f / np.maximum(s, 1e-12)))
            count[r] = int(ok.sum())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            relative = flux / np.nanmean(flux, axis=1)[:, None]
        out[band] = {"amp": amp, "err": amp_err, "trend": trend, "snr": snr, "n": count,
                     "trimmed": data["trimmed"], "relative": relative}
    return out


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def analyse(epochs: list[dict], grid, nsigma: float = 8.0) -> dict:
    """Measure the sources of a field in every epoch and flag what changed.

    *epochs* is a time-ordered list of ``{"index", "year", "all": (data, nexp),
    "bands": {"D1": (data, nexp), ...}}`` on the common *grid*.
    """
    empty = {"sources": [], "n_sources": 0, "n_moving": 0, "n_variable": 0, "registration": []}
    n_ep = len(epochs)
    if n_ep < 2:
        return empty
    years = np.array([ep["year"] for ep in epochs], dtype=float)
    baseline = float(years.max() - years.min())
    if baseline <= 0:
        return empty
    scale = grid.scale
    to_mjy = (scale / ARCSEC_PER_RAD) ** 2 * 1e9

    # --- detection + astrometry on the all-detector stacks, then on the red stacks
    passes = [("all", [ep["all"][0] for ep in epochs], [ep["all"][1] for ep in epochs])]
    if all(any(b in ep["bands"] for b in RED_BANDS) for ep in epochs):
        red, red_n = [], []
        for ep in epochs:
            present = [ep["bands"][b] for b in RED_BANDS if b in ep["bands"]]
            red.append(_nanmean([p[0] for p in present]))
            red_n.append(np.sum([p[1].astype(np.int32) for p in present], axis=0))
        passes.append(("red", red, red_n))

    measured = []
    registration = []
    for name, stacks, nexp in passes:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            track = _track_sources(stacks, nexp, nsigma)
        if track is None:
            continue
        rows = _fit_motions(track, years)
        amplitudes = _band_amplitudes(_band_photometry(rows, track, epochs), len(rows))
        if name == "all":
            registration = [dict(reg, epoch=epochs[e]["index"]) for e, reg in enumerate(track["registration"])]
        measured.append((name, track, rows, amplitudes))
    if not measured:
        return empty

    # --- how much do ordinary stars appear to vary here, band by band? -----------------
    thresholds = {}
    name0, track0, rows0, amps0 = measured[0]
    steady = np.array([not track0["saturated"][row["k"]] and not track0["blended"][row["k"]] for row in rows0])
    for band, data in amps0.items():
        ok = steady & np.isfinite(data["amp"]) & (data["snr"] >= 20) & (data["trimmed"] <= MAX_CLIPPED_FRACTION)
        if ok.sum() >= 5:
            spread = _robust_sigma(data["amp"][ok])
            spread = spread if np.isfinite(spread) and spread > 0 else 0.02
            thresholds[band] = max(VARIABLE_MIN_AMPLITUDE, float(np.median(data["amp"][ok])) + 6.0 * spread)
        else:
            thresholds[band] = 1.5 * VARIABLE_MIN_AMPLITUDE
    for band in BAND_IDS:
        thresholds.setdefault(band, 1.5 * VARIABLE_MIN_AMPLITUDE)

    # --- assemble; red-only sources are appended after the all-detector ones -------------
    sources = []
    for name, track, rows, amplitudes in measured:
        for r, row in enumerate(rows):
            if name == "red" and any(math.hypot(s["x"] - row["xm"], s["y"] - row["ym"]) < 2.5 for s in sources):
                continue
            k, use = row["k"], row["use"]
            saturated = bool(track["saturated"][k])
            blended = bool(track["blended"][k])
            floor = VARIABLE_MIN_AMPLITUDE_SATURATED if saturated else 0.0
            bands = []
            for band in BAND_IDS:
                data = amplitudes.get(band)
                if data is None or not np.isfinite(data["amp"][r]):
                    continue
                amp, amp_err = float(data["amp"][r]), float(data["err"][r])
                trimmed = float(data["trimmed"][r])
                varying = bool(data["snr"][r] >= 8.0 and amp >= max(thresholds[band], floor) and amp >= 5.0 * amp_err
                               and trimmed <= MAX_CLIPPED_FRACTION)
                bands.append({"band": band, "amp": amp, "err": amp_err, "n": int(data["n"][r]),
                              "trend": None if not np.isfinite(data["trend"][r]) else float(data["trend"][r]),
                              "clipped": trimmed, "detected": bool(data["snr"][r] >= 8.0), "varying": varying})
            changing = [b for b in bands if b["varying"]]
            detected = [b for b in bands if b["detected"]]
            # relative brightness per band and epoch (each band divided by its own mean)
            curve = {}
            for b in detected:
                rel = amplitudes[b["band"]]["relative"][r]
                curve[b["band"]] = [[epochs[e]["index"], round(float(rel[e]), 4)] for e in range(n_ep) if np.isfinite(rel[e])]
            needed = max(VARIABLE_MIN_BANDS, math.ceil(VARIABLE_BAND_MAJORITY * len(detected)))
            variable = bool(not blended and row["snr"] >= 10.0 and len(changing) >= needed)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ra, dec = grid.wcs.wcs_pix2world(row["xm"], row["ym"], 0)
            pm_ra = -row["mx"] * scale            # +x points west on the north-up grid
            pm_dec = row["my"] * scale
            same_epochs = bool(changing) and all(b["n"] == int(use.sum()) for b in changing)
            trends = [b["trend"] for b in changing if b["trend"] is not None] if same_epochs else []
            sources.append({
                "x": float(row["xm"]), "y": float(row["ym"]), "year": float(row["tm"]),
                "ra": float(ra), "dec": float(dec),
                "detected_in": name,
                "snr": row["snr"],
                "flux_mjy": float(np.nanmean(track["flux"][k, use]) * to_mjy),
                "n_epochs": int(use.sum()),
                "pm_ra": pm_ra, "pm_dec": pm_dec, "pm": math.hypot(pm_ra, pm_dec),
                "pm_err": math.hypot(row["emx"], row["emy"]) * scale / math.sqrt(2.0),
                "pm_pa": (math.degrees(math.atan2(pm_ra, pm_dec)) + 360.0) % 360.0,
                "motion_sig": float(row["significance"]),
                "motion_loo": float(row["loo"]),
                "shift_arcsec": float(row["shift"] * scale),
                "var_amp": float(np.median([b["amp"] for b in changing])) if changing else
                           (float(np.max([b["amp"] for b in bands])) if bands else 0.0),
                "var_bands": len(changing),
                "colour_change": bool(trends and min(trends) < -0.05 and max(trends) > 0.05),
                "bands": bands,
                "curve": curve,
                "moving": row["moving"], "variable": variable,
                "saturated": saturated, "blended": blended,
                "elongation": None if not np.isfinite(track["elongation"][k]) else float(track["elongation"][k]),
                "track": [{"epoch": epochs[e]["index"], "x": float(track["x"][k, e]), "y": float(track["y"][k, e]),
                           "snr": float(track["snr"][k, e])} for e in range(n_ep) if use[e]],
            })

    sources.sort(key=lambda s: (not s["moving"], not s["variable"], -s["snr"]))
    sources = sources[:MAX_SOURCES]
    for i, src in enumerate(sources, 1):
        src["id"] = i
        if i > 25 and not (src["moving"] or src["variable"]):
            src["curve"] = {}
    scatter = [reg["scatter"] for reg in registration if reg.get("n")]
    return {
        "sources": sources,
        "n_sources": len(sources),
        "n_moving": sum(s["moving"] for s in sources),
        "n_variable": sum(s["variable"] for s in sources),
        "registration": registration,
        "baseline_years": baseline,
        "variability_thresholds": thresholds,
        "position_scatter_px": float(np.median(scatter)) if scatter else 0.02,
        "note": "Candidates only. Motion: at least 5 sigma and 0.25 pixel over the baseline after removing the "
                "median shift of the field stars, with the scatter of the source's own track counted as error; "
                "isolated sources only. Variability: a peak-to-peak change, well above what the field stars "
                "show, in at least two detector bands and in most of the bands where the source is detected. "
                "Saturated stars are measured from the wings of their image.",
    }
