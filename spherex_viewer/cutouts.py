"""Cutout data structure, WCS helpers, flag names and the wavelength lookup."""
from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, NoConvergence, Sip

from . import config
from .sia import Exposure

FLAG_PREFIX = "MP_"

# Bit numbers of the Level 2 pixel flags: Table 16 of the SPHEREx Explanatory
# Supplement v2.0 (2026-08-25); bits 4 and 5 from the QR2 FLAGS headers.
# QR2 files repeat these as MP_* header keywords, QR3 files do not.
DEFAULT_FLAG_DEFS: dict[str, int] = {
    "TRANSIENT": 0, "OVERFLOW": 1, "SUR_ERROR": 2, "PHANTOM": 4, "REFERENCE": 5,
    "NONFUNC": 6, "DICHROIC": 7, "MISSING_DATA": 9, "HOT": 10, "COLD": 11,
    "FULLSAMPLE": 12, "PHANMISS": 14, "NONLINEAR": 15, "PERSIST": 17,
    "OUTLIER": 19, "CROSSTALK": 20, "SOURCE": 21, "GHOST": 22, "GHOST_FPA": 23,
    "GHOST_EXT": 24, "STREAK": 25, "BLOOM": 26, "SNOWBALL": 27, "HALO": 28,
    "SATELLITE_HALO": 29,
}


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------
def flag_definitions(header: fits.Header) -> dict[str, int]:
    """Map flag name -> bit number from the MP_* keywords of a FLAGS header."""
    defs = {}
    for key in header:
        if key.startswith(FLAG_PREFIX):
            try:
                defs[key[len(FLAG_PREFIX):]] = int(header[key])
            except (TypeError, ValueError):
                continue
    return defs


def mask_from_names(defs: dict[str, int], names) -> int:
    mask = 0
    for name in names:
        bit = defs.get(str(name).upper())
        if bit is not None:
            mask |= 1 << int(bit)
    return mask


def flag_names(defs: dict[str, int], value: int) -> list[str]:
    value = int(value)
    return [name for name, bit in sorted(defs.items(), key=lambda kv: kv[1]) if value & (1 << bit)]


# --------------------------------------------------------------------------
# Wavelength lookup (WCS-WAVE extension)
# --------------------------------------------------------------------------
class WaveLookup:
    """Bilinear interpolation of the WCS-WAVE control-point grid.

    The table stores a coarse grid of 1-based pixel positions (X, Y) and, for
    each control point, the central wavelength and bandwidth in microns.
    IRSA documents bilinear interpolation between control points as accurate
    to about 1 nm.  The table is identical for every exposure of a detector
    within a pipeline version, so it is read once and shared.
    """

    def __init__(self, x, y, values):
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.values = np.asarray(values, dtype=float).reshape(len(self.y), len(self.x), 2)

    def __call__(self, x0based, y0based):
        """Wavelength and bandwidth (um) at 0-based full-frame pixel coordinates."""
        px = np.asarray(x0based, dtype=float) + 1.0
        py = np.asarray(y0based, dtype=float) + 1.0
        py, px = np.broadcast_arrays(py, px)
        ix = np.clip(np.searchsorted(self.x, px, side="right") - 1, 0, len(self.x) - 2)
        iy = np.clip(np.searchsorted(self.y, py, side="right") - 1, 0, len(self.y) - 2)
        tx = (px - self.x[ix]) / (self.x[ix + 1] - self.x[ix])
        ty = (py - self.y[iy]) / (self.y[iy + 1] - self.y[iy])
        v = self.values
        out = (v[iy, ix] * ((1 - tx) * (1 - ty))[..., None] + v[iy, ix + 1] * (tx * (1 - ty))[..., None]
               + v[iy + 1, ix] * ((1 - tx) * ty)[..., None] + v[iy + 1, ix + 1] * (tx * ty)[..., None])
        return out[..., 0], out[..., 1]

    def to_json(self) -> dict:
        return {"x": self.x.tolist(), "y": self.y.tolist(), "values": self.values.ravel().tolist()}

    @classmethod
    def from_json(cls, data: dict) -> "WaveLookup":
        return cls(data["x"], data["y"], data["values"])


# --------------------------------------------------------------------------
# WCS helpers
# --------------------------------------------------------------------------
_KEEP = re.compile(
    r"^(NAXIS[12]?|BITPIX|WCSAXES|CTYPE[12]|CRPIX[12]|CRVAL[12]|CUNIT[12]|CDELT[12]|"
    r"PC[12]_[12]|CD[12]_[12]|LONPOLE|LATPOLE|RADESYS|EQUINOX|"
    r"(A|B|AP|BP)_(ORDER|\d_\d)|MJD-(OBS|AVG|BEG|END)|DATE-(OBS|AVG)|"
    r"DETECTOR|OBSID|EXPTIME|BUNIT|PSF_FWHM|BZERO|BSCALE)$"
)


def reduce_header(header: fits.Header) -> list[list]:
    """Keep only the cards needed later (celestial WCS with SIP, times, ids).

    A full IMAGE header has ~270 cards (20 kB); the reduced one is small enough
    to cache for every file ever touched.
    """
    cards = []
    for key in header:
        if key and _KEEP.match(key):
            value = header[key]
            if isinstance(value, (bool, int, float, str)):
                cards.append([key, value])
    return cards


def header_from_cards(cards) -> fits.Header:
    header = fits.Header()
    for key, value in cards:
        header[key] = value
    return header


def make_wcs(header_or_cards) -> WCS:
    if not isinstance(header_or_cards, fits.Header):
        fast = wcs_from_cards(header_or_cards)
        if fast is not None:
            return fast
    header = header_or_cards if isinstance(header_or_cards, fits.Header) else header_from_cards(header_or_cards)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return WCS(header)


# Every SPHEREx Level 2 image so far has the same celestial WCS: TAN with SIP
# distortion (A, B, AP, BP), a PC matrix and CDELT.  Going through
# astropy.io.fits.Header to build it costs ~40 ms of pure Python per exposure
# (70% of a whole frame build, all of it under the GIL); setting the same
# numbers directly takes ~0.2 ms.  Any other layout takes the general path.
_SIP_COEFF = re.compile(r"^(A|B|AP|BP)_(\d+)_(\d+)$")
_FAST_CTYPES = ("RA---TAN-SIP", "DEC--TAN-SIP")
_FAST_KEYS = {"CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CDELT1", "CDELT2", "PC1_1", "PC1_2", "PC2_1", "PC2_2",
              "CTYPE1", "CTYPE2", "CUNIT1", "CUNIT2", "LONPOLE", "LATPOLE", "RADESYS", "WCSAXES", "NAXIS",
              "NAXIS1", "NAXIS2", "A_ORDER", "B_ORDER", "AP_ORDER", "BP_ORDER"}
# Cards kept with the WCS that play no part in the sky <-> pixel transformation.
_PASSIVE_KEYS = {"BITPIX", "BUNIT", "DETECTOR", "OBSID", "EXPTIME", "PSF_FWHM", "BZERO", "BSCALE",
                 "MJD-OBS", "MJD-AVG", "MJD-BEG", "MJD-END", "DATE-OBS", "DATE-AVG"}
_fast_wcs = {"enabled": True, "checked": 0}


def _sip_matrix(coeffs: dict, order) -> np.ndarray | None:
    """The coefficient matrix exactly as astropy's SIP reader fills it."""
    if order is None or int(order) <= 1:
        return None
    m = int(order)
    out = np.zeros((m + 1, m + 1), np.double)
    for (i, j), value in coeffs.items():
        if i + j <= m:
            out[i, j] = value
    return out


def _build_fast_wcs(cards) -> WCS | None:
    h: dict = {}
    sip: dict[str, dict] = {"A": {}, "B": {}, "AP": {}, "BP": {}}
    for key, value in cards:
        match = _SIP_COEFF.match(key)
        if match:
            sip[match.group(1)][(int(match.group(2)), int(match.group(3)))] = float(value)
        elif key in _FAST_KEYS:
            h[key] = value
        elif key not in _PASSIVE_KEYS:
            return None
    if (h.get("CTYPE1"), h.get("CTYPE2")) != _FAST_CTYPES or int(h.get("WCSAXES", 2)) != 2:
        return None
    if not all(k in h for k in ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "A_ORDER", "B_ORDER")):
        return None
    a, b = _sip_matrix(sip["A"], h.get("A_ORDER")), _sip_matrix(sip["B"], h.get("B_ORDER"))
    ap, bp = _sip_matrix(sip["AP"], h.get("AP_ORDER")), _sip_matrix(sip["BP"], h.get("BP_ORDER"))
    if a is None or b is None or (ap is None) != (bp is None):
        return None
    crpix = (float(h["CRPIX1"]), float(h["CRPIX2"]))
    out = WCS(naxis=2)
    out.wcs.ctype = list(_FAST_CTYPES)
    out.wcs.crpix = list(crpix)
    out.wcs.crval = [float(h["CRVAL1"]), float(h["CRVAL2"])]
    out.wcs.cdelt = [float(h.get("CDELT1", 1.0)), float(h.get("CDELT2", 1.0))]
    out.wcs.pc = [[float(h.get("PC1_1", 1.0)), float(h.get("PC1_2", 0.0))],
                  [float(h.get("PC2_1", 0.0)), float(h.get("PC2_2", 1.0))]]
    out.wcs.cunit = [str(h.get("CUNIT1", "deg")), str(h.get("CUNIT2", "deg"))]
    if "LONPOLE" in h:
        out.wcs.lonpole = float(h["LONPOLE"])
    if "LATPOLE" in h:
        out.wcs.latpole = float(h["LATPOLE"])
    if "RADESYS" in h:
        out.wcs.radesys = str(h["RADESYS"])
    out.sip = Sip(a, b, ap, bp, crpix)
    if "NAXIS1" in h and "NAXIS2" in h:
        out.pixel_shape = (int(h["NAXIS1"]), int(h["NAXIS2"]))
    out.wcs.set()
    return out


def wcs_from_cards(cards) -> WCS | None:
    """Fast TAN-SIP WCS from reduced header cards, or None when the general path must be used.

    The first few WCSs built this way in a process are compared with the
    general path; any disagreement switches the shortcut off for good.
    """
    if not _fast_wcs["enabled"]:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fast = _build_fast_wcs(cards)
    except Exception:  # noqa: BLE001 - the general path decides
        return None
    if fast is None or _fast_wcs["checked"] >= 6:
        return fast
    _fast_wcs["checked"] += 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        slow = WCS(header_from_cards(cards))
        xs = np.array([10.0, 1020.0, 2030.0, 300.0, 1700.0])
        ys = np.array([15.0, 1020.0, 2025.0, 1800.0, 250.0])
        ra, dec = slow.all_pix2world(xs, ys, 0)
        same_sky = np.allclose(fast.all_pix2world(xs, ys, 0), (ra, dec), rtol=0, atol=1e-10)
        same_pix = np.allclose(fast.all_world2pix(ra, dec, 0), slow.all_world2pix(ra, dec, 0), rtol=0, atol=1e-7)
    if not (same_sky and same_pix):
        _fast_wcs["enabled"] = False
        return None
    return fast


def world_to_pixel(wcs: WCS, ra, dec):
    """Sky -> 0-based pixel coordinates, tolerant of SIP non-convergence."""
    ra = np.atleast_1d(np.asarray(ra, dtype=float))
    dec = np.atleast_1d(np.asarray(dec, dtype=float))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            x, y = wcs.all_world2pix(ra, dec, 0, quiet=True, maxiter=30,
                                     tolerance=1e-5, detect_divergence=True)
        except NoConvergence as exc:
            sol = np.asarray(exc.best_solution)
            x, y = sol[:, 0], sol[:, 1]
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float)


def shifted_wcs(wcs: WCS, x0: int, y0: int) -> WCS:
    """WCS for a cutout whose pixel (0, 0) is full-frame pixel (x0, y0)."""
    out = wcs.deepcopy()
    shift = np.array([x0, y0], dtype=float)
    out.wcs.crpix = out.wcs.crpix - shift
    if out.sip is not None:
        out.sip = Sip(out.sip.a, out.sip.b, out.sip.ap, out.sip.bp, out.sip.crpix - shift)
    return out


def bounding_box(wcs: WCS, ra, dec, pad: int, nx: int | None = None, ny: int | None = None):
    """Pixel window ``(x0, x1, y0, y1)`` (exclusive ends) containing the sky points.

    The box is padded by *pad* pixels and clipped to the detector; None when
    the points fall entirely outside it.
    """
    nx = nx or config.DETECTOR_SIZE
    ny = ny or config.DETECTOR_SIZE
    xs, ys = world_to_pixel(wcs, ra, dec)
    if not (np.all(np.isfinite(xs)) and np.all(np.isfinite(ys))):
        return None
    return clip_box(math.floor(xs.min()) - pad, math.ceil(xs.max()) + pad + 1,
                    math.floor(ys.min()) - pad, math.ceil(ys.max()) + pad + 1, nx, ny)


def clip_box(x0: int, x1: int, y0: int, y1: int, nx: int, ny: int):
    x0, x1 = max(0, int(x0)), min(nx, int(x1))
    y0, y1 = max(0, int(y0)), min(ny, int(y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, x1, y0, y1


def box_covers(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] >= inner[1] and outer[2] <= inner[2] and outer[3] >= inner[3]


def crop(arr: np.ndarray, win, need) -> np.ndarray:
    """Slice the window *need* out of an array that holds the window *win*."""
    return arr[need[2] - win[2]: need[3] - win[2], need[0] - win[0]: need[1] - win[0]]


# --------------------------------------------------------------------------
# Cutout
# --------------------------------------------------------------------------
@dataclass
class Cutout:
    exposure: Exposure
    x0: int                      # full-frame pixel of the cutout's pixel (0, 0)
    y0: int
    image: np.ndarray            # float32, MJy/sr
    wcs: WCS                     # celestial WCS of the cutout (SIP included)
    flags: np.ndarray | None = None   # uint32 bitmask, same shape as image
    wave: WaveLookup | None = None

    @property
    def shape(self) -> tuple[int, int]:
        return self.image.shape

    def wavelength(self, x, y):
        """Wavelength/bandwidth (um) at 0-based cutout pixel coordinates."""
        if self.wave is None:
            raise ValueError("wavelength table not attached")
        return self.wave(np.asarray(x, dtype=float) + self.x0, np.asarray(y, dtype=float) + self.y0)

    def sky_to_pixel(self, ra, dec):
        return world_to_pixel(self.wcs, ra, dec)
