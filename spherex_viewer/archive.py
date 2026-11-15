"""Access planner for SPHEREx Level 2 spectral images held by IRSA.

Goal: get a cutout out of a 70 MB file with as few requests and bytes as
possible, because a single sky position is covered by hundreds of files.

What is learned once and reused
-------------------------------
* **Version layout**: byte sizes of the PRIMARY and IMAGE headers and of the
  IMAGE data, plus the FLAGS header, are the same for every file written by a
  pipeline version.  Learned from the first file of each version.
* **Distortion template**: the SIP polynomial of each of the six detectors,
  used with the footprint returned by the image search to predict where a sky
  position falls on the detector (see :mod:`predict`).
* **Wavelength table**: the WCS-WAVE lookup of each detector and version.

Typical cost per file afterwards
--------------------------------
* image cutout: **one** multi-range request carrying the headers (26 kB) and
  the predicted rows of the IMAGE extension;
* flags (optional): one more request, or two for the tile-compressed QR3
  files (tile descriptors, then the compressed rows).

Every prediction is verified against the bytes that come back; a file that
does not match its siblings is simply read the slow, explicit way.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from astropy.io import fits

from . import config
from .cache import DiskCache, default_cache
from .cutouts import (DEFAULT_FLAG_DEFS, Cutout, WaveLookup, bounding_box, box_covers, clip_box,
                      crop, flag_definitions, make_wcs, reduce_header, shifted_wcs)
from .fitsrange import (BLOCK, LayoutMismatch, RangeClient, RangeError, TransferStats, assemble_tiles,
                        bitpix_dtype, data_bytes, find_header_end, header_from_text, header_to_text,
                        is_compressed, padded, parse_header, row_ranges, rows_to_array, slice_parts,
                        tile_locations, tile_plan, apply_scaling)
from .predict import SipTemplate, fit_pointing, predict_pixels
from .sia import Exposure

PROBE_BYTES = 16 * BLOCK          # PRIMARY + IMAGE headers of every pipeline version so far
MAX_HEADER_BYTES = 96 * BLOCK
IMAGE_NS = "img"
FLAGS_NS = "flg"
OUTSIDE = "outside"


# --------------------------------------------------------------------------
# Knowledge about files
# --------------------------------------------------------------------------
@dataclass
class VersionLayout:
    """What every file of a pipeline version has in common.

    The IMAGE header is not part of it: its length changes with the detector
    and sometimes from file to file (it sits near a 2880-byte block boundary),
    so that is tracked separately as a per-detector tally.
    """
    primary_bytes: int
    image_data_bytes: int
    flags_header_bytes: int
    flags_header_text: str
    _flags_header: fits.Header | None = field(default=None, repr=False, compare=False)

    @property
    def flags_header(self) -> fits.Header:
        if self._flags_header is None:
            self._flags_header = header_from_text(self.flags_header_text)
        return self._flags_header

    def to_json(self) -> dict:
        return {"primary_bytes": self.primary_bytes, "image_data_bytes": self.image_data_bytes,
                "flags_header_bytes": self.flags_header_bytes, "flags_header_text": self.flags_header_text}

    @classmethod
    def from_json(cls, data: dict) -> "VersionLayout":
        return cls(int(data["primary_bytes"]), int(data["image_data_bytes"]),
                   int(data["flags_header_bytes"]), data["flags_header_text"])


@dataclass
class FileInfo:
    cards: list                      # reduced IMAGE header (WCS, times)
    nx: int
    ny: int
    bitpix: int
    image_data_offset: int
    flags_header_offset: int
    flags_header_bytes: int | None = None   # set once verified for this file
    flags_header_text: str | None = None    # only when it differs from the version layout
    size: int | None = None

    def to_json(self) -> dict:
        return {"cards": self.cards, "nx": self.nx, "ny": self.ny, "bitpix": self.bitpix,
                "image_data_offset": self.image_data_offset, "flags_header_offset": self.flags_header_offset,
                "flags_header_bytes": self.flags_header_bytes, "flags_header_text": self.flags_header_text,
                "size": self.size}

    @classmethod
    def from_json(cls, data: dict) -> "FileInfo":
        return cls(data["cards"], int(data["nx"]), int(data["ny"]), int(data["bitpix"]),
                   int(data["image_data_offset"]), int(data["flags_header_offset"]),
                   data.get("flags_header_bytes"), data.get("flags_header_text"), data.get("size"))


class ArchiveState:
    """Process-wide memory of layouts, templates and wavelength tables."""

    INFO_LRU = 4000

    def __init__(self, cache: DiskCache):
        self.cache = cache
        self._layouts: dict[str, VersionLayout | None] = {}
        self._header_sizes: dict[str, dict[int, int]] = {}
        self._templates: dict[int, SipTemplate | None] = {}
        self._waves: dict[str, WaveLookup] = {}
        self._infos: OrderedDict[str, FileInfo] = OrderedDict()
        self._locks: dict = {}
        self._guard = threading.Lock()

    def lock(self, key) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    # -- version layouts ---------------------------------------------------
    def layout(self, hint_key: str) -> VersionLayout | None:
        if hint_key not in self._layouts:
            data = self.cache.get_json("version", hint_key)
            if data is None:
                return None            # do not memoise a miss: another thread may be learning it
            self._layouts[hint_key] = VersionLayout.from_json(data)
        return self._layouts[hint_key]

    def set_layout(self, hint_key: str, layout: VersionLayout) -> None:
        self._layouts[hint_key] = layout
        self.cache.put_json("version", hint_key, layout.to_json())

    # -- IMAGE header sizes per (version, detector) -----------------------------
    def header_guess(self, hint_key: str, detector: int) -> tuple[int, int] | None:
        """``(most common, largest)`` IMAGE header size seen so far, or None."""
        key = f"{hint_key}/D{detector}"
        with self._guard:
            tally = self._header_sizes.get(key)
        if tally is None:
            data = self.cache.get_json("hdrsize", key)
            if not data:
                return None
            tally = {int(k): int(v) for k, v in data.items()}
            with self._guard:
                tally = self._header_sizes.setdefault(key, tally)
        if not tally:
            return None
        return max(tally, key=lambda size: tally[size]), max(tally)

    def note_header_size(self, hint_key: str, detector: int, size: int) -> None:
        key = f"{hint_key}/D{detector}"
        if self.header_guess(hint_key, detector) is None:
            with self._guard:
                self._header_sizes.setdefault(key, {})
        with self._guard:
            tally = self._header_sizes[key]
            new_size = size not in tally
            tally[size] = tally.get(size, 0) + 1
            persist = new_size or sum(tally.values()) % 25 == 0
            snapshot = dict(tally)
        if persist:
            self.cache.put_json("hdrsize", key, {str(k): v for k, v in snapshot.items()})

    # -- distortion templates ------------------------------------------------
    def template(self, detector: int) -> SipTemplate | None:
        if detector not in self._templates:
            data = self.cache.get_json("template", f"D{detector}")
            if data is None:
                return None
            self._templates[detector] = SipTemplate.from_json(data)
        return self._templates[detector]

    def set_template(self, detector: int, template: SipTemplate) -> None:
        self._templates[detector] = template
        self.cache.put_json("template", f"D{detector}", template.to_json())

    # -- wavelength tables ------------------------------------------------------
    def wave(self, key: str) -> WaveLookup | None:
        if key not in self._waves:
            data = self.cache.get_json("wavemap", key)
            if data is None:
                return None
            self._waves[key] = WaveLookup.from_json(data)
        return self._waves[key]

    def set_wave(self, key: str, wave: WaveLookup) -> None:
        self._waves[key] = wave
        self.cache.put_json("wavemap", key, wave.to_json())

    # -- per-file info ------------------------------------------------------------
    def info(self, url: str) -> FileInfo | None:
        with self._guard:
            hit = self._infos.get(url)
            if hit is not None:
                self._infos.move_to_end(url)
                return hit
        data = self.cache.get_json("file", url)
        if data is None:
            return None
        try:
            info = FileInfo.from_json(data)
        except (KeyError, TypeError, ValueError):
            return None
        self._remember(url, info)
        return info

    def set_info(self, url: str, info: FileInfo) -> None:
        self._remember(url, info)
        self.cache.put_json("file", url, info.to_json())

    def _remember(self, url: str, info: FileInfo) -> None:
        with self._guard:
            self._infos[url] = info
            self._infos.move_to_end(url)
            while len(self._infos) > self.INFO_LRU:
                self._infos.popitem(last=False)


_states: dict[str, ArchiveState] = {}
_states_guard = threading.Lock()


def shared_state(cache: DiskCache) -> ArchiveState:
    key = str(cache.root)
    with _states_guard:
        if key not in _states:
            _states[key] = ArchiveState(cache)
        return _states[key]


def expand_box(box, margin: int, nx: int, ny: int):
    return clip_box(box[0] - margin, box[1] + margin, box[2] - margin, box[3] + margin, nx, ny)


# A file keeps one window on disk.  A request just outside it (a source clicked
# near the edge of the field) reads the box around both when that box is barely
# bigger than the two windows, so the field's window is not lost and the next
# load of the field is not read from IRSA again.
MERGE_SLACK = 1.25
MERGE_MAX_PIXELS = 600 * 600


def _area(box) -> int:
    return (box[1] - box[0]) * (box[3] - box[2])


def merged_box(want, held):
    """*want* grown to also hold the kept window *held*, when that costs little; else *want*."""
    merged = (min(want[0], held[0]), max(want[1], held[1]), min(want[2], held[2]), max(want[3], held[3]))
    if _area(merged) <= min(MERGE_SLACK * (_area(want) + _area(held)), MERGE_MAX_PIXELS):
        return merged
    return want


# --------------------------------------------------------------------------
# Archive
# --------------------------------------------------------------------------
class Archive:
    """Cutouts, flags and wavelength tables for SPHEREx exposures."""

    def __init__(self, cache: DiskCache | None = None, stats: TransferStats | None = None):
        self.cache = cache or default_cache()
        self.state = shared_state(self.cache)
        self.stats = stats or TransferStats()
        self.client = RangeClient(stats=self.stats)

    # ------------------------------------------------------------------ cutouts
    def image_cutout(self, exp: Exposure, ra, dec, pad: int = 2, want_flags: bool = False) -> Cutout | None:
        """Cutout covering the sky points (plus *pad* pixels), or None if off-detector."""
        ra = np.atleast_1d(np.asarray(ra, dtype=float))
        dec = np.atleast_1d(np.asarray(dec, dtype=float))
        info = self.state.info(exp.url)
        speculative = None
        if info is None:
            if not self._predictable(exp):
                # Cold start for this pipeline version or detector: let one
                # thread learn the layout while its siblings wait.
                with self.state.lock(("cold", exp.hint_key, exp.detector)):
                    if not self._predictable(exp):
                        info = self._discover(exp)
            if info is None:
                box = self._predict_box(exp, ra, dec, pad + config.PREDICT_MARGIN_PX + config.CUTOUT_MARGIN_PX)
                if box == OUTSIDE:
                    return None
                info, speculative = self._header_and_rows(exp, box)

        wcs = make_wcs(info.cards)
        need = bounding_box(wcs, ra, dec, pad, info.nx, info.ny)
        if need is None:
            return None

        image = None
        held = None
        if speculative is not None and box_covers(speculative[0], need):
            self._store_window(IMAGE_NS, exp.url, speculative[0], speculative[1])
            image = crop(speculative[1], speculative[0], need)
        if image is None:
            held = self._held_window(IMAGE_NS, exp.url)
            if held is not None and box_covers(held[0], need):
                image = crop(held[1], held[0], need)
        if image is None:
            want = expand_box(need, config.CUTOUT_MARGIN_PX, info.nx, info.ny)
            if held is not None:
                want = merged_box(want, held[0])
            full = self._read_image_rows(exp.url, info, want)
            self._store_window(IMAGE_NS, exp.url, want, full)
            image = crop(full, want, need)

        flags = self.flags_window(exp, info, need) if want_flags else None
        return Cutout(exposure=exp, x0=need[0], y0=need[2], image=image,
                      wcs=shifted_wcs(wcs, need[0], need[2]), flags=flags)

    def cached_flags(self, exp: Exposure, cutout: Cutout) -> np.ndarray | None:
        """Flags for a cutout if they are already on disk (never touches the network)."""
        ny, nx = cutout.shape
        need = (cutout.x0, cutout.x0 + nx, cutout.y0, cutout.y0 + ny)
        return self._cached_window(FLAGS_NS, exp.url, need)

    def flag_defs(self, exp: Exposure | None = None) -> dict[str, int]:
        defs = dict(DEFAULT_FLAG_DEFS)
        if exp is not None:
            layout = self.state.layout(exp.hint_key)
            if layout is not None:
                defs.update(flag_definitions(layout.flags_header))
        return defs

    # ------------------------------------------------------------------ prediction
    def _predictable(self, exp: Exposure) -> bool:
        return (self.state.layout(exp.hint_key) is not None
                and self.state.header_guess(exp.hint_key, exp.detector) is not None
                and self.state.template(exp.detector) is not None)

    def _predict_box(self, exp: Exposure, ra, dec, pad: int):
        """Window predicted from the SIA footprint: a box, OUTSIDE, or None (unknown)."""
        template = self.state.template(exp.detector)
        pointing = fit_pointing(template, exp.footprint, (exp.ra_center, exp.dec_center))
        if pointing is None:
            return None
        xs, ys = predict_pixels(template, pointing, ra, dec)
        if not (np.all(np.isfinite(xs)) and np.all(np.isfinite(ys))):
            return None
        n = config.DETECTOR_SIZE
        box = clip_box(math.floor(xs.min()) - pad, math.ceil(xs.max()) + pad + 1,
                       math.floor(ys.min()) - pad, math.ceil(ys.max()) + pad + 1, n, n)
        return box if box is not None else OUTSIDE

    def _header_and_rows(self, exp: Exposure, box):
        """One request: PRIMARY+IMAGE headers and (if *box*) the predicted rows.

        The rows are placed assuming the most common IMAGE header length of
        this detector and version; the header range reaches the longest length
        seen, so a longer-than-usual header is still read in full and only the
        rows have to be fetched again.
        """
        layout = self.state.layout(exp.hint_key)
        guess, longest = self.state.header_guess(exp.hint_key, exp.detector)
        data_offset = layout.primary_bytes + guess
        head = (0, layout.primary_bytes + longest - 1)
        rows = row_ranges(data_offset, config.DETECTOR_SIZE, 4, *box) if box else []
        parts, total = self.client.fetch(exp.url, [head] + rows)
        raw = slice_parts(parts, [head])[0]
        primary = find_header_end(raw, 0)
        image_bytes = find_header_end(raw, primary) if primary else None
        header = None
        if primary == layout.primary_bytes and image_bytes is not None:
            header = parse_header(raw[primary: primary + image_bytes])
            if (data_bytes(header) != layout.image_data_bytes or int(header.get("BITPIX", 0)) != -32
                    or int(header.get("NAXIS1", 0)) != config.DETECTOR_SIZE):
                header = None
        if header is None:                      # not like its siblings: explicit read
            return self._discover(exp, probe=raw, total=total), None
        info = self._make_info(exp, header, primary + image_bytes, total)
        speculative = None
        if rows and image_bytes == guess:
            arr = rows_to_array(slice_parts(parts, rows), np.dtype(">f4"), box[1] - box[0])
            speculative = (box, arr.astype(np.float32))
        return info, speculative

    # ------------------------------------------------------------------ discovery
    def _discover(self, exp: Exposure, probe: bytes | None = None, total: int | None = None) -> FileInfo:
        """Read the headers of one file explicitly and learn from them."""
        url = exp.url
        layout = self.state.layout(exp.hint_key)
        if probe is None:
            parts, total = self.client.fetch(url, [(0, PROBE_BYTES - 1)])
            probe = slice_parts(parts, [(0, PROBE_BYTES - 1)])[0]
        primary = find_header_end(probe, 0)
        if primary is None:
            raise RangeError(f"{url}: no FITS primary header")
        image_bytes = find_header_end(probe, primary)
        while image_bytes is None:
            if len(probe) >= MAX_HEADER_BYTES:
                raise RangeError(f"{url}: IMAGE header has no END card")
            end = max(2 * len(probe), PROBE_BYTES) - 1
            if total:
                end = min(end, total - 1)
            parts, size = self.client.fetch(url, [(len(probe), end)])
            probe += slice_parts(parts, [(len(probe), end)])[0]
            total = total or size
            image_bytes = find_header_end(probe, primary)
        header = parse_header(probe[primary: primary + image_bytes])
        image_data = data_bytes(header)
        flags_offset = primary + image_bytes + padded(image_data)

        flags_bytes = None
        flags_text = None
        same = (layout is not None and layout.primary_bytes == primary and layout.image_data_bytes == image_data)
        if not same:
            raw = self._read_header_at(url, flags_offset, total)
            flags_bytes = len(raw)
            flags_text = header_to_text(parse_header(raw))
            if layout is None:
                self.state.set_layout(exp.hint_key, VersionLayout(primary, image_data, flags_bytes, flags_text))
                flags_text = None
        return self._make_info(exp, header, primary + image_bytes, total, flags_bytes, flags_text, flags_offset)

    def _make_info(self, exp: Exposure, header: fits.Header, image_data_offset: int, total: int | None,
                   flags_bytes: int | None = None, flags_text: str | None = None,
                   flags_offset: int | None = None) -> FileInfo:
        if flags_offset is None:
            flags_offset = image_data_offset + padded(data_bytes(header))
        info = FileInfo(cards=reduce_header(header), nx=int(header["NAXIS1"]), ny=int(header["NAXIS2"]),
                        bitpix=int(header["BITPIX"]), image_data_offset=int(image_data_offset),
                        flags_header_offset=int(flags_offset), flags_header_bytes=flags_bytes,
                        flags_header_text=flags_text, size=total)
        self.state.set_info(exp.url, info)
        layout = self.state.layout(exp.hint_key)
        primary_bytes = layout.primary_bytes if layout is not None else BLOCK
        self.state.note_header_size(exp.hint_key, exp.detector, int(image_data_offset) - primary_bytes)
        if self.state.template(exp.detector) is None:
            template = SipTemplate.from_header(header)
            if template is not None:
                self.state.set_template(exp.detector, template)
        return info

    def _read_header_at(self, url: str, offset: int, total: int | None) -> bytes:
        nblocks = 8
        while True:
            end = offset + nblocks * BLOCK - 1
            if total:
                end = min(end, total - 1)
            raw = self.client.fetch_bytes(url, [(offset, end)])[0]
            hbytes = find_header_end(raw)
            if hbytes is not None:
                return raw[:hbytes]
            if nblocks * BLOCK >= MAX_HEADER_BYTES or (total and end >= total - 1):
                raise RangeError(f"{url}: header at byte {offset} has no END card")
            nblocks *= 2

    # ------------------------------------------------------------------ pixels
    def _read_image_rows(self, url: str, info: FileInfo, win) -> np.ndarray:
        bpp = abs(info.bitpix) // 8
        rows = row_ranges(info.image_data_offset, info.nx, bpp, *win)
        blobs = self.client.fetch_bytes(url, rows)
        arr = rows_to_array(blobs, bitpix_dtype(info.bitpix), win[1] - win[0])
        return np.asarray(arr, dtype=np.float32)

    def flags_window(self, exp: Exposure, info: FileInfo, win) -> np.ndarray:
        """FLAGS pixels of a window as uint32 (reads the archive if not cached)."""
        cached = self._cached_window(FLAGS_NS, exp.url, win)
        if cached is not None:
            return cached
        want = expand_box(win, config.CUTOUT_MARGIN_PX, info.nx, info.ny)
        layout = self.state.layout(exp.hint_key)
        header = None
        if info.flags_header_text:
            header = header_from_text(info.flags_header_text)
        elif layout is not None:
            header = layout.flags_header
        hbytes = info.flags_header_bytes or (layout.flags_header_bytes if layout is not None else None)

        arr = None
        if header is not None and hbytes:
            try:
                arr = self._read_flag_rows(exp.url, header, info.flags_header_offset + hbytes, want,
                                           verify=info.flags_header_bytes is None)
            except LayoutMismatch:
                arr = None
            else:
                if info.flags_header_bytes is None:
                    info.flags_header_bytes = int(hbytes)
                    self.state.set_info(exp.url, info)
        if arr is None:                         # this file differs from its siblings
            raw = self._read_header_at(exp.url, info.flags_header_offset, info.size)
            header = parse_header(raw)
            info.flags_header_bytes = len(raw)
            info.flags_header_text = header_to_text(header)
            self.state.set_info(exp.url, info)
            arr = self._read_flag_rows(exp.url, header, info.flags_header_offset + len(raw), want, verify=False)
        self._store_window(FLAGS_NS, exp.url, want, arr, compressed=True)
        return crop(arr, want, win)

    def _read_flag_rows(self, url: str, header: fits.Header, data_offset: int, win, verify: bool) -> np.ndarray:
        x0, x1, y0, y1 = win
        check = (data_offset - BLOCK, data_offset - 1)
        extra = [check] if verify else []

        def verified(parts) -> None:
            if verify and find_header_end(slice_parts(parts, [check])[0]) != BLOCK:
                raise LayoutMismatch(f"{url}: FLAGS header is not where its siblings have it")

        if is_compressed(header):
            plan = tile_plan(header, data_offset, x0, x1, y0, y1)
            parts, _ = self.client.fetch(url, extra + plan["desc_ranges"])
            verified(parts)
            tiles = tile_locations(plan, slice_parts(parts, plan["desc_ranges"]))
            heap_parts, _ = self.client.fetch(url, [rng for rng, _ in tiles])
            arr = assemble_tiles(plan, tiles, heap_parts, x0, x1, y0, y1)
        else:
            bitpix = int(header["BITPIX"])
            rows = row_ranges(data_offset, int(header["NAXIS1"]), abs(bitpix) // 8, x0, x1, y0, y1)
            parts, _ = self.client.fetch(url, extra + rows)
            verified(parts)
            arr = apply_scaling(rows_to_array(slice_parts(parts, rows), bitpix_dtype(bitpix), x1 - x0), header)
        return np.asarray(arr).astype(np.int64).astype(np.uint32)

    # ------------------------------------------------------------------ wavelengths
    def wave(self, exp: Exposure) -> WaveLookup:
        """Wavelength lookup of the exposure's detector (shared per pipeline version)."""
        key = f"{exp.hint_key}/D{exp.detector}"
        found = self.state.wave(key)
        if found is not None:
            return found
        with self.state.lock(("wave", key)):
            found = self.state.wave(key)
            if found is not None:
                return found
            _start, tail, _total = self.client.fetch_suffix(exp.url, 6 * BLOCK)
            # WCS-WAVE is the last HDU: scan block boundaries backwards for its header.
            usable = len(tail) - (len(tail) % BLOCK)
            tail = tail[len(tail) - usable:]
            for pos in range(len(tail) - BLOCK, -1, -BLOCK):
                if not tail[pos: pos + 9] == b"XTENSION=":
                    continue
                hbytes = find_header_end(tail, pos)
                if hbytes is None:
                    continue
                header = parse_header(tail[pos: pos + hbytes])
                if str(header.get("EXTNAME", "")).strip() != "WCS-WAVE":
                    break
                table = fits.BinTableHDU.fromstring(tail[pos:])
                row = table.data[0]
                wave = WaveLookup(np.asarray(row["X"]), np.asarray(row["Y"]), np.asarray(row["VALUES"]))
                self.state.set_wave(key, wave)
                return wave
            raise RangeError(f"{exp.url}: WCS-WAVE table not found at the end of the file")

    # ------------------------------------------------------------------ window cache
    def _held_window(self, namespace: str, url: str):
        """``(window, pixels)`` kept on disk for a file, or None."""
        data = self.cache.get_arrays(namespace, url)
        if not data or "win" not in data or "data" not in data:
            return None
        return tuple(int(v) for v in data["win"]), data["data"]

    def _cached_window(self, namespace: str, url: str, need) -> np.ndarray | None:
        held = self._held_window(namespace, url)
        if held is None or not box_covers(held[0], need):
            return None
        return crop(held[1], held[0], need)

    def _store_window(self, namespace: str, url: str, win, arr: np.ndarray, compressed: bool = False) -> None:
        self.cache.put_arrays(namespace, url, {"data": arr, "win": np.asarray(win, dtype=np.int32)},
                              compressed=compressed)
