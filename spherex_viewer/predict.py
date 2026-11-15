"""Predict where a sky position lands on a detector before reading the file.

IRSA's image search returns, for every exposure, the sky coordinates of the
four detector corners and of the detector centre.  Those five points pin down
the pointing and roll of the exposure; the optical distortion (SIP
polynomial) is a property of each detector and barely changes from exposure
to exposure.  Combining the two predicts the pixel position of any sky point
to a small fraction of a pixel, which lets the viewer ask for a file's header
*and* the cutout rows in a single HTTP request.  The prediction is only a
speed-up: the real WCS from the header always decides what is used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from astropy.io import fits

from . import config


# --------------------------------------------------------------------------
# Gnomonic (TAN) projection, degrees in and out
# --------------------------------------------------------------------------
def gnomonic(ra, dec, ra0: float, dec0: float):
    a = np.radians(np.asarray(ra, dtype=float) - ra0)
    d = np.radians(np.asarray(dec, dtype=float))
    d0 = math.radians(dec0)
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a)
    xi = np.cos(d) * np.sin(a) / cosc
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a)) / cosc
    return np.degrees(xi), np.degrees(eta)


def inverse_gnomonic(xi: float, eta: float, ra0: float, dec0: float) -> tuple[float, float]:
    x, y = math.radians(xi), math.radians(eta)
    d0 = math.radians(dec0)
    rho = math.hypot(x, y)
    if rho == 0.0:
        return ra0, dec0
    c = math.atan(rho)
    dec = math.asin(math.cos(c) * math.sin(d0) + y * math.sin(c) * math.cos(d0) / rho)
    ra = ra0 + math.degrees(math.atan2(x * math.sin(c),
                                       rho * math.cos(d0) * math.cos(c) - y * math.sin(d0) * math.sin(c)))
    return ra % 360.0, math.degrees(dec)


# --------------------------------------------------------------------------
# SIP distortion template (one per detector)
# --------------------------------------------------------------------------
class SipTemplate:
    def __init__(self, a, b, crpix):
        self.a = np.asarray(a, dtype=float)
        self.b = np.asarray(b, dtype=float)
        self.crpix = (float(crpix[0]), float(crpix[1]))

    @classmethod
    def from_header(cls, header) -> "SipTemplate | None":
        if isinstance(header, list):
            header = dict((k, v) for k, v in header)
        if "A_ORDER" not in header or "B_ORDER" not in header:
            return None

        def matrix(prefix: str) -> np.ndarray:
            order = int(header[f"{prefix}_ORDER"])
            m = np.zeros((order + 1, order + 1))
            for p in range(order + 1):
                for q in range(order + 1 - p):
                    m[p, q] = float(header.get(f"{prefix}_{p}_{q}", 0.0))
            return m

        return cls(matrix("A"), matrix("B"), (header["CRPIX1"], header["CRPIX2"]))

    @staticmethod
    def _poly(m: np.ndarray, u, v):
        out = np.zeros_like(np.asarray(u, dtype=float))
        for p in range(m.shape[0]):
            for q in range(m.shape[1] - p):
                if m[p, q]:
                    out = out + m[p, q] * u ** p * v ** q
        return out

    def pix2foc(self, x, y):
        """0-based pixel -> distortion-corrected offsets from CRPIX (pixels)."""
        u = np.asarray(x, dtype=float) + 1.0 - self.crpix[0]
        v = np.asarray(y, dtype=float) + 1.0 - self.crpix[1]
        return u + self._poly(self.a, u, v), v + self._poly(self.b, u, v)

    def foc2pix(self, fu, fv):
        """Inverse of :meth:`pix2foc` by fixed-point iteration."""
        fu = np.asarray(fu, dtype=float)
        fv = np.asarray(fv, dtype=float)
        u, v = fu.copy(), fv.copy()
        for _ in range(6):
            u, v = fu - self._poly(self.a, u, v), fv - self._poly(self.b, u, v)
        return u + self.crpix[0] - 1.0, v + self.crpix[1] - 1.0

    def to_json(self) -> dict:
        return {"a": self.a.tolist(), "b": self.b.tolist(), "crpix": list(self.crpix)}

    @classmethod
    def from_json(cls, data: dict) -> "SipTemplate":
        return cls(data["a"], data["b"], data["crpix"])


# --------------------------------------------------------------------------
# Pointing fit
# --------------------------------------------------------------------------
@dataclass
class Pointing:
    ra0: float
    dec0: float
    matrix: np.ndarray        # degrees of tangent plane per focal pixel (2x2)
    inverse: np.ndarray
    residual_px: float        # worst mismatch of the five reference points


def detector_reference_points(n: int | None = None):
    """0-based pixel coordinates of the footprint corners and the centre.

    Verified against the archive: the SIA polygon lists the outer pixel edges
    in the order (x, y) = (-0.5, -0.5), (N-0.5, -0.5), (N-0.5, N-0.5),
    (-0.5, N-0.5), and (s_ra, s_dec) is the geometric centre.
    """
    n = n or config.DETECTOR_SIZE
    lo, hi, mid = -0.5, n - 0.5, (n - 1) / 2.0
    return np.array([lo, hi, hi, lo, mid]), np.array([lo, lo, hi, hi, mid])


def fit_pointing(template: SipTemplate, footprint, centre) -> Pointing | None:
    """Solve for the tangent point and CD matrix that reproduce the footprint."""
    if template is None or footprint is None or len(footprint) < 4 or centre is None:
        return None
    px, py = detector_reference_points()
    sky = np.array([list(p) for p in footprint[:4]] + [list(centre)], dtype=float)
    if not np.all(np.isfinite(sky)):
        return None
    fu, fv = template.pix2foc(px, py)
    design = np.column_stack([fu, fv, np.ones_like(fu)])
    ra0, dec0 = float(centre[0]), float(centre[1])
    matrix = None
    for _ in range(6):
        xi, eta = gnomonic(sky[:, 0], sky[:, 1], ra0, dec0)
        cx, *_ = np.linalg.lstsq(design, xi, rcond=None)
        cy, *_ = np.linalg.lstsq(design, eta, rcond=None)
        matrix = np.array([[cx[0], cx[1]], [cy[0], cy[1]]])
        offset = math.hypot(cx[2], cy[2])
        ra0, dec0 = inverse_gnomonic(cx[2], cy[2], ra0, dec0)
        if offset < 1e-10:
            break
    if matrix is None or abs(np.linalg.det(matrix)) < 1e-12:
        return None
    pointing = Pointing(ra0, dec0, matrix, np.linalg.inv(matrix), 0.0)
    x, y = predict_pixels(template, pointing, sky[:, 0], sky[:, 1])
    pointing.residual_px = float(np.max(np.hypot(x - px, y - py)))
    if not math.isfinite(pointing.residual_px) or pointing.residual_px > 1.0:
        return None
    return pointing


def predict_pixels(template: SipTemplate, pointing: Pointing, ra, dec):
    """Predicted 0-based pixel coordinates of sky positions."""
    xi, eta = gnomonic(ra, dec, pointing.ra0, pointing.dec0)
    fu = pointing.inverse[0, 0] * xi + pointing.inverse[0, 1] * eta
    fv = pointing.inverse[1, 0] * xi + pointing.inverse[1, 1] * eta
    return template.foc2pix(fu, fv)
