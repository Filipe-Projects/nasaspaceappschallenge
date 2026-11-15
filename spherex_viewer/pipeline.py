"""Orchestration: search -> cutouts -> reproject -> stack -> analyse."""
from __future__ import annotations

import base64
import hashlib
import logging
import math
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from . import config, detect, snapshots
from .archive import Archive
from .cache import default_cache
from .cutouts import DEFAULT_FLAG_DEFS, flag_names, mask_from_names
from .fitsrange import TransferStats
from .photometry import measure
from .processing import (Epoch, ReprojectedFrame, TargetGrid, group_epochs, plan_grid,
                         reproject_cutout, select_exposures, stack_frames)
from .resolve import propagate
from .sia import Exposure, find_exposures, mjd_to_iso, mjd_to_year, search_is_cached

log = logging.getLogger("spherex_viewer")

# Bump when the processing changes, so cached results are not reused.
RESULT_VERSION = "12"

BAND_RANGES_UM = {1: (0.75, 1.11), 2: (1.10, 1.65), 3: (1.63, 2.43),
                  4: (2.40, 3.85), 5: (3.81, 4.43), 6: (4.41, 5.01)}
BANDS = ["ALL"] + [f"D{d}" for d in range(1, 7)]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def b64_array(arr: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(arr.astype(dtype)).tobytes()).decode("ascii")


def frame_stats(data: np.ndarray) -> dict:
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return {"p1": 0.0, "p50": 0.0, "p99": 0.0, "max": 0.0, "rms": 0.0, "coverage": 0.0}
    p1, p50, p99 = np.percentile(finite, [1, 50, 99])
    mad = 1.4826 * np.median(np.abs(finite - p50))
    return {"p1": float(p1), "p50": float(p50), "p99": float(p99), "max": float(finite.max()),
            "rms": float(mad), "coverage": float(finite.size) / float(data.size)}


def band_label(band: str) -> str:
    if band == "ALL":
        return "All detectors (0.75–5.0 µm)"
    lo, hi = BAND_RANGES_UM[int(band[1])]
    return f"Band {band[1]} ({lo:.2f}–{hi:.2f} µm)"


def search_radius_deg(fov_arcmin: float) -> float:
    """Circle that contains the square field of view plus a small margin."""
    return float(fov_arcmin) / 60.0 * math.sqrt(2) / 2.0 + 0.5 / 60.0


def search(ra: float, dec: float, fov_arcmin: float, max_exposures: int | None = None,
           collections=None, failures: dict | None = None,
           timeout: float | None = None) -> tuple[list[Exposure], list[Epoch], int]:
    """Exposures covering the field, grouped in epochs, with the subsample chosen."""
    exposures = find_exposures(ra, dec, search_radius_deg(fov_arcmin), collections=collections,
                               failures=failures, timeout=timeout)
    epochs = group_epochs(exposures)
    n_selected = select_exposures(epochs, max_exposures)
    return exposures, epochs, n_selected


def _search_warnings(failures: dict) -> list[str]:
    return [f"The {name.replace('spherex_', '').upper()} release could not be searched ({error.split(':')[0]}); "
            "some epochs may be missing. Load again in a moment." for name, error in failures.items()]


def exposure_summary(exp: Exposure, epoch: int | None, used: bool) -> dict:
    return {"obs_id": exp.obs_id, "detector": exp.detector, "mjd": round(exp.mjd, 5),
            "epoch": epoch, "collection": exp.collection, "used": used}


def _exposure_hash(exposures) -> str:
    return hashlib.sha1("|".join(sorted(e.key for e in exposures)).encode()).hexdigest()


def _transfer(stats: TransferStats) -> dict:
    return {"requests": stats.requests, "megabytes": round(stats.bytes / 1e6, 2)}


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------
def normalise_frame_params(params: dict) -> dict:
    fov = float(params.get("fov") or config.DEFAULT_FOV_ARCMIN)
    fov = min(max(fov, config.MIN_FOV_ARCMIN), config.MAX_FOV_ARCMIN)
    group = params.get("group") or "epoch"
    if group not in ("epoch", "exposure"):
        group = "epoch"
    detector = str(params.get("detector") or "ALL").upper()
    if detector not in BANDS:
        detector = "ALL"
    mask = params.get("mask")
    if isinstance(mask, str):
        mask = [m for m in mask.split(",") if m]
    if mask is None:
        mask = list(config.DEFAULT_MASK_NAMES)
    flags = params.get("flags")
    flags = config.DEFAULT_FLAGS if flags is None else bool(flags)
    max_exposures = int(params.get("max_exposures") or config.MAX_EXPOSURES)
    return {
        "ra": round(float(params["ra"]) % 360.0, 6),
        "dec": round(max(-90.0, min(90.0, float(params["dec"]))), 6),
        "fov": round(fov, 3),
        "group": group,
        "detector": detector if group == "exposure" else "ALL",
        "flags": flags,
        "mask": sorted(str(m).upper() for m in mask) if flags else [],
        "max_exposures": max(12, min(max_exposures, 4 * config.MAX_EXPOSURES)),
    }


# Fields being read from IRSA in the background while their saved copy is on show.
_warming: set[tuple] = set()
_warm_failed: dict[tuple, float] = {}
_warm_lock = threading.Lock()
WARM_RETRY_SECONDS = 300.0


def _field_key(p: dict) -> tuple:
    return (round(p["ra"], 5), round(p["dec"], 5), round(p["fov"], 3), p.get("group", "epoch"), p.get("detector", "ALL"))


def _is_warming(p: dict) -> bool:
    with _warm_lock:
        return _field_key(p) in _warming


def _warm_later(p: dict) -> None:
    """Read the field from IRSA in the background, so the next visit gets the live result."""
    key = _field_key(p)
    with _warm_lock:
        if key in _warming or time.time() - _warm_failed.get(key, 0.0) < WARM_RETRY_SECONDS:
            return
        _warming.add(key)

    def work() -> None:
        try:
            _build_frames(p, lambda *_: None)
        except Exception as exc:  # noqa: BLE001 - the saved copy stays on show
            log.warning("could not read (%.5f, %.5f) from IRSA: %s", p["ra"], p["dec"], exc)
            with _warm_lock:
                _warm_failed[key] = time.time()
        finally:
            with _warm_lock:
                _warming.discard(key)

    threading.Thread(target=work, name="warm", daemon=True).start()


def build_frames(params: dict, progress, fallback: bool = True) -> dict:
    """The frame sequence for the viewer (JSON-serialisable).

    The five test targets have a saved copy (see snapshots.py).  It is shown
    at once when the field has never been read on this server, while IRSA is
    read in the background, and whenever IRSA cannot be reached; a field that
    is in the cache is always answered from the live data.
    """
    p = normalise_frame_params(params)
    saved = snapshots.find("frames", p) if fallback else None
    if saved is not None and (_is_warming(p) or not search_is_cached(p["ra"], p["dec"], search_radius_deg(p["fov"]))):
        _warm_later(p)
        progress(1, 1, "loaded a saved copy")
        return saved
    try:
        result = _build_frames(p, progress)
        if saved is not None and result["n_used"] < 0.5 * saved["n_used"]:
            raise RuntimeError("most exposures could not be read")
        return result
    except Exception as exc:  # noqa: BLE001 - a saved copy is better than an error
        if saved is None:
            raise
        log.warning("frames for (%.5f, %.5f) served from a snapshot: %s", p["ra"], p["dec"], exc)
        progress(1, 1, "loaded a saved copy")
        return saved


def _build_frames(p: dict, progress, search_timeout: float | None = None) -> dict:
    t_start = time.time()
    ra, dec, fov = p["ra"], p["dec"], p["fov"]
    progress(0, 1, "searching the IRSA archive")
    failures: dict[str, str] = {}
    exposures, epochs, n_selected = search(ra, dec, fov, p["max_exposures"], failures=failures,
                                           timeout=search_timeout)
    if not exposures:
        raise LookupError("No SPHEREx exposures cover this position yet.")

    cache = default_cache()
    result_key = f"frames|v{RESULT_VERSION}|{_exposure_hash(exposures)}|{sorted(p.items())}"
    cached = cache.get_json("results", result_key)
    if cached is not None:
        progress(1, 1, "loaded from cache")
        cached["cached"] = True
        return cached

    npix, scale = plan_grid(fov)
    grid = TargetGrid(ra, dec, npix, scale)
    epoch_of = {e.key: ep.index for ep in epochs for e in ep.exposures}
    wanted = [e for ep in epochs for e in ep.used]
    if p["group"] == "exposure":
        if p["detector"] != "ALL":
            wanted = [e for e in wanted if e.band == p["detector"]]
        if len(wanted) * npix * npix > config.MAX_EXPOSURE_MODE_PIXELS:
            raise ValueError(
                f"{len(wanted)} individual exposures of {npix}x{npix} pixels are too many to send to the "
                "browser. Choose a single band or a smaller field of view.")
    used_keys = {e.key for e in wanted}

    stats = TransferStats()
    archive = Archive(cache, stats)
    mask_bits = mask_from_names(DEFAULT_FLAG_DEFS, p["mask"]) if p["flags"] else 0

    def task(exp: Exposure):
        cutout = archive.image_cutout(exp, grid.outline_ra, grid.outline_dec, pad=2, want_flags=p["flags"])
        if cutout is None:
            return None
        return reproject_cutout(cutout, grid, mask_bits)

    errors: dict[str, str] = {}
    out_frames: list[dict] = []
    epoch_stacks: dict[int, dict] = {}            # epoch -> stacks kept for the change analysis
    single: list[ReprojectedFrame] = []
    pending = {ep.index: sum(1 for e in ep.used if e.key in used_keys) for ep in epochs}
    by_epoch: dict[int, list[ReprojectedFrame]] = {ep.index: [] for ep in epochs}
    epoch_by_index = {ep.index: ep for ep in epochs}
    stacked_frames: dict[int, list[dict]] = {}
    total = len(wanted)
    done = 0
    n_used = 0
    progress(0, total, f"reading {total} exposures from IRSA")

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        futures = {pool.submit(task, exp): exp for exp in wanted}
        for future in as_completed(futures):
            exp = futures[future]
            ep_index = epoch_of[exp.key]
            done += 1
            try:
                frame = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the job
                errors[exp.key] = f"{type(exc).__name__}: {exc}"
                frame = None
            if frame is not None:
                n_used += 1
                if p["group"] == "exposure":
                    single.append(frame)
                else:
                    by_epoch[ep_index].append(frame)
            pending[ep_index] -= 1
            if p["group"] == "epoch" and pending[ep_index] == 0:
                frames = by_epoch.pop(ep_index)       # stack now, then let the frames go
                if frames:
                    stacked_frames[ep_index] = _stack_epoch(epoch_by_index[ep_index], frames, npix, epoch_stacks)
                del frames
            progress(done, total, f"read {done}/{total} exposures · {stats.requests} requests, "
                                  f"{stats.bytes / 1e6:.1f} MB")

    if n_used == 0:
        detail = "; ".join(list(errors.values())[:3])
        raise RuntimeError("Could not read any exposure from the archive" + (f": {detail}" if detail else "."))

    analysis = None
    if p["group"] == "epoch":
        for ep in epochs:
            out_frames.extend(stacked_frames.get(ep.index, []))
        ordered = [epoch_stacks[i] for i in sorted(epoch_stacks)]
        if len(ordered) >= 2:
            progress(total, total, "looking for moving and variable sources")
            try:
                analysis = detect.analyse(ordered, grid)
            except Exception as exc:  # noqa: BLE001 - the movie matters more than the list
                analysis = {"sources": [], "n_sources": 0, "n_moving": 0, "n_variable": 0,
                            "registration": [], "error": f"{type(exc).__name__}: {exc}"}
    else:
        out_frames = _exposure_frames(single, epoch_of, archive, npix)

    result = {
        "params": p,
        "target": {"ra": ra, "dec": dec, "fov_arcmin": fov, "npix": npix,
                   "scale_arcsec": scale, "wcs": grid.to_dict()},
        "epochs": [ep.to_dict() for ep in epochs],
        "bands": [{"id": b, "label": band_label(b)} for b in BANDS],
        "frames": out_frames,
        "exposures": [exposure_summary(e, epoch_of.get(e.key), e.key in used_keys) for e in exposures],
        "analysis": analysis,
        "flag_defs": DEFAULT_FLAG_DEFS,
        "has_flags": p["flags"],
        "mask_names": p["mask"],
        "collections": sorted({e.collection for e in exposures}),
        "warnings": _search_warnings(failures),
        "n_found": len(exposures),
        "subsampled": n_selected < len(exposures),
        "n_selected": total,
        "n_used": n_used,
        "errors": dict(list(errors.items())[:50]),
        "n_errors": len(errors),
        "transfer": _transfer(stats),
        "elapsed_s": round(time.time() - t_start, 1),
        "cached": False,
    }
    if not failures and not errors:       # an incomplete search or a failed read must not be remembered
        cache.put_json("results", result_key, result)
    return result


def _frame_dict(frame_id: str, label: str, epoch: int | None, band: str, sel_exposures: list[Exposure],
                data: np.ndarray, nexp: np.ndarray, flags: np.ndarray | None, npix: int) -> dict:
    mjds = [e.mjd for e in sel_exposures]
    mean_mjd = float(np.mean(mjds))
    return {
        "id": frame_id,
        "epoch": epoch,
        "band": band,
        "label": label,
        "n_exposures": len(sel_exposures),
        "mjd": mean_mjd,
        "mjd_start": float(min(e.mjd_start for e in sel_exposures)),
        "mjd_end": float(max(e.mjd_end for e in sel_exposures)),
        "date": mjd_to_iso(mean_mjd, seconds=len(sel_exposures) == 1),
        "year": mjd_to_year(mean_mjd),
        "wl_min": float(min(e.em_min_um for e in sel_exposures)),
        "wl_max": float(max(e.em_max_um for e in sel_exposures)),
        "shape": [npix, npix],
        "data": b64_array(data, "<f4"),
        "nexp": b64_array(np.minimum(nexp, 255), "u1"),
        "flags": b64_array(flags, "<u4") if flags is not None else None,
        "stats": frame_stats(data),
    }


def _stack_epoch(epoch: Epoch, frames: list[ReprojectedFrame], npix: int, epoch_stacks: dict) -> list[dict]:
    """Band stacks of one epoch, and their mean as the all-detector frame.

    The all-detector frame is the unweighted mean of the band stacks rather
    than of all exposures: outlier rejection then happens inside each band (a
    source seen only at 4-5 um is not "rejected" by the other four bands), and
    every epoch gets the same effective bandpass even when the number of
    exposures per band differs.
    """
    out = []
    bands: dict[str, tuple] = {}
    stacked_by_band = {}
    for band in BANDS[1:]:
        sel = [f for f in frames if f.exposure.band == band]
        if not sel:
            continue
        stacked = stack_frames(sel)
        stacked_by_band[band] = (stacked, [f.exposure for f in sel])
        bands[band] = (stacked.data, stacked.nexp, stacked.clipped)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        all_data = np.nanmean(np.stack([st.data for st, _ in stacked_by_band.values()]), axis=0).astype(np.float32)
    all_nexp = np.sum([st.nexp.astype(np.int32) for st, _ in stacked_by_band.values()], axis=0)
    all_flags = None
    if all(st.flags is not None for st, _ in stacked_by_band.values()):
        all_flags = np.bitwise_or.reduce(np.stack([st.flags for st, _ in stacked_by_band.values()]), axis=0)
    all_exps = [e for _, exps in stacked_by_band.values() for e in exps]
    item = _frame_dict(f"E{epoch.index}-ALL", f"Epoch {epoch.index} · {band_label('ALL')}", epoch.index, "ALL",
                       all_exps, all_data, all_nexp, all_flags, npix)
    item["detectors"] = sorted(int(b[1]) for b in stacked_by_band)
    out.append(item)
    for band, (stacked, exps) in stacked_by_band.items():
        out.append(_frame_dict(f"E{epoch.index}-{band}", f"Epoch {epoch.index} · {band_label(band)}",
                               epoch.index, band, exps, stacked.data, stacked.nexp, stacked.flags, npix))
    epoch_stacks[epoch.index] = {
        "index": epoch.index,
        "year": mjd_to_year(float(np.mean([e.mjd for e in all_exps]))),
        "all": (all_data, all_nexp),
        "bands": bands,
    }
    return out


def _exposure_frames(frames: list[ReprojectedFrame], epoch_of: dict, archive: Archive, npix: int) -> list[dict]:
    """One frame per exposure, in time order, labelled with its wavelength."""
    out = []
    send_flags = len(frames) <= 150
    for frame in sorted(frames, key=lambda f: (f.exposure.mjd, f.exposure.detector)):
        exp = frame.exposure
        wavelength = bandwidth = None
        try:
            wl, bw = archive.wave(exp)(frame.x_src, frame.y_src)
            wavelength, bandwidth = float(wl), float(bw)
        except Exception:  # noqa: BLE001 - the wavelength label is a nicety
            pass
        label = f"{exp.date_iso[:16].replace('T', ' ')} · {exp.band}"
        if wavelength:
            label += f" · {wavelength:.3f} µm"
        item = _frame_dict(exp.key, label, epoch_of.get(exp.key), exp.band, [exp], frame.data,
                           np.isfinite(frame.data).astype(np.uint8),
                           frame.flags if send_flags else None, npix)
        item.update({"wavelength": wavelength, "bandwidth": bandwidth,
                     "background": frame.background, "noise": frame.noise})
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Spectrum / light curve
# --------------------------------------------------------------------------
def normalise_spectrum_params(params: dict) -> dict:
    r_ap = min(max(float(params.get("r_ap") or 2.0), 1.0), 4.0)
    field = params.get("field") or None
    if field:
        fov = min(max(float(field.get("fov") or config.DEFAULT_FOV_ARCMIN), config.MIN_FOV_ARCMIN),
                  config.MAX_FOV_ARCMIN)
        field = {"ra": round(float(field["ra"]) % 360.0, 6), "dec": round(float(field["dec"]), 6),
                 "fov": round(fov, 3)}
    return {
        "ra": round(float(params["ra"]) % 360.0, 6),
        "dec": round(max(-90.0, min(90.0, float(params["dec"]))), 6),
        "pmra": float(params.get("pmra") or 0.0),
        "pmdec": float(params.get("pmdec") or 0.0),
        "epoch": float(params.get("epoch") or 2000.0),
        "r_ap": r_ap,
        "field": field,
        "max_exposures": int(params.get("max_exposures") or config.MAX_EXPOSURES),
    }


def build_spectrum(params: dict, progress, fallback: bool = True) -> dict:
    """Per-exposure photometry of one position: spectrum and light curve.

    When the request names the field the viewer is showing, the same search
    and the same exposure subsample are used, so every cutout is already in
    the cache and the answer is immediate.
    """
    p = normalise_spectrum_params(params)
    saved = snapshots.find("spectrum", p) if fallback else None
    if saved is not None:
        field = dict(p["field"], group="epoch", detector="ALL")
        if _is_warming(field) or not search_is_cached(field["ra"], field["dec"], search_radius_deg(field["fov"])):
            progress(1, 1, "loaded a saved copy")
            return saved
    try:
        result = _build_spectrum(p, progress)
        if saved is not None and len(result["points"]) < 0.5 * len(saved["points"]):
            raise RuntimeError("most exposures could not be read")
        return result
    except Exception as exc:  # noqa: BLE001 - a saved copy is better than an error
        if saved is None:
            raise
        log.warning("spectrum at (%.5f, %.5f) served from a snapshot: %s", p["ra"], p["dec"], exc)
        progress(1, 1, "loaded a saved copy")
        return saved


def _build_spectrum(p: dict, progress, search_timeout: float | None = None) -> dict:
    t_start = time.time()
    progress(0, 1, "searching the IRSA archive")
    ra_mid, dec_mid = propagate(p["ra"], p["dec"], p["pmra"], p["pmdec"], p["epoch"], 2026.0)
    if p["field"]:
        exposures, epochs, _ = search(p["field"]["ra"], p["field"]["dec"], p["field"]["fov"], p["max_exposures"],
                                      timeout=search_timeout)
    else:
        exposures, epochs, _ = search(ra_mid, dec_mid, 3.0, p["max_exposures"], timeout=search_timeout)
    if not exposures:
        raise LookupError("No SPHEREx exposures cover this position yet.")
    epoch_of = {e.key: ep.index for ep in epochs for e in ep.exposures}
    wanted = [e for ep in epochs for e in ep.used]

    cache = default_cache()
    result_key = f"spectrum|v{RESULT_VERSION}|{_exposure_hash(wanted)}|{sorted((k, str(v)) for k, v in p.items())}"
    cached = cache.get_json("results", result_key)
    if cached is not None:
        progress(1, 1, "loaded from cache")
        cached["cached"] = True
        return cached

    stats = TransferStats()
    archive = Archive(cache, stats)
    r_in = max(4.0, p["r_ap"] + 2.0)
    r_out = max(8.0, p["r_ap"] + 6.0)
    pad = int(math.ceil(r_out)) + 2

    def task(exp: Exposure):
        ra_t, dec_t = propagate(p["ra"], p["dec"], p["pmra"], p["pmdec"], p["epoch"], mjd_to_year(exp.mjd))
        cutout = archive.image_cutout(exp, [ra_t], [dec_t], pad=pad)
        if cutout is None:
            return None
        cutout.wave = archive.wave(exp)
        cutout.flags = archive.cached_flags(exp, cutout)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            point = measure(cutout, ra_t, dec_t, r_ap=p["r_ap"], r_in=r_in, r_out=r_out)
        if point is None:
            return None
        value = point.pop("flag_value")
        point["flags"] = flag_names(DEFAULT_FLAG_DEFS, value) if value is not None else None
        point["epoch"] = epoch_of.get(exp.key)
        return point

    points = []
    errors: dict[str, str] = {}
    total = len(wanted)
    done = 0
    progress(0, total, f"measuring {total} exposures")
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        futures = {pool.submit(task, exp): exp for exp in wanted}
        for future in as_completed(futures):
            exp = futures[future]
            done += 1
            try:
                point = future.result()
            except Exception as exc:  # noqa: BLE001
                errors[exp.key] = f"{type(exc).__name__}: {exc}"
                point = None
            if point is not None:
                points.append(point)
            if done % 20 == 0 or done == total:
                progress(done, total, f"measured {done}/{total} exposures")
    points.sort(key=lambda pt: (pt["wavelength_um"] or 0.0))
    if any(pt["flags"] is None for pt in points):      # flags for some exposures only would mislead
        for pt in points:
            pt["flags"] = None

    result = {
        "params": p,
        "position": {"ra": p["ra"], "dec": p["dec"], "epoch": p["epoch"], "ra_mid": ra_mid, "dec_mid": dec_mid},
        "epochs": [ep.to_dict() for ep in epochs],
        "points": points,
        "has_flags": any(pt["flags"] is not None for pt in points),
        "n_exposures": total,
        "n_errors": len(errors),
        "errors": dict(list(errors.items())[:20]),
        "transfer": _transfer(stats),
        "units": {"flux": "mJy", "wavelength": "um"},
        "note": "Aperture photometry on the calibrated images (MJy/sr times the 6.15 arcsec pixel), sky from "
                "an annulus, no aperture correction; error bars are the sky noise only. Quick-look.",
        "elapsed_s": round(time.time() - t_start, 1),
        "cached": False,
    }
    if not errors:                        # exposures that could not be read are tried again next time
        cache.put_json("results", result_key, result)
    return result
