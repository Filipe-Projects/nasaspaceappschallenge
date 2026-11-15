"""Time grouping, reprojection onto a common grid and stacking."""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import map_coordinates

from . import config
from .cutouts import Cutout, world_to_pixel
from .sia import Exposure, mjd_to_iso, mjd_to_year


# --------------------------------------------------------------------------
# Epochs
# --------------------------------------------------------------------------
@dataclass
class Epoch:
    index: int
    exposures: list[Exposure] = field(default_factory=list)
    kind: str = "visit"            # "visit": one survey pass; "bin": slice of continuous coverage
    selected: list[Exposure] | None = None

    @property
    def used(self) -> list[Exposure]:
        return self.exposures if self.selected is None else self.selected

    @property
    def mjd_start(self) -> float:
        return min(e.mjd_start for e in self.exposures)

    @property
    def mjd_end(self) -> float:
        return max(e.mjd_end for e in self.exposures)

    @property
    def mjd_mean(self) -> float:
        return float(np.mean([e.mjd for e in self.exposures]))

    @property
    def survey(self) -> int:
        return survey_number(self.mjd_mean)

    @property
    def label(self) -> str:
        start, end = mjd_to_iso(self.mjd_start), mjd_to_iso(self.mjd_end)
        return start if start == end else f"{start} to {end}"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "kind": self.kind,
            "label": self.label,
            "mjd_start": self.mjd_start,
            "mjd_end": self.mjd_end,
            "mjd_mean": self.mjd_mean,
            "year": mjd_to_year(self.mjd_mean),
            "date_start": mjd_to_iso(self.mjd_start),
            "date_end": mjd_to_iso(self.mjd_end),
            "survey": self.survey,
            "n_exposures": len(self.exposures),
            "n_selected": len(self.used),
            "detectors": sorted({e.detector for e in self.exposures}),
        }


def survey_number(mjd: float) -> int:
    return int((float(mjd) - config.SURVEY_START_MJD) // config.SURVEY_LENGTH_DAYS) + 1


def group_epochs(exposures: list[Exposure], gap_days: float | None = None,
                 max_span_days: float | None = None, bin_days: float | None = None,
                 min_bin_exposures: int = 6) -> list[Epoch]:
    """Split exposures into the time steps of the movie.

    SPHEREx revisits a typical spot for a few weeks during each six-monthly
    all-sky survey, so exposures cluster into visits separated by months: a
    gap longer than *gap_days* starts a new epoch.  Near the ecliptic poles
    coverage is continuous; a cluster longer than *max_span_days* is cut into
    calendar bins of *bin_days* (aligned to a fixed origin, so the bins do
    not shift when new data arrive).
    """
    gap = config.EPOCH_GAP_DAYS if gap_days is None else gap_days
    max_span = config.EPOCH_MAX_SPAN_DAYS if max_span_days is None else max_span_days
    width = config.EPOCH_BIN_DAYS if bin_days is None else bin_days

    clusters: list[list[Exposure]] = []
    for exp in sorted(exposures, key=lambda e: e.mjd):
        if clusters and exp.mjd - clusters[-1][-1].mjd <= gap:
            clusters[-1].append(exp)
        else:
            clusters.append([exp])

    groups: list[tuple[str, list[Exposure]]] = []
    for cluster in clusters:
        if cluster[-1].mjd - cluster[0].mjd <= max_span:
            groups.append(("visit", cluster))
            continue
        bins: dict[int, list[Exposure]] = {}
        for exp in cluster:
            bins.setdefault(int(math.floor((exp.mjd - config.SURVEY_START_MJD) / width)), []).append(exp)
        ordered = [bins[k] for k in sorted(bins)]
        merged: list[list[Exposure]] = []
        for chunk in ordered:                      # fold tiny leading/trailing bins into a neighbour
            if merged and (len(chunk) < min_bin_exposures or len(merged[-1]) < min_bin_exposures):
                merged[-1] = merged[-1] + chunk
            else:
                merged.append(chunk)
        groups.extend(("bin", chunk) for chunk in merged)

    return [Epoch(index=i + 1, exposures=chunk, kind=kind) for i, (kind, chunk) in enumerate(groups)]


def select_exposures(epochs: list[Epoch], max_total: int | None = None) -> int:
    """Thin out the exposure list when a position has thousands of them.

    Keeps an even sample in time for every detector of every epoch, so each
    band stack still spans the epoch and the full wavelength range.  Returns
    the number of exposures selected.
    """
    limit = config.MAX_EXPOSURES if max_total is None else int(max_total)
    total = sum(len(ep.exposures) for ep in epochs)
    if total <= limit or not epochs:
        for ep in epochs:
            ep.selected = None
        return total
    per_epoch = max(6, limit // len(epochs))
    count = 0
    for ep in epochs:
        by_det: dict[int, list[Exposure]] = {}
        for exp in ep.exposures:
            by_det.setdefault(exp.detector, []).append(exp)
        quota = max(1, per_epoch // max(1, len(by_det)))
        chosen: list[Exposure] = []
        for det in sorted(by_det):
            items = sorted(by_det[det], key=lambda e: e.mjd)
            if len(items) <= quota:
                chosen.extend(items)
            else:
                idx = np.unique(np.round(np.linspace(0, len(items) - 1, quota)).astype(int))
                chosen.extend(items[i] for i in idx)
        ep.selected = sorted(chosen, key=lambda e: (e.mjd, e.detector))
        count += len(ep.selected)
    return count


# --------------------------------------------------------------------------
# Target grid
# --------------------------------------------------------------------------
def make_target_wcs(ra: float, dec: float, npix: int, scale_arcsec: float) -> WCS:
    """North-up, east-left tangent-plane grid centred on (ra, dec)."""
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [float(ra), float(dec)]
    wcs.wcs.crpix = [(npix + 1) / 2.0, (npix + 1) / 2.0]
    wcs.wcs.cdelt = [-scale_arcsec / 3600.0, scale_arcsec / 3600.0]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.radesys = "ICRS"
    wcs.wcs.equinox = 2000.0
    wcs.pixel_shape = (npix, npix)
    return wcs


def plan_grid(fov_arcmin: float, scale_arcsec: float | None = None) -> tuple[int, float]:
    """Pick (npix, scale) for a field of view: native pixels unless too many."""
    fov_arcsec = float(fov_arcmin) * 60.0
    scale = float(scale_arcsec) if scale_arcsec else config.PIXEL_SCALE_ARCSEC
    npix = int(math.ceil(fov_arcsec / scale))
    if npix > config.MAX_OUTPUT_PIXELS:
        npix = config.MAX_OUTPUT_PIXELS
        scale = fov_arcsec / npix
    if npix % 2 == 0:
        npix += 1
    return npix, scale


class TargetGrid:
    """The common output grid plus everything needed to map exposures onto it.

    The sky -> detector mapping is smooth (a cubic SIP polynomial over 2040
    pixels), so it is evaluated on a coarse lattice of control points and
    interpolated bilinearly; the interpolation error is below 1e-3 pixel.
    """

    STEP = 8

    def __init__(self, ra: float, dec: float, npix: int, scale_arcsec: float):
        self.ra, self.dec = float(ra), float(dec)
        self.npix, self.scale = int(npix), float(scale_arcsec)
        self.wcs = make_target_wcs(ra, dec, npix, scale_arcsec)
        edge = np.array([-0.5, (npix - 1) / 2.0, npix - 0.5])
        ox, oy = np.meshgrid(edge, edge)
        self.outline_ra, self.outline_dec = self._sky(ox.ravel(), oy.ravel())
        if npix <= 3 * self.STEP:
            nodes = np.arange(npix, dtype=float)
        else:
            nodes = np.unique(np.append(np.arange(0, npix - 1, self.STEP), npix - 1)).astype(float)
        self.nodes = nodes
        gx, gy = np.meshgrid(nodes, nodes)
        self.node_ra, self.node_dec = self._sky(gx.ravel(), gy.ravel())
        full = np.arange(npix, dtype=float)
        self._idx = np.clip(np.searchsorted(nodes, full, side="right") - 1, 0, len(nodes) - 2)
        self._w = (full - nodes[self._idx]) / (nodes[self._idx + 1] - nodes[self._idx])

    def _sky(self, x, y):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return self.wcs.wcs_pix2world(x, y, 0)

    def _interpolate(self, lattice: np.ndarray) -> np.ndarray:
        i, w = self._idx, self._w
        rows = lattice[:, i] * (1.0 - w) + lattice[:, i + 1] * w
        return rows[i, :] * (1.0 - w)[:, None] + rows[i + 1, :] * w[:, None]

    def source_coords(self, wcs: WCS) -> tuple[np.ndarray, np.ndarray]:
        """Pixel coordinates in *wcs* of every pixel of the target grid."""
        sx, sy = world_to_pixel(wcs, self.node_ra, self.node_dec)
        m = len(self.nodes)
        return self._interpolate(sx.reshape(m, m)), self._interpolate(sy.reshape(m, m))

    def to_dict(self) -> dict:
        """Minimal FITS-style description for the browser."""
        w = self.wcs.wcs
        return {
            "naxis": [self.npix, self.npix],
            "crpix": [float(w.crpix[0]), float(w.crpix[1])],
            "crval": [float(w.crval[0]), float(w.crval[1])],
            "cdelt": [float(w.cdelt[0]), float(w.cdelt[1])],
            "ctype": list(w.ctype),
        }


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
def sigma_clipped_median(values, sigma: float = 3.0, iters: int = 3) -> float:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return float("nan")
    for _ in range(iters):
        med = np.median(vals)
        mad = 1.4826 * np.median(np.abs(vals - med))
        if mad <= 0:
            break
        keep = np.abs(vals - med) < sigma * mad
        if keep.all():
            break
        vals = vals[keep]
    return float(np.median(vals))


def robust_rms(values) -> float:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size < 2:
        return float("nan")
    med = np.median(vals)
    return float(1.4826 * np.median(np.abs(vals - med)))


# --------------------------------------------------------------------------
# Reprojection
# --------------------------------------------------------------------------
@dataclass
class ReprojectedFrame:
    exposure: Exposure
    data: np.ndarray               # float32, background-subtracted MJy/sr, NaN = no data
    flags: np.ndarray | None       # uint32 nearest-neighbour flags (when read)
    background: float
    noise: float
    x_src: float                   # full-frame detector position of the grid centre
    y_src: float


def reproject_cutout(cutout: Cutout, grid: TargetGrid, mask_bits: int = 0,
                     order: int = 1) -> ReprojectedFrame | None:
    """Resample one cutout onto the target grid (bilinear, NaN-aware).

    Non-finite pixels, and pixels flagged with *mask_bits* when flags were
    read, count as missing: the interpolation renormalises by the local
    coverage so one bad pixel does not punch a hole through its neighbours.
    The cutout's own sky level (mostly zodiacal light, which depends on the
    wavelength of each exposure) is removed first.
    """
    npix = grid.npix
    sx, sy = grid.source_coords(cutout.wcs)
    img = cutout.image.astype(np.float64)
    good = np.isfinite(img)
    if mask_bits and cutout.flags is not None:
        good &= (cutout.flags & np.uint32(mask_bits)) == 0
    if good.sum() < 16:
        return None
    background = sigma_clipped_median(img[good])
    noise = robust_rms(img[good])
    img = np.where(good, img - background, 0.0)
    weight = good.astype(np.float64)

    coords = [sy, sx]
    values = map_coordinates(img, coords, order=order, mode="constant", cval=0.0)
    cover = map_coordinates(weight, coords, order=order, mode="constant", cval=0.0)
    data = np.where(cover > 0.5, values / np.maximum(cover, 1e-6), np.nan)
    if not np.isfinite(data).any():
        return None

    flags = None
    if cutout.flags is not None:
        ny, nx = cutout.shape
        ix = np.rint(sx).astype(int)
        iy = np.rint(sy).astype(int)
        inside = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        flags = np.zeros((npix, npix), dtype=np.uint32)
        flags[inside] = cutout.flags[iy[inside], ix[inside]]

    centre = npix // 2
    return ReprojectedFrame(
        exposure=cutout.exposure,
        data=data.astype(np.float32),
        flags=flags,
        background=float(background),
        noise=float(noise),
        x_src=float(sx[centre, centre]) + cutout.x0,
        y_src=float(sy[centre, centre]) + cutout.y0,
    )


# --------------------------------------------------------------------------
# Stacking
# --------------------------------------------------------------------------
@dataclass
class StackResult:
    data: np.ndarray               # float32 mean, NaN where no exposure contributed
    nexp: np.ndarray               # uint16 contributing exposures per pixel
    flags: np.ndarray | None       # uint32 bits set in a sizeable fraction of the exposures
    n_input: int
    clipped: np.ndarray | None = None   # uint16 samples removed by the outlier rejection, per pixel


def majority_flags(frames: list[ReprojectedFrame], nexp: np.ndarray, fraction: float = 0.3) -> np.ndarray:
    """Flag bits set in more than *fraction* of the exposures covering a pixel.

    A plain OR over a hundred exposures would flag almost every pixel (each
    exposure has its own cosmic rays and outliers); the bits that recur, such
    as saturation around bright stars, are the ones worth showing.
    """
    shape = frames[0].flags.shape
    counts: dict[int, np.ndarray] = {}
    for frame in frames:
        fl = frame.flags
        present = int(np.bitwise_or.reduce(fl, axis=None))
        bit = 0
        while present >> bit:
            if (present >> bit) & 1:
                if bit not in counts:
                    counts[bit] = np.zeros(shape, dtype=np.int32)
                counts[bit] += ((fl >> np.uint32(bit)) & np.uint32(1)).astype(np.int32)
            bit += 1
    out = np.zeros(shape, dtype=np.uint32)
    denom = np.maximum(nexp.astype(np.int32), 1)
    for bit, count in counts.items():
        out[count > fraction * denom] |= np.uint32(1 << bit)
    return out


def stack_frames(frames: list[ReprojectedFrame], clip_sigma: float = 5.0) -> StackResult:
    """Robust average of aligned frames.

    Five or more frames: mean after rejecting samples further than
    *clip_sigma* robust deviations from the per-pixel median (cosmic rays,
    hot pixels, passing satellites and asteroids).  Three or four: median.
    Fewer: plain mean.
    """
    cube = np.stack([f.data for f in frames]).astype(np.float32)
    n = cube.shape[0]
    valid = np.sum(np.isfinite(cube), axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if n >= 5:
            med = np.nanmedian(cube, axis=0)
            dev = np.abs(cube - med)
            mad = 1.4826 * np.nanmedian(dev, axis=0)
            cube[(dev > clip_sigma * mad) & (mad > 0)] = np.nan
            data = np.nanmean(cube, axis=0)
        elif n >= 3:
            data = np.nanmedian(cube, axis=0)
        else:
            data = np.nanmean(cube, axis=0)
        nexp = np.sum(np.isfinite(cube), axis=0)
    flags = None
    if all(f.flags is not None for f in frames):
        flags = frames[0].flags.copy() if n == 1 else majority_flags(frames, nexp)
    return StackResult(data.astype(np.float32), nexp.astype(np.uint16), flags, n,
                       clipped=(valid - nexp).astype(np.uint16))
