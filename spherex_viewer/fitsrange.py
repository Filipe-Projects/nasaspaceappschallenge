"""Low-level helpers: HTTP byte ranges and FITS file structure.

SPHEREx Level 2 spectral images are 60-70 MB multi-extension FITS files.  A
cutout of a few arcminutes needs a few tens of kilobytes of them, so the
viewer never downloads whole files: it asks the archive for exactly the bytes
it needs with HTTP ``Range`` requests.  IRSA's image server answers
multi-range requests (``multipart/byteranges``) with at most 100 ranges; with
more it silently returns the whole file, which this module detects before
reading the body.
"""
from __future__ import annotations

import bisect
import re
import threading
import warnings
from dataclasses import dataclass

import numpy as np
from astropy.io import fits

from . import config
from .http import fetch

BLOCK = 2880


class RangeError(RuntimeError):
    """The server did not return the bytes we asked for."""


class FullBodyError(RangeError):
    """The server ignored the Range header (too many ranges)."""


class LayoutMismatch(RangeError):
    """A file does not have the layout predicted from its siblings."""


# --------------------------------------------------------------------------
# Byte ranges
# --------------------------------------------------------------------------
def merge_ranges(ranges) -> list[tuple[int, int]]:
    """Sort, de-duplicate and merge touching/overlapping inclusive ranges."""
    out: list[tuple[int, int]] = []
    for start, end in sorted({(int(s), int(e)) for s, e in ranges}):
        if out and start <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


_BOUNDARY_RE = re.compile(r'boundary="?([^";]+)"?', re.I)
_CRANGE_RE = re.compile(rb"content-range:\s*bytes\s+(\d+)-(\d+)/(\d+|\*)", re.I)
_CRANGE_HDR_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.I)


def parse_multipart(body: bytes, boundary: str) -> list[tuple[int, int, bytes]]:
    """Split a multipart/byteranges body into (start, end, bytes) parts."""
    delimiter = b"--" + boundary.encode("ascii")
    parts = []
    for chunk in body.split(delimiter):
        if not chunk or chunk.startswith(b"--"):
            continue
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        match = _CRANGE_RE.search(head)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        parts.append((start, end, data[: end - start + 1]))
    return parts


def slice_parts(parts: list[tuple[int, int, bytes]], ranges) -> list[bytes]:
    """Extract each requested inclusive range from the fetched parts."""
    parts = sorted(parts, key=lambda p: p[0])
    starts = [p[0] for p in parts]
    out = []
    for start, end in ranges:
        idx = bisect.bisect_right(starts, start) - 1
        if idx < 0:
            raise RangeError(f"bytes {start}-{end} missing from response")
        pstart, pend, data = parts[idx]
        if pend < end or len(data) < end - pstart + 1:
            raise RangeError(f"bytes {start}-{end} missing from response")
        out.append(data[start - pstart: end - pstart + 1])
    return out


def covered(parts, rng) -> bool:
    start, end = rng
    return any(ps <= start and pe >= end for ps, pe, _ in parts)


@dataclass
class TransferStats:
    """Requests and bytes spent on the archive (shared by a job's threads)."""
    requests: int = 0
    bytes: int = 0

    def __post_init__(self):
        self._lock = threading.Lock()

    def add(self, nbytes: int) -> None:
        with self._lock:
            self.requests += 1
            self.bytes += int(nbytes)


class RangeClient:
    """Fetch byte ranges of a URL, batching many ranges per request."""

    def __init__(self, max_ranges: int | None = None, stats: TransferStats | None = None):
        self.max_ranges = max_ranges or config.MAX_RANGES_PER_REQUEST
        self.stats = stats or TransferStats()

    def fetch(self, url: str, ranges) -> tuple[list[tuple[int, int, bytes]], int | None]:
        """Return ``(parts, total_file_size)`` for the given inclusive ranges."""
        merged = merge_ranges(ranges)
        parts: list[tuple[int, int, bytes]] = []
        total = None
        batch = self.max_ranges
        i = 0
        while i < len(merged):
            chunk = merged[i: i + batch]
            try:
                got, size = self._request(url, "bytes=" + ",".join(f"{s}-{e}" for s, e in chunk), len(chunk))
            except FullBodyError:
                if batch <= 4:
                    raise
                batch = max(4, batch // 2)
                continue
            parts.extend(got)
            total = size if size is not None else total
            i += len(chunk)
        return parts, total

    def fetch_bytes(self, url: str, ranges) -> list[bytes]:
        parts, _ = self.fetch(url, ranges)
        return slice_parts(parts, ranges)

    def fetch_suffix(self, url: str, nbytes: int) -> tuple[int, bytes, int | None]:
        """Last *nbytes* of the file: ``(start_offset, data, total_size)``."""
        parts, total = self._request(url, f"bytes=-{int(nbytes)}", 1)
        start, _end, data = parts[0]
        return start, data, total

    def _request(self, url: str, range_header: str, nranges: int):
        name = url.rsplit("/", 1)[-1]

        resp = fetch("GET", url, headers={"Range": range_header}, what=f"range GET {name}",
                     read_if=lambda status: status == 206)
        if resp.status_code == 200:
            raise FullBodyError(f"{name}: server ignored the Range header ({nranges} ranges)")
        if resp.status_code != 206:
            raise RangeError(f"{name}: HTTP {resp.status_code} for a range request")
        ctype = resp.headers.get("Content-Type", "")
        crange = resp.headers.get("Content-Range")
        body = resp.content
        self.stats.add(len(body))

        if "multipart/byteranges" in ctype.lower():
            match = _BOUNDARY_RE.search(ctype)
            if not match:
                raise RangeError(f"{name}: multipart response without boundary")
            parts = parse_multipart(body, match.group(1))
            if not parts:
                raise RangeError(f"{name}: empty multipart response")
            total = None
            first = _CRANGE_RE.search(body[:4096])
            if first and first.group(3) != b"*":
                total = int(first.group(3))
            return parts, total
        if crange:
            match = _CRANGE_HDR_RE.match(crange.strip())
            if not match:
                raise RangeError(f"{name}: bad Content-Range {crange!r}")
            start, end = int(match.group(1)), int(match.group(2))
            total = int(match.group(3)) if match.group(3) != "*" else None
            return [(start, end, body[: end - start + 1])], total
        raise RangeError(f"{name}: 206 without Content-Range")


# --------------------------------------------------------------------------
# FITS structure
# --------------------------------------------------------------------------
def find_header_end(raw: bytes, offset: int = 0) -> int | None:
    """Header length in bytes if its END card lies inside *raw*, else None."""
    pos = offset
    while pos + BLOCK <= len(raw):
        block = raw[pos: pos + BLOCK]
        for i in range(0, BLOCK, 80):
            card = block[i: i + 80]
            if card[:3] == b"END" and card[3:].strip() == b"":
                return pos + BLOCK - offset
        pos += BLOCK
    return None


def parse_header(raw: bytes) -> fits.Header:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fits.Header.fromstring(raw.decode("latin-1"))


def header_to_text(header: fits.Header) -> str:
    return header.tostring(sep="\n", endcard=True, padding=False)


def header_from_text(text: str) -> fits.Header:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fits.Header.fromstring(text, sep="\n")


def data_bytes(header: fits.Header) -> int:
    naxis = int(header.get("NAXIS", 0))
    if naxis == 0:
        return 0
    count = 1
    for i in range(1, naxis + 1):
        count *= int(header[f"NAXIS{i}"])
    bitpix = abs(int(header["BITPIX"])) // 8
    gcount = int(header.get("GCOUNT", 1))
    pcount = int(header.get("PCOUNT", 0))
    return bitpix * gcount * (pcount + count)


def padded(nbytes: int) -> int:
    return ((nbytes + BLOCK - 1) // BLOCK) * BLOCK


def bitpix_dtype(bitpix: int) -> np.dtype:
    return np.dtype({8: "u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}[int(bitpix)])


def apply_scaling(arr: np.ndarray, header: fits.Header) -> np.ndarray:
    """Apply BSCALE/BZERO and return a native-endian array."""
    bzero = float(header.get("BZERO", 0.0))
    bscale = float(header.get("BSCALE", 1.0))
    arr = arr.astype(arr.dtype.newbyteorder("="))
    if bscale == 1.0 and bzero == 0.0:
        return arr
    if arr.dtype.kind in "iu" and bscale == 1.0 and float(bzero).is_integer():
        width = arr.dtype.itemsize
        if bzero == 2 ** (8 * width - 1):       # unsigned integer convention
            return (arr.astype(np.int64) + int(bzero)).astype(np.dtype(f"u{width}"))
        return arr.astype(np.int64) + int(bzero)
    return arr.astype(np.float64) * bscale + bzero


def is_compressed(header: fits.Header) -> bool:
    return bool(header.get("ZIMAGE", False)) and str(header.get("XTENSION", "")).strip() == "BINTABLE"


def image_shape(header: fits.Header) -> tuple[int, int]:
    """(ny, nx) of an image HDU, tile-compressed or not."""
    if is_compressed(header):
        return int(header["ZNAXIS2"]), int(header["ZNAXIS1"])
    return int(header["NAXIS2"]), int(header["NAXIS1"])


def row_ranges(data_offset: int, width: int, bytes_per_pixel: int,
               x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int]]:
    """Byte ranges of the window [y0:y1, x0:x1] of an uncompressed image."""
    row_bytes = (x1 - x0) * bytes_per_pixel
    out = []
    for row in range(y0, y1):
        start = data_offset + (row * width + x0) * bytes_per_pixel
        out.append((start, start + row_bytes - 1))
    return out


def rows_to_array(blobs: list[bytes], dtype: np.dtype, nx: int) -> np.ndarray:
    arr = np.empty((len(blobs), nx), dtype=dtype)
    for r, blob in enumerate(blobs):
        arr[r] = np.frombuffer(blob, dtype=dtype, count=nx)
    return arr


# --------------------------------------------------------------------------
# Tile-compressed images (fpack convention: one binary-table row per tile)
# --------------------------------------------------------------------------
_TFORM_RE = re.compile(r"^(\d*)([PQ]?)([LXBIJKAEDCM])(?:\((\d+)\))?$")
_TFORM_SIZE = {"L": 1, "X": 1, "B": 1, "I": 2, "J": 4, "K": 8, "A": 1, "E": 4, "D": 8, "C": 8, "M": 16}


def table_columns(header: fits.Header) -> dict[str, tuple[int, str, int]]:
    """name -> (byte offset in row, code letter or P/Q, repeat) of a binary table."""
    cols = {}
    offset = 0
    for i in range(1, int(header.get("TFIELDS", 0)) + 1):
        tform = str(header[f"TFORM{i}"]).strip()
        match = _TFORM_RE.match(tform)
        if not match:
            raise RangeError(f"unsupported TFORM {tform!r}")
        repeat = int(match.group(1) or 1)
        pointer = match.group(2)
        code = match.group(3)
        if pointer == "P":
            width, code_out = 8 * repeat, "P"
        elif pointer == "Q":
            width, code_out = 16 * repeat, "Q"
        else:
            width, code_out = _TFORM_SIZE[code] * repeat, code
        cols[str(header[f"TTYPE{i}"]).strip()] = (offset, code_out, repeat)
        offset += width
    return cols


def decode_tile(blob: bytes, header: fits.Header, npixels: int) -> np.ndarray:
    """Decompress one tile of a tile-compressed image HDU."""
    from astropy.io.fits.hdu.compressed._codecs import Gzip1, Gzip2, NoCompress, Rice1

    ctype = str(header.get("ZCMPTYPE", "RICE_1")).strip().upper()
    zbitpix = int(header["ZBITPIX"])
    settings = {}
    for i in range(1, 10):
        if f"ZNAME{i}" in header:
            settings[str(header[f"ZNAME{i}"]).strip().upper()] = header[f"ZVAL{i}"]
    buf = np.frombuffer(blob, dtype=np.uint8)
    native = bitpix_dtype(zbitpix).newbyteorder("=")
    if ctype == "RICE_1":
        bytepix = int(settings.get("BYTEPIX", abs(zbitpix) // 8))
        codec = Rice1(blocksize=int(settings.get("BLOCKSIZE", 32)), bytepix=bytepix, tilesize=npixels)
        return np.asarray(codec.decode(buf))
    if ctype == "GZIP_1":
        raw = np.asarray(Gzip1().decode(buf)).tobytes()
    elif ctype == "GZIP_2":
        raw = np.asarray(Gzip2(itemsize=abs(zbitpix) // 8).decode(buf)).tobytes()
    elif ctype == "NOCOMPRESS":
        raw = np.asarray(NoCompress().decode(buf)).tobytes()
    else:
        raise RangeError(f"unsupported tile compression {ctype!r}")
    return np.frombuffer(raw, dtype=bitpix_dtype(zbitpix)).astype(native)


def tile_plan(header: fits.Header, data_offset: int, x0: int, x1: int, y0: int, y1: int) -> dict:
    """Which tile descriptors to read for the window of a compressed image."""
    ny, nx = image_shape(header)
    tile_x = int(header.get("ZTILE1", nx))
    tile_y = int(header.get("ZTILE2", 1))
    ntx = -(-nx // tile_x)
    cols = table_columns(header)
    if "COMPRESSED_DATA" not in cols:
        raise RangeError("compressed image without a COMPRESSED_DATA column")
    col_offset, code, _ = cols["COMPRESSED_DATA"]
    desc_size = 8 if code == "P" else 16
    row_size = int(header["NAXIS1"])
    tile_ids = [(tx, ty)
                for ty in range(y0 // tile_y, (y1 - 1) // tile_y + 1)
                for tx in range(x0 // tile_x, (x1 - 1) // tile_x + 1)]
    desc_ranges = []
    for tx, ty in tile_ids:
        start = data_offset + (ty * ntx + tx) * row_size + col_offset
        desc_ranges.append((start, start + desc_size - 1))
    heap_start = data_offset + int(header.get("THEAP", row_size * int(header["NAXIS2"])))
    return {"header": header, "tile_x": tile_x, "tile_y": tile_y, "nx": nx, "ny": ny,
            "tile_ids": tile_ids, "desc_ranges": desc_ranges, "desc_code": code,
            "heap_start": heap_start}


def tile_locations(plan: dict, descriptors: list[bytes]):
    """Heap byte range of every tile in the plan: ``[((start, end), (tx, ty)), ...]``."""
    dtype = ">i4" if plan["desc_code"] == "P" else ">i8"
    tiles = []
    for (tx, ty), blob in zip(plan["tile_ids"], descriptors):
        nelem, offset = (int(v) for v in np.frombuffer(blob, dtype=dtype)[:2])
        if nelem <= 0 or offset < 0:
            raise LayoutMismatch(f"implausible tile descriptor ({nelem}, {offset})")
        start = plan["heap_start"] + offset
        tiles.append(((start, start + nelem - 1), (tx, ty)))
    return tiles


def assemble_tiles(plan: dict, tiles, heap_parts, x0: int, x1: int, y0: int, y1: int) -> np.ndarray:
    header = plan["header"]
    tile_x, tile_y, nx, ny = plan["tile_x"], plan["tile_y"], plan["nx"], plan["ny"]
    out = None
    for rng, (tx, ty) in tiles:
        blob = slice_parts(heap_parts, [rng])[0]
        w = min(tile_x, nx - tx * tile_x)
        h = min(tile_y, ny - ty * tile_y)
        try:
            tile = decode_tile(blob, header, w * h)
        except Exception as exc:  # noqa: BLE001 - corrupt/misaligned tile
            raise LayoutMismatch(f"cannot decode tile ({tx},{ty}): {exc}") from exc
        if tile.size != w * h:
            raise LayoutMismatch(f"tile ({tx},{ty}) has {tile.size} pixels, expected {w * h}")
        tile = tile.reshape(h, w)
        if out is None:
            out = np.zeros((y1 - y0, x1 - x0), dtype=tile.dtype)
        ty0, tx0 = ty * tile_y, tx * tile_x
        oy0, oy1 = max(y0, ty0), min(y1, ty0 + h)
        ox0, ox1 = max(x0, tx0), min(x1, tx0 + w)
        out[oy0 - y0: oy1 - y0, ox0 - x0: ox1 - x0] = tile[oy0 - ty0: oy1 - ty0, ox0 - tx0: ox1 - tx0]
    if out is None:
        raise RangeError("no tiles read")
    scaling = fits.Header()
    for key in ("BZERO", "BSCALE"):
        if key in header:
            scaling[key] = header[key]
    return apply_scaling(out, scaling)
