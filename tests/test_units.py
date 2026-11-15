"""Offline unit tests for the pure parts of the pipeline (no network)."""
from __future__ import annotations

import io
import math

import numpy as np
import pytest
from astropy.io import fits

from spherex_viewer import detect, fitsrange, photometry, processing, releases, resolve
from spherex_viewer.cutouts import (Cutout, WaveLookup, bounding_box, flag_definitions, make_wcs,
                                    mask_from_names, reduce_header, shifted_wcs, world_to_pixel)
from spherex_viewer.predict import SipTemplate, fit_pointing, predict_pixels
from spherex_viewer.sia import Exposure, _VERSION_RE


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _exp(obs="2026W12_1A_0311_3", det=1, mjd=61000.0, collection="spherex_dr1", version="v27") -> Exposure:
    return Exposure(obs_id=obs, detector=det, collection=collection, url=f"https://x/{obs}D{det}.fits",
                    mjd_start=mjd, mjd_end=mjd + 0.0013, exptime=113.6, em_min_um=0.75, em_max_um=1.1,
                    version=version, ra_center=10.0, dec_center=20.0)


def _sip_header(n=2040, ra=150.0, dec=20.0, roll_deg=0.0) -> fits.Header:
    """A SPHEREx-like celestial WCS: 6.15 arcsec pixels, cubic SIP distortion."""
    scale = 6.15 / 3600.0
    c, s = math.cos(math.radians(roll_deg)), math.sin(math.radians(roll_deg))
    h = fits.Header()
    h["NAXIS"], h["NAXIS1"], h["NAXIS2"], h["BITPIX"] = 2, n, n, -32
    h["WCSAXES"] = 2
    h["CTYPE1"], h["CTYPE2"] = "RA---TAN-SIP", "DEC--TAN-SIP"
    h["CRPIX1"], h["CRPIX2"] = n / 2 + 0.5, n / 2 + 0.5
    h["CRVAL1"], h["CRVAL2"] = ra, dec
    h["CUNIT1"], h["CUNIT2"] = "deg", "deg"
    h["CDELT1"], h["CDELT2"] = 1.0, 1.0
    h["PC1_1"], h["PC1_2"] = -scale * c, scale * s
    h["PC2_1"], h["PC2_2"] = scale * s, scale * c
    h["A_ORDER"], h["B_ORDER"] = 3, 3
    for key, value in {"A_0_0": 0.05, "A_1_1": 4.0e-6, "A_2_0": -1.6e-6, "A_0_2": -1.4e-6, "A_3_0": 5e-10,
                       "B_0_0": -0.9, "B_2_0": 6.6e-6, "B_1_1": -7e-7, "B_0_2": 2.0e-6, "B_0_3": 4e-10}.items():
        h[key] = value
    h["MJD-AVG"] = 61000.0
    h["DETECTOR"] = 2
    return h


def _footprint(header: fits.Header):
    """What IRSA's image search reports: the four outer corners and the centre."""
    wcs = make_wcs(header)
    n = header["NAXIS1"]
    xs = np.array([-0.5, n - 0.5, n - 0.5, -0.5, (n - 1) / 2])
    ys = np.array([-0.5, -0.5, n - 0.5, n - 0.5, (n - 1) / 2])
    ra, dec = wcs.all_pix2world(xs, ys, 0)
    return [[float(a), float(b)] for a, b in zip(ra[:4], dec[:4])], (float(ra[4]), float(dec[4]))


def _gaussian(n, x, y, flux, sigma=1.0):
    yy, xx = np.mgrid[0:n, 0:n]
    return flux / (2 * math.pi * sigma ** 2) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))


# --------------------------------------------------------------------------
# byte ranges / multipart
# --------------------------------------------------------------------------
def test_merge_ranges_merges_touching_and_overlapping():
    assert fitsrange.merge_ranges([(10, 20), (21, 30), (0, 5), (15, 18), (40, 41)]) == [(0, 5), (10, 30), (40, 41)]


def test_parse_multipart_and_slice():
    body = (b"\r\n--abc123\r\nContent-Type: application/fits\r\nContent-Range: bytes 0-3/100\r\n\r\nABCD\r\n"
            b"--abc123\r\nContent-Type: application/fits\r\nContent-Range: bytes 10-12/100\r\n\r\nXYZ\r\n--abc123--\r\n")
    parts = fitsrange.parse_multipart(body, "abc123")
    assert parts == [(0, 3, b"ABCD"), (10, 12, b"XYZ")]
    assert fitsrange.slice_parts(parts, [(1, 2), (10, 10), (11, 12)]) == [b"BC", b"X", b"YZ"]
    with pytest.raises(fitsrange.RangeError):
        fitsrange.slice_parts(parts, [(4, 5)])


def test_slice_parts_handles_merged_parts():
    parts = [(0, 99, bytes(range(100)))]
    out = fitsrange.slice_parts(parts, [(0, 9), (50, 59), (90, 99)])
    assert out[0] == bytes(range(10)) and out[1] == bytes(range(50, 60)) and out[2] == bytes(range(90, 100))


# --------------------------------------------------------------------------
# FITS structure
# --------------------------------------------------------------------------
def test_header_walk_and_row_ranges_on_a_real_fits_layout():
    data = np.arange(20 * 30, dtype=">f4").reshape(20, 30)
    flags = np.zeros((20, 30), dtype=">i4")
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(data, name="IMAGE"), fits.ImageHDU(flags, name="FLAGS")]).writeto(buf)
    raw = buf.getvalue()
    primary = fitsrange.find_header_end(raw, 0)
    image_bytes = fitsrange.find_header_end(raw, primary)
    header = fitsrange.parse_header(raw[primary: primary + image_bytes])
    assert header["EXTNAME"] == "IMAGE" and fitsrange.data_bytes(header) == 20 * 30 * 4
    offset = primary + image_bytes
    ranges = fitsrange.row_ranges(offset, 30, 4, 5, 12, 3, 6)          # window x 5..11, y 3..5
    blobs = [raw[s: e + 1] for s, e in ranges]
    window = fitsrange.rows_to_array(blobs, np.dtype(">f4"), 7).astype(np.float32)
    assert np.array_equal(window, data[3:6, 5:12].astype(np.float32))
    flags_offset = offset + fitsrange.padded(fitsrange.data_bytes(header))
    assert fitsrange.parse_header(raw[flags_offset: flags_offset + 2880])["EXTNAME"] == "FLAGS"


def test_apply_scaling_unsigned_convention():
    out = fitsrange.apply_scaling(np.array([[-32768, 0, 32767]], dtype=">i2"), fits.Header({"BZERO": 32768, "BSCALE": 1}))
    assert out.dtype == np.uint16 and out.tolist() == [[0, 32768, 65535]]


def test_compressed_flags_window_matches_astropy():
    """Tile-compressed FLAGS (as in DR1/QR3 files) read through tile plans equals a normal read."""
    rng = np.random.default_rng(1)
    flags = (rng.integers(0, 4, size=(40, 64)) * 2 ** rng.integers(0, 22, size=(40, 64))).astype(np.int32)
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(flags, name="FLAGS", compression_type="RICE_1",
                                                       tile_shape=(1, 64))]).writeto(buf)
    raw = buf.getvalue()
    primary = fitsrange.find_header_end(raw, 0)
    hbytes = fitsrange.find_header_end(raw, primary)
    header = fitsrange.parse_header(raw[primary: primary + hbytes])
    assert fitsrange.is_compressed(header) and fitsrange.image_shape(header) == (40, 64)
    x0, x1, y0, y1 = 10, 30, 5, 17
    plan = fitsrange.tile_plan(header, primary + hbytes, x0, x1, y0, y1)
    descriptors = [raw[s: e + 1] for s, e in plan["desc_ranges"]]
    tiles = fitsrange.tile_locations(plan, descriptors)
    heap_parts = [(s, e, raw[s: e + 1]) for (s, e), _ in tiles]
    window = fitsrange.assemble_tiles(plan, tiles, heap_parts, x0, x1, y0, y1)
    assert np.array_equal(window, flags[y0:y1, x0:x1])


# --------------------------------------------------------------------------
# flags, wavelengths, WCS
# --------------------------------------------------------------------------
def test_flag_definitions_and_mask():
    header = fits.Header()
    header["HIERARCH MP_TRANSIENT"] = 0
    header["HIERARCH MP_NONFUNC"] = 6
    header["HIERARCH MP_SOURCE"] = 21
    defs = flag_definitions(header)
    assert defs == {"TRANSIENT": 0, "NONFUNC": 6, "SOURCE": 21}
    assert mask_from_names(defs, ["nonfunc", "SOURCE", "UNKNOWN"]) == (1 << 6) | (1 << 21)


def test_wave_lookup_matches_control_points_and_interpolates():
    grid = np.zeros((3, 3, 2))
    for j in range(3):
        for i in range(3):
            grid[j, i] = (1.0 + 0.5 * j + 0.01 * i, 0.02)      # wavelength grows with y
    lookup = WaveLookup([1, 1021, 2040], [1, 1021, 2040], grid.ravel())
    wl, bw = lookup(0, 0)                                      # 0-based (0,0) is control point (1,1)
    assert math.isclose(float(wl), 1.0, abs_tol=1e-9) and math.isclose(float(bw), 0.02)
    assert math.isclose(float(lookup(0, 510)[0]), 1.25, abs_tol=1e-6)
    again = WaveLookup.from_json(lookup.to_json())
    assert np.allclose(again(np.array([3.0, 900.0]), np.array([10.0, 2000.0]))[0],
                       lookup(np.array([3.0, 900.0]), np.array([10.0, 2000.0]))[0])


def test_reduced_header_keeps_the_wcs_exactly():
    header = _sip_header(roll_deg=17.0)
    header["HISTORY"] = "noise"
    header["CRPIX1A"] = 1.0                                     # alternate WCS keys must not leak in
    cards = reduce_header(header)
    assert "CRPIX1A" not in dict(cards) and "A_2_0" in dict(cards)
    full, lean = make_wcs(header), make_wcs(cards)
    ra, dec = full.all_pix2world([5.0, 1000.0, 2030.0], [9.0, 1200.0, 2000.0], 0)
    assert np.allclose(world_to_pixel(lean, ra, dec), world_to_pixel(full, ra, dec), atol=1e-6)


def test_shifted_wcs_and_bounding_box():
    wcs = make_wcs(_sip_header())
    ra, dec = wcs.all_pix2world([700.3], [900.6], 0)
    x, y = world_to_pixel(shifted_wcs(wcs, 650, 880), ra, dec)
    assert math.isclose(float(x[0]), 50.3, abs_tol=1e-3) and math.isclose(float(y[0]), 20.6, abs_tol=1e-3)
    box = bounding_box(wcs, ra, dec, pad=10)
    assert box == (690, 712, 890, 912)
    far_ra, far_dec = wcs.all_pix2world([-400.0], [900.0], 0)
    assert bounding_box(wcs, far_ra, far_dec, pad=10) is None


# --------------------------------------------------------------------------
# predicting the detector position from the search footprint
# --------------------------------------------------------------------------
def test_footprint_prediction_is_subpixel_for_any_roll():
    template = SipTemplate.from_header(_sip_header())
    again = SipTemplate.from_json(template.to_json())
    rng = np.random.default_rng(5)
    for roll in (0.0, 33.0, 180.0, 271.5):
        header = _sip_header(ra=210.0, dec=-35.0, roll_deg=roll)
        header["A_0_0"], header["B_0_0"] = 0.4, -2.1            # exposure-specific constant terms
        corners, centre = _footprint(header)
        pointing = fit_pointing(again, corners, centre)
        assert pointing is not None and pointing.residual_px < 0.05
        truth = make_wcs(header)
        px, py = rng.uniform(0, 2039, 30), rng.uniform(0, 2039, 30)
        ra, dec = truth.all_pix2world(px, py, 0)
        x, y = predict_pixels(again, pointing, ra, dec)
        assert np.max(np.hypot(x - px, y - py)) < 0.05


def test_fit_pointing_rejects_a_bad_footprint():
    template = SipTemplate.from_header(_sip_header())
    corners, centre = _footprint(_sip_header())
    assert fit_pointing(template, corners[:3], centre) is None
    scrambled = [corners[0], corners[2], corners[1], corners[3]]
    assert fit_pointing(template, scrambled, centre) is None


# --------------------------------------------------------------------------
# releases
# --------------------------------------------------------------------------
def test_release_order_prefers_data_releases_then_newest():
    names = ["spherex_qr3_deep", "spherex_dr1_cube", "spherex_qr3", "spherex_dr1", "spherex_dr1_deep",
             "spherex_dr1_cal", "spherex_qr4", "spherex_dr2", "other"]
    assert releases.order(names) == ["spherex_dr2", "spherex_dr1", "spherex_dr1_deep",
                                     "spherex_qr4", "spherex_qr3", "spherex_qr3_deep"]


def test_version_and_layout_key_from_file_names():
    assert _VERSION_RE.search("level2_2026W12_1A_0311_3D2_spx_l2b-v24-2026-082.fits").group(1) == "24"
    assert _VERSION_RE.search("level2_2026W38_1A_0162_1D6_spx_l2b_retry-v27-2026-262.fits").group(1) == "27"
    assert _exp(collection="spherex_dr1_deep").hint_key == _exp(collection="spherex_dr1").hint_key == "spherex_dr1/v27"


# --------------------------------------------------------------------------
# epochs and selection
# --------------------------------------------------------------------------
def test_group_epochs_splits_on_long_gaps():
    exps = [_exp("a", 1, 60900.0), _exp("b", 2, 60910.0), _exp("c", 1, 61100.0), _exp("d", 3, 61105.0), _exp("e", 1, 61290.0)]
    epochs = processing.group_epochs(exps)
    assert [len(e.exposures) for e in epochs] == [2, 2, 1]
    assert [e.index for e in epochs] == [1, 2, 3] and all(e.kind == "visit" for e in epochs)


def test_continuous_coverage_is_cut_into_calendar_bins():
    exps = [_exp(f"o{i}", 1 + i % 6, 60900.0 + i * 0.5) for i in range(400)]      # 200 days without a gap
    epochs = processing.group_epochs(exps)
    assert 6 <= len(epochs) <= 8 and all(e.kind == "bin" for e in epochs)
    assert sum(len(e.exposures) for e in epochs) == 400
    assert all(e.mjd_end - e.mjd_start <= 60.5 for e in epochs)
    assert processing.group_epochs(exps + [_exp("late", 1, 61001.0)])[0].exposures[0].obs_id == "o0"   # bins are stable


def test_select_exposures_samples_every_detector_of_every_epoch():
    exps = [_exp(f"o{i}", 1 + i % 6, 60900.0 + i * 0.05) for i in range(3000)]
    epochs = processing.group_epochs(exps)
    count = processing.select_exposures(epochs, 300)
    assert count <= 330 and count == sum(len(e.used) for e in epochs)
    for epoch in epochs:
        assert {e.detector for e in epoch.used} == {e.detector for e in epoch.exposures}
    assert processing.select_exposures(epochs, 5000) == 3000 and epochs[0].selected is None


# --------------------------------------------------------------------------
# target grid, reprojection, stacking
# --------------------------------------------------------------------------
def test_target_grid_orientation_and_pixel_mapping():
    npix, scale = processing.plan_grid(8.0)
    assert npix % 2 == 1 and math.isclose(scale, 6.15)
    assert processing.plan_grid(60.0)[0] <= 481 and processing.plan_grid(60.0)[1] > 6.15
    grid = processing.TargetGrid(133.76, -7.24, npix, scale)
    x, y = grid.wcs.wcs_world2pix(133.76, -7.24, 0)
    assert math.isclose(float(x), npix // 2, abs_tol=1e-6) and math.isclose(float(y), npix // 2, abs_tol=1e-6)
    assert grid.wcs.wcs_world2pix(133.76, -7.24 + 10 * scale / 3600, 0)[1] > y        # north is up
    assert grid.wcs.wcs_world2pix(133.76 + 0.01, -7.24, 0)[0] < x                     # east is left
    # the coarse-lattice mapping agrees with a brute-force one
    wcs = make_wcs(_sip_header(ra=133.76, dec=-7.24, roll_deg=40.0))
    sx, sy = grid.source_coords(wcs)
    yy, xx = np.mgrid[0:npix, 0:npix]
    ra, dec = grid.wcs.wcs_pix2world(xx.ravel(), yy.ravel(), 0)
    bx, by = world_to_pixel(wcs, ra, dec)
    assert np.max(np.abs(sx.ravel() - bx)) < 2e-3 and np.max(np.abs(sy.ravel() - by)) < 2e-3


def test_reproject_puts_a_star_where_the_sky_says():
    header = _sip_header(ra=133.76, dec=-7.24, roll_deg=25.0)
    wcs = make_wcs(header)
    star_ra, star_dec = 133.7645, -7.2362
    fx, fy = world_to_pixel(wcs, [star_ra], [star_dec])
    x0, y0 = int(fx[0]) - 60, int(fy[0]) - 60
    image = (_gaussian(121, fx[0] - x0, fy[0] - y0, 500.0) + 3.0).astype(np.float32)   # star on a sky of 3
    image[10, 10] = np.nan
    cutout = Cutout(_exp(det=2), x0, y0, image, shifted_wcs(wcs, x0, y0))
    grid = processing.TargetGrid(133.76, -7.24, 41, 6.15)
    frame = processing.reproject_cutout(cutout, grid)
    assert frame is not None and abs(frame.background - 3.0) < 0.05
    gx, gy = grid.wcs.wcs_world2pix(star_ra, star_dec, 0)
    data = np.nan_to_num(frame.data)
    yy, xx = np.mgrid[0:41, 0:41]
    cx, cy = (data * xx).sum() / data.sum(), (data * yy).sum() / data.sum()
    assert abs(cx - gx) < 0.1 and abs(cy - gy) < 0.1
    assert abs(data.sum() - 500.0) / 500.0 < 0.05                        # flux is conserved


def test_stack_frames_rejects_outliers_and_counts_exposures():
    rng = np.random.default_rng(0)
    frames = []
    for k in range(8):
        data = rng.normal(0, 0.1, size=(9, 9)).astype(np.float32)
        if k == 0:
            data[4, 4] = 50.0                                       # a cosmic ray in one exposure
        frames.append(processing.ReprojectedFrame(_exp(f"o{k}", 1, 60900 + k), data, None, 0.0, 0.1, 4.0, 4.0))
    out = processing.stack_frames(frames)
    assert abs(out.data[4, 4]) < 1.0 and out.nexp[4, 4] == 7 and out.nexp[0, 0] == 8 and out.flags is None
    with_flags = [processing.ReprojectedFrame(f.exposure, f.data, np.full((9, 9), 2 if i < 4 else 0, dtype=np.uint32),
                                              0.0, 0.1, 4.0, 4.0) for i, f in enumerate(frames)]
    with_flags[0].flags[0, 0] |= np.uint32(64)                      # one exposure only: not a majority
    stacked = processing.stack_frames(with_flags)
    assert stacked.flags[2, 2] == 2 and stacked.flags[0, 0] == 2


# --------------------------------------------------------------------------
# photometry
# --------------------------------------------------------------------------
def test_aperture_weights_have_the_area_of_the_circle():
    weights = photometry.aperture_weights((21, 21), 10.3, 9.6, 2.0, sub=9)
    assert abs(weights.sum() - math.pi * 4.0) < 0.15


def test_measure_recovers_flux_and_wavelength():
    wcs = make_wcs(_sip_header())
    ra, dec = wcs.all_pix2world([1000.4], [800.7], 0)
    x0, y0 = 980, 780
    image = (_gaussian(41, 20.4, 20.7, 800.0, sigma=0.8) + 5.0).astype(np.float32)
    cutout = Cutout(_exp(det=2), x0, y0, image, shifted_wcs(wcs, x0, y0),
                    flags=np.zeros((41, 41), dtype=np.uint32),
                    wave=WaveLookup([1, 2040], [1, 2040], [1.0, 0.03, 1.0, 0.03, 2.0, 0.03, 2.0, 0.03]))
    point = photometry.measure(cutout, float(ra[0]), float(dec[0]), r_ap=3.0)
    assert abs(point["flux_mjy"] / photometry.MJYSR_TO_MJY - 800.0) / 800.0 < 0.02
    assert abs(point["background_mjysr"] - 5.0) < 1e-3 and point["flag_value"] == 0
    assert abs(point["wavelength_um"] - (1.0 + 800.7 / 2039.0)) < 2e-3
    assert photometry.measure(cutout, float(ra[0]) + 0.1, float(dec[0]), r_ap=3.0) is None      # off the cutout


# --------------------------------------------------------------------------
# change detection
# --------------------------------------------------------------------------
def _epochs_with(sources, n_epochs=3, npix=81, seed=3):
    """Synthetic epoch stacks: sources is a list of (x, y, flux, (vx, vy) px/yr, [relative flux per epoch])."""
    rng = np.random.default_rng(seed)
    years = [2025.6 + 0.5 * e for e in range(n_epochs)]
    epochs = []
    for e, year in enumerate(years):
        bands = {}
        for b in detect.BAND_IDS:
            img = rng.normal(0, 0.02, size=(npix, npix))
            for x, y, flux, (vx, vy), rel in sources:
                dt = year - years[0]
                img += _gaussian(npix, x + vx * dt, y + vy * dt, flux * (rel[e] if rel else 1.0), sigma=1.0)
            bands[b] = (img.astype(np.float32), np.full((npix, npix), 20, dtype=np.uint16))
        mean = np.mean([bands[b][0] for b in bands], axis=0).astype(np.float32)
        epochs.append({"index": e + 1, "year": year, "all": (mean, np.full((npix, npix), 120, dtype=np.uint16)), "bands": bands})
    return epochs


def test_analyse_finds_the_mover_and_the_variable_and_nothing_else():
    rng = np.random.default_rng(11)
    stars = [(float(x), float(y), float(f), (0.0, 0.0), None)
             for x, y, f in zip(rng.uniform(8, 72, 14), rng.uniform(8, 72, 14), rng.uniform(40, 400, 14))]
    stars = [s for i, s in enumerate(stars) if all(math.hypot(s[0] - t[0], s[1] - t[1]) > 9 for t in stars[:i])]
    stars = [s for s in stars if math.hypot(s[0] - 40, s[1] - 40) > 10 and math.hypot(s[0] - 20, s[1] - 60) > 10]
    mover = (40.0, 40.0, 300.0, (0.3, 1.6), None)                 # 1.63 px/yr = 10 arcsec/yr, mostly north
    variable = (20.0, 60.0, 200.0, (0.0, 0.0), [1.0, 1.6, 0.8])
    grid = processing.TargetGrid(150.0, 20.0, 81, 6.15)
    result = detect.analyse(_epochs_with(stars + [mover, variable]), grid)
    moving = [s for s in result["sources"] if s["moving"]]
    varying = [s for s in result["sources"] if s["variable"]]
    assert len(moving) == 1 and len(varying) == 1 and result["n_sources"] >= len(stars)
    m = moving[0]
    assert abs(m["pm"] - math.hypot(0.3, 1.6) * 6.15) < 0.6          # arcsec/yr
    assert m["pm_dec"] > 0 and m["pm_ra"] < 0                         # +x on the grid is west
    assert abs(varying[0]["x"] - 20.0) < 1.0 and abs(varying[0]["var_amp"] - 0.7) < 0.15
    assert result["sources"][0]["moving"] and result["sources"][1]["variable"]


def test_analyse_does_not_trust_blends():
    pair = [(40.0, 40.0, 60.0, (0.0, 0.0), None), (42.3, 41.0, 45.0, (0.0, 0.0), None)]
    stars = [(15.0, 15.0, 300.0, (0.0, 0.0), None), (65.0, 20.0, 250.0, (0.0, 0.0), None),
             (20.0, 65.0, 200.0, (0.0, 0.0), None), (62.0, 62.0, 350.0, (0.0, 0.0), None),
             (40.0, 12.0, 280.0, (0.0, 0.0), None)]
    grid = processing.TargetGrid(150.0, 20.0, 81, 6.15)
    result = detect.analyse(_epochs_with(stars + pair, seed=8), grid)
    assert result["n_moving"] == 0
    assert any(s["blended"] for s in result["sources"] if abs(s["x"] - 41) < 3 and abs(s["y"] - 40.5) < 3)


def test_analyse_needs_two_epochs():
    grid = processing.TargetGrid(150.0, 20.0, 81, 6.15)
    assert detect.analyse(_epochs_with([(40.0, 40.0, 300.0, (0.0, 0.0), None)], n_epochs=1), grid)["sources"] == []


# --------------------------------------------------------------------------
# resolve
# --------------------------------------------------------------------------
def test_parse_coordinates_formats():
    assert resolve.parse_coordinates("133.76 -7.24") == (133.76, -7.24)
    ra, dec = resolve.parse_coordinates("08h55m10.831s -7d14m42.53s")
    assert math.isclose(ra, 133.795129, abs_tol=1e-5) and math.isclose(dec, -7.245147, abs_tol=1e-5)
    ra2, dec2 = resolve.parse_coordinates("08:55:10.83 -07:14:42.5")
    assert math.isclose(ra2, ra, abs_tol=1e-4) and math.isclose(dec2, dec, abs_tol=1e-4)
    assert resolve.parse_coordinates("Barnard's Star") is None


def test_parse_coordinates_accepts_labelled_input():
    ra, dec = resolve.parse_coordinates("Right Ascension 08h55m10.831s, declination -7d14m42.53s")
    assert math.isclose(ra, 133.795129, abs_tol=1e-5) and math.isclose(dec, -7.245147, abs_tol=1e-5)
    assert resolve.parse_coordinates("RA: 133.795, Dec: -7.245") == (133.795, -7.245)
    assert resolve.parse_coordinates("ra=133.795 dec=−7.245") == (133.795, -7.245)
    assert resolve.parse_coordinates("133.795° -7.245°") == (133.795, -7.245)
    assert resolve.parse_coordinates("R Dra") is None and resolve.parse_coordinates("400 5") is None


def test_coordinates_on_the_past_track_of_a_test_target_say_so():
    # the often-quoted position of WISE 0855 is where WISE saw the brown dwarf in 2010
    target = resolve.resolve("08h55m10.831s -7d14m42.53s", epoch_to=2026.75)
    passing = target["passing"]
    assert passing["id"] == "wise0855" and passing["year"] == 2010 and passing["direction"] == "west"
    assert 120 < passing["moved_arcsec"] < 145                     # 8.15"/yr for sixteen years
    assert resolve.resolve("76.9565 30.4014", epoch_to=2026.75)["passing"] is None     # RW Aur stays put
    assert resolve.resolve("10.0 10.0", epoch_to=2026.75)["passing"] is None
    assert "passing" not in resolve.resolve("barnard", epoch_to=2026.75)


def test_preset_names_as_the_page_writes_them():
    # the search box shows "Barnard’s Star"; pressing Enter on it must find the test target, not ask Sesame
    assert resolve.find_preset("Barnard’s Star")["id"] == "barnard"
    assert resolve.find_preset("Barnard's Star")["id"] == "barnard"


def test_propagate_barnard_moves_north():
    ra, dec = resolve.propagate(269.4520770, 4.6933650, -801.551, 10362.394, 2000.0, 2026.0)
    assert dec > 4.6933650 + 0.07 and ra < 269.4520770
    assert math.isclose((dec - 4.6933650) * 3600, 10.362394 * 26, rel_tol=1e-3)


def test_presets_resolve_offline_and_viewing_epoch_is_quantised():
    target = resolve.resolve("WISE 0855", epoch_to=2026.0)
    assert target["source"] == "preset" and target["id"] == "wise0855"
    assert target["ra_now"] < target["ra"] - 0.05                  # 26 years of 8.1"/yr westward motion
    assert math.isclose(resolve.viewing_epoch() * 4, round(resolve.viewing_epoch() * 4))
    assert {p["id"] for p in resolve.PRESETS} == {"barnard", "epsindi-b", "wise0855", "rwaur", "rdra"}


def test_viewing_epoch_holds_for_a_whole_quarter(monkeypatch):
    for now in (2026.751, 2026.874, 2026.876, 2026.999):
        monkeypatch.setattr(resolve, "current_decimal_year", lambda now=now: now)
        assert resolve.viewing_epoch() == 2026.75
    monkeypatch.setattr(resolve, "current_decimal_year", lambda: 2027.001)
    assert resolve.viewing_epoch() == 2027.0


# --------------------------------------------------------------------------
# When IRSA is out of reach
# --------------------------------------------------------------------------
def _exposure(obs_id="2025W30_1A_0001_1", detector=1):
    return Exposure(obs_id=obs_id, detector=detector, collection="spherex_dr1", url="https://x/y.fits",
                    mjd_start=60900.0, mjd_end=60900.001, exptime=113.0, em_min_um=0.75, em_max_um=1.11,
                    version="v27", ra_center=10.0, dec_center=10.0)


def test_an_old_cached_search_is_returned_at_once_and_renewed_in_the_background(tmp_path, monkeypatch):
    import os
    import time
    from dataclasses import asdict

    from spherex_viewer import cache as cache_module, config, sia

    disk = cache_module.DiskCache(tmp_path)
    monkeypatch.setattr(sia, "default_cache", lambda: disk)
    asked = []
    monkeypatch.setattr(sia, "_refresh_later", lambda key, *args: asked.append(key))

    def unreachable(*args, **kwargs):
        raise RuntimeError("IRSA is down")

    monkeypatch.setattr(sia, "query_collection", unreachable)
    key = "spherex_dr1|10.00000|10.00000|0.1000"
    disk.put_json("sia", key, [asdict(_exposure())])
    old = time.time() - config.SIA_CACHE_TTL - 3600
    os.utime(disk.path("sia", key, ".json"), (old, old))

    found = sia.find_exposures(10.0, 10.0, 0.1, collections=["spherex_dr1"])
    # an answer stored for the field's own circle still counts; the shared, wider circle is asked for next
    assert [e.key for e in found] == ["2025W30_1A_0001_1/D1"]
    assert asked == [sia._cache_key(["spherex_dr1"], 10.0, 10.0, sia.SHARED_RADIUS_DEG)]
    with pytest.raises(RuntimeError):                          # never seen before: nothing to fall back on
        sia.find_exposures(20.0, 10.0, 0.1, collections=["spherex_dr1"])


def _square(ra, dec, half):
    """A footprint: a square of side 2 * half degrees around (ra, dec), closed as IRSA writes it."""
    c = math.cos(math.radians(dec))
    corners = [(ra - half / c, dec - half), (ra + half / c, dec - half), (ra + half / c, dec + half), (ra - half / c, dec + half)]
    return [list(p) for p in corners + corners[:1]]


def test_a_footprint_reaches_a_circle_when_it_covers_its_centre_or_passes_within_its_radius():
    from spherex_viewer import sia

    exp = _exposure()
    exp.footprint = _square(10.0, 10.0, 1.0)
    assert sia.touches(exp, 10.0, 10.0, 0.1)                   # centre inside
    assert sia.touches(exp, 10.0, 11.05, 0.1)                  # 0.05° above the top edge
    assert not sia.touches(exp, 10.0, 11.15, 0.1)              # 0.15° above it
    assert not sia.touches(exp, 190.0, -10.0, 0.1)             # the other side of the sky
    assert sia.touches(_exposure(), 50.0, 50.0, 0.1)           # no footprint: kept
    exp.footprint = _square(0.2, 0.0, 0.5)                     # across RA 0
    assert sia.touches(exp, 359.8, 0.0, 0.1) and sia.touches(exp, 359.65, 0.0, 0.1)


def test_a_narrower_field_is_cut_from_the_shared_search_without_asking_irsa(tmp_path, monkeypatch):
    from dataclasses import asdict

    from spherex_viewer import cache as cache_module, sia

    disk = cache_module.DiskCache(tmp_path)
    monkeypatch.setattr(sia, "default_cache", lambda: disk)

    def unreachable(*args, **kwargs):
        raise AssertionError("IRSA must not be asked")

    monkeypatch.setattr(sia, "query_collection", unreachable)
    near, edge = _exposure("2025W30_1A_0001_1"), _exposure("2025W30_1A_0002_1")
    near.footprint = _square(10.0, 10.0, 1.0)
    edge.footprint = _square(10.0, 8.85, 1.0)                  # top edge 0.15° below the centre
    disk.put_json("sia", sia._cache_key(["spherex_dr1"], 10.0, 10.0, sia.SHARED_RADIUS_DEG), [asdict(near), asdict(edge)])

    assert len(sia.find_exposures(10.0, 10.0, 0.18, collections=["spherex_dr1"])) == 2
    assert [e.key for e in sia.find_exposures(10.0, 10.0, 0.1, collections=["spherex_dr1"])] == [near.key]


def test_the_same_search_asked_twice_at_once_goes_to_irsa_once(tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from spherex_viewer import cache as cache_module, sia

    disk = cache_module.DiskCache(tmp_path)
    monkeypatch.setattr(sia, "default_cache", lambda: disk)
    monkeypatch.setattr(sia.snapshots, "search", lambda *args: None)
    calls, lock = [], threading.Lock()

    def slow(collection, ra, dec, radius, timeout=None):
        with lock:
            calls.append(collection)
        time.sleep(0.3)
        return [_exposure()]

    monkeypatch.setattr(sia, "query_collection", slow)
    with ThreadPoolExecutor(3) as pool:
        answers = list(pool.map(lambda r: sia.find_exposures(30.0, 5.0, r, collections=["spherex_dr1"]), [0.1, 0.1, 0.15]))
    assert calls == ["spherex_dr1"] and all(len(a) == 1 for a in answers)


def test_snapshots_cover_the_five_test_targets_and_nothing_else(tmp_path, monkeypatch):
    from spherex_viewer import cache as cache_module, pipeline, sia, snapshots

    monkeypatch.setattr(sia, "default_cache", lambda: cache_module.DiskCache(tmp_path))    # nothing searched yet
    monkeypatch.setattr(sia, "_refresh_later", lambda *args: None)                         # and nothing asked of IRSA

    for preset in resolve.PRESETS:
        target = resolve.resolve(preset["id"], epoch_to=2026.75)
        params = pipeline.normalise_frame_params({"ra": target["ra_now"], "dec": target["dec_now"], "fov": preset["fov"]})
        saved = snapshots.find("frames", params)
        assert saved is not None, preset["id"]
        assert saved["snapshot"]["saved"] and not saved["warnings"]
        assert len(saved["frames"]) >= 7 and saved["n_errors"] == 0
        assert snapshots.find("frames", dict(params, flags=True)) is None        # flags are always read live
        nudged = dict(params, dec=params["dec"] + 3.0 / 3600.0)      # the next quarter's centre is still served
        assert snapshots.find("frames", nudged) is not None
        # the image search goes with them, so no view of a test target waits for IRSA's search
        wide = max(pipeline.search_radius_deg(preset["fov"]), sia.SHARED_RADIUS_DEG)
        rows = snapshots.search(params["ra"], params["dec"], wide)
        assert rows, preset["id"]
        assert len(sia.cached_exposures(params["ra"], params["dec"], pipeline.search_radius_deg(preset["fov"])) or []) \
            >= saved["n_selected"]
        assert snapshots.find("frames", dict(params, dec=params["dec"] + 60.0 / 3600.0)) is None
        assert snapshots.find("frames", dict(params, fov=params["fov"] + 2.0)) is None
        field = saved["target"]
        spectrum = snapshots.find("spectrum", pipeline.normalise_spectrum_params({
            "ra": target["ra"], "dec": target["dec"], "pmra": target["pmra"], "pmdec": target["pmdec"],
            "epoch": 2000.0, "r_ap": 2.0, "field": {"ra": field["ra"], "dec": field["dec"], "fov": field["fov_arcmin"]}}))
        assert spectrum is not None and spectrum["points"], preset["id"]


def test_live_status_follows_the_background_reading_of_a_field(monkeypatch):
    from spherex_viewer import pipeline

    monkeypatch.setattr(pipeline, "cached_exposures", lambda *args: None)
    p = pipeline.normalise_frame_params({"ra": 123.4, "dec": -5.6, "fov": 8})
    key = pipeline._field_key(p)
    assert pipeline.live_status(p)["state"] == "idle"
    with pipeline._warm_lock:
        pipeline._warming.add(key)
        pipeline._warm_progress[key] = (100, 400, "read 100/400 exposures")
    try:
        st = pipeline.live_status(dict(p, max_exposures=None))
        assert st["state"] == "reading" and st["progress"] == 0.25 and st["message"].startswith("read 100/400")
    finally:
        with pipeline._warm_lock:
            pipeline._warming.discard(key)
            pipeline._warm_progress.pop(key, None)


def test_frames_fall_back_to_the_snapshot_when_the_archive_fails(tmp_path, monkeypatch):
    from spherex_viewer import config, pipeline

    def unreachable(*args, **kwargs):
        raise RuntimeError("SIA spherex_dr1 failed after 1 attempt")

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)              # nothing read from IRSA yet
    monkeypatch.setattr(pipeline, "_warm_results", {})
    monkeypatch.setattr(pipeline, "search", unreachable)
    warmed = []
    monkeypatch.setattr(pipeline, "_warm_later", lambda p: warmed.append(p))
    target = resolve.resolve("barnard", epoch_to=2026.75)
    request = {"ra": target["ra_now"], "dec": target["dec_now"], "fov": 8.0}
    result = pipeline.build_frames(request, lambda *_: None)
    assert result["snapshot"] and result["n_used"] > 100
    assert len(warmed) == 1                                            # and IRSA is read in the background
    with pytest.raises(RuntimeError):
        pipeline.build_frames({"ra": 10.0, "dec": 10.0, "fov": 8.0}, lambda *_: None)


def test_a_test_target_is_answered_live_once_its_frames_are_ready(tmp_path, monkeypatch):
    from spherex_viewer import config, pipeline

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "_warm_results", {})
    monkeypatch.setattr(pipeline, "_warm_later", lambda p: pytest.fail("nothing to read"))
    monkeypatch.setattr(pipeline, "_build_frames", lambda *a, **k: pytest.fail("must not wait on IRSA"))
    target = resolve.resolve("barnard", epoch_to=2026.75)
    params = pipeline.normalise_frame_params({"ra": target["ra_now"], "dec": target["dec_now"], "fov": 8.0})
    live = {"n_used": 395, "frames": [], "snapshot": None}
    pipeline._warm_results[pipeline._params_id(params)] = (__import__("time").time(), live)
    assert pipeline.build_frames(dict(params), lambda *_: None) is live
    # its target spectrum is then measured live too (here: the measurement is stubbed)
    monkeypatch.setattr(pipeline, "_build_spectrum", lambda p, progress: {"points": [1] * 500, "live": True})
    spectrum = pipeline.build_spectrum({"ra": target["ra"], "dec": target["dec"], "pmra": target["pmra"],
                                        "pmdec": target["pmdec"], "epoch": 2000.0, "r_ap": 2.0,
                                        "field": {"ra": params["ra"], "dec": params["dec"], "fov": 8.0}},
                                       lambda *_: None)
    assert spectrum.get("live")


def test_a_test_target_spectrum_follows_its_saved_frames(tmp_path, monkeypatch):
    from spherex_viewer import config, pipeline

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "_warm_results", {})
    monkeypatch.setattr(pipeline, "_build_spectrum", lambda *a, **k: pytest.fail("must not wait on IRSA"))
    target = resolve.resolve("barnard", epoch_to=2026.75)
    params = pipeline.normalise_frame_params({"ra": target["ra_now"], "dec": target["dec_now"], "fov": 8.0})
    spectrum = pipeline.build_spectrum({"ra": target["ra"], "dec": target["dec"], "pmra": target["pmra"],
                                        "pmdec": target["pmdec"], "epoch": 2000.0, "r_ap": 2.0,
                                        "field": {"ra": params["ra"], "dec": params["dec"], "fov": 8.0}},
                                       lambda *_: None)
    assert spectrum["snapshot"] and spectrum["points"]


def test_a_background_read_runs_once_per_field(tmp_path, monkeypatch):
    import threading

    from spherex_viewer import config, pipeline

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "_warm_results", {})
    monkeypatch.setattr(pipeline, "_warm_failed", {})
    gate, calls = threading.Event(), []

    def slow_build(p, progress, **kwargs):
        calls.append(kwargs.get("workers"))
        gate.wait(5)
        return {"n_used": 400}

    monkeypatch.setattr(pipeline, "_build_frames", slow_build)
    target = resolve.resolve("barnard", epoch_to=2026.75)
    params = pipeline.normalise_frame_params({"ra": target["ra_now"], "dec": target["dec_now"], "fov": 8.0})
    first = threading.Thread(target=pipeline.warm, args=(params,))
    first.start()
    for _ in range(100):
        if pipeline._is_warming(params):
            break
        __import__("time").sleep(0.01)
    pipeline.warm(params)                       # already being read: returns at once
    gate.set()
    first.join(5)
    assert calls == [pipeline.WARM_WORKERS]
    assert pipeline._ready_frames(params) == {"n_used": 400}


def test_fast_wcs_matches_astropy_header_wcs():
    import warnings

    from astropy.wcs import WCS

    from spherex_viewer import cutouts

    header = _sip_header(ra=271.3, dec=-33.2, roll_deg=37.0)
    header["A_ORDER"] = header["B_ORDER"] = header["AP_ORDER"] = header["BP_ORDER"] = 3
    for name, value in (("A_2_0", 2.1e-6), ("A_0_2", -1.3e-6), ("A_1_1", 4.2e-6), ("A_3_0", 5.6e-10),
                        ("B_0_2", 1.9e-6), ("B_2_1", 6.1e-10), ("AP_2_0", -2.0e-6), ("BP_1_1", 7.3e-7)):
        header[name] = value
    # values as a FITS card holds them (at most 20 characters), like every real header
    cards = [[k, float(f"{v:.14g}") if isinstance(v, float) else v] for k, v in reduce_header(header)]
    fast = cutouts._build_fast_wcs(cards)
    assert fast is not None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        slow = WCS(cutouts.header_from_cards(cards))
    xs, ys = np.meshgrid(np.linspace(-20, 2060, 9), np.linspace(-20, 2060, 9))
    ra, dec = slow.all_pix2world(xs.ravel(), ys.ravel(), 0)
    assert np.array_equal(np.array(fast.all_pix2world(xs.ravel(), ys.ravel(), 0)), np.array([ra, dec]))
    for a, b in zip(world_to_pixel(fast, ra, dec), world_to_pixel(slow, ra, dec)):
        assert np.array_equal(a, b)
    for a, b in zip(world_to_pixel(shifted_wcs(fast, 700, 900), ra, dec),
                    world_to_pixel(shifted_wcs(slow, 700, 900), ra, dec)):
        assert np.array_equal(a, b)
    # anything that is not a TAN-SIP image takes astropy's own path
    assert cutouts._build_fast_wcs([["CTYPE1", "RA---TAN"], ["CTYPE2", "DEC--TAN"], ["CRPIX1", 1.0]]) is None
    assert cutouts._build_fast_wcs(cards + [["PV2_1", 0.1]]) is None
    assert make_wcs(cards).sip is not None


def test_a_window_next_to_the_kept_one_is_read_together_with_it():
    from spherex_viewer.archive import merged_box

    field = (500, 589, 700, 789)                       # the field's window
    edge_source = (580, 607, 740, 767)                 # a source clicked at its edge
    assert merged_box(edge_source, field) == (500, 607, 700, 789)
    far = (1500, 1589, 100, 189)                       # another field on the same detector
    assert merged_box(far, field) == far               # kept apart: reading the box between would be wasteful


def test_a_finished_job_keeps_its_result_compressed_and_old_jobs_go():
    import gzip
    import json
    import time

    from spherex_viewer import jobs as jobs_module

    manager = jobs_module.JobManager(max_workers=2)
    job = manager.submit("frames", {"a": 1}, lambda p, progress: {"x": [1.5, 2], "s": "µm"})
    for _ in range(200):
        if job.state == "done":
            break
        time.sleep(0.01)
    assert job.state == "done" and json.loads(gzip.decompress(job.body)) == {"x": [1.5, 2], "s": "µm"}
    assert job.result == {"x": [1.5, 2], "s": "µm"} and not job.snapshot
    assert manager.submit("frames", {"a": 1}, lambda p, progress: pytest.fail("shared")) is job
    saved = manager.submit("frames", {"b": 1}, lambda p, progress: {"snapshot": {"saved": "2026-11-15"}})
    for _ in range(200):
        if saved.state == "done":
            break
        time.sleep(0.01)
    assert saved.snapshot                                  # a saved copy is not handed out again
    assert manager.submit("frames", {"b": 1}, lambda p, progress: {}) is not saved
    for job_ in list(manager._jobs.values()):
        job_.finished = time.time() - jobs_module.JOB_TTL_SECONDS - 1
    manager.submit("frames", {"c": 1}, lambda p, progress: {})
    assert len(manager._jobs) == 1


def test_uptime_monitors_can_check_with_head():
    from fastapi.testclient import TestClient

    from spherex_viewer import main

    client = TestClient(main.app)
    for path in ("/api/health", "/"):
        assert client.head(path).status_code == 200, path
        assert client.get(path).status_code == 200, path
    assert client.get("/api/health").json()["status"] == "ok"


def test_sesame_asks_the_mirrors_when_the_main_server_is_slow(tmp_path, monkeypatch):
    import time

    from spherex_viewer import config

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "SESAME_URLS", ["https://main", "https://mirror"])
    monkeypatch.setattr(resolve, "SESAME_HEDGE_SECONDS", 0.05)
    answer = ('<?xml version="1.0"?><Sesame><Target><Resolver name="S=Simbad"><oname>Proxima Centauri</oname>'
              '<jradeg>217.42895</jradeg><jdedeg>-62.67948</jdedeg></Resolver></Target></Sesame>')
    asked = []

    class Reply:
        status_code = 200
        text = answer

    def fake_fetch(method, url, **kwargs):
        asked.append(url.split("?")[0])
        if url.startswith("https://main"):
            time.sleep(1.0)
        return Reply()

    monkeypatch.setattr(resolve, "fetch", fake_fetch)
    started = time.time()
    found = resolve.sesame("Proxima Centauri")
    assert found["name"] == "Proxima Centauri" and time.time() - started < 0.8
    assert asked == ["https://main", "https://mirror"]
    monkeypatch.setattr(resolve, "fetch", lambda *a, **k: pytest.fail("remembered"))
    assert resolve.sesame("proxima  centauri")["ra"] == pytest.approx(217.42895)
