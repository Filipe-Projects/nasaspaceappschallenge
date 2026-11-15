"""Find SPHEREx spectral images covering a sky position (IRSA SIA2 service).

The IRSA Simple Image Access service returns one row per Level 2 spectral
image (one exposure of one of the six detector arrays) whose footprint
intersects the search circle.  Every row carries the on-premises access URL,
the AWS S3 key, the observation time and the wavelength range of the
detector, which is all the viewer needs to plan its range requests.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from . import config, snapshots
from .cache import default_cache
from .http import TransientHTTPError, fetch

_VERSION_RE = re.compile(r"_spx_l2b[a-z_]*-v(\d+)", re.I)
_DETECTOR_RE = re.compile(r"D(\d)")

MJD_EPOCH = datetime(1858, 11, 17)


def mjd_to_datetime(mjd: float) -> datetime:
    return MJD_EPOCH + timedelta(days=float(mjd))


def mjd_to_iso(mjd: float, seconds: bool = False) -> str:
    fmt = "%Y-%m-%dT%H:%M:%S" if seconds else "%Y-%m-%d"
    return mjd_to_datetime(mjd).strftime(fmt)


def mjd_to_year(mjd: float) -> float:
    """Decimal (Julian) year, good enough for proper-motion propagation."""
    return 2000.0 + (float(mjd) - 51544.5) / 365.25


@dataclass
class Exposure:
    obs_id: str
    detector: int
    collection: str
    url: str
    mjd_start: float
    mjd_end: float
    exptime: float
    em_min_um: float
    em_max_um: float
    version: str
    ra_center: float
    dec_center: float
    footprint: list = field(default_factory=list)
    s3_key: str | None = None

    @property
    def mjd(self) -> float:
        return 0.5 * (self.mjd_start + self.mjd_end)

    @property
    def key(self) -> str:
        return f"{self.obs_id}/D{self.detector}"

    @property
    def band(self) -> str:
        return f"D{self.detector}"

    @property
    def date_iso(self) -> str:
        return mjd_to_iso(self.mjd, seconds=True)

    @property
    def hint_key(self) -> str:
        """Files written by one pipeline version share their HDU layout
        (the deep-field collections come from the same pipeline)."""
        return f"{self.collection.replace('_deep', '')}/{self.version}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["mjd"] = self.mjd
        data["date"] = self.date_iso
        data["band"] = self.band
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Exposure":
        fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**fields)


def _parse_footprint(region: str) -> list[list[float]]:
    tokens = region.replace(",", " ").split()
    nums = []
    for tok in tokens:
        try:
            nums.append(float(tok))
        except ValueError:
            continue
    return [[nums[i], nums[i + 1]] for i in range(0, len(nums) - 1, 2)]


def _parse_row(row: dict, collection: str) -> Exposure | None:
    try:
        if row.get("dataproduct_type", "image") != "image":
            return None
        url = row["access_url"].strip()
        if not url:
            return None
        band = row.get("energy_bandpassname", "")
        m = _DETECTOR_RE.search(band) or _DETECTOR_RE.search(url.rsplit("/", 1)[-1].split("_spx_")[0][-2:])
        if not m:
            return None
        detector = int(m.group(1))
        version_match = _VERSION_RE.search(url)
        version = f"v{version_match.group(1)}" if version_match else "v?"
        s3_key = None
        cloud = row.get("cloud_access") or ""
        if cloud.strip():
            try:
                s3_key = json.loads(cloud).get("aws", {}).get("key")
            except ValueError:
                s3_key = None
        return Exposure(
            obs_id=row["obs_id"].strip(),
            detector=detector,
            collection=collection,
            url=url,
            mjd_start=float(row["t_min"]),
            mjd_end=float(row["t_max"]),
            exptime=float(row.get("t_exptime") or 0.0),
            em_min_um=float(row["em_min"]) * 1e6,
            em_max_um=float(row["em_max"]) * 1e6,
            version=version,
            ra_center=float(row["s_ra"]),
            dec_center=float(row["s_dec"]),
            footprint=_parse_footprint(row.get("s_region", "")),
            s3_key=s3_key,
        )
    except (KeyError, ValueError):
        return None


def query_collection(collection: str, ra: float, dec: float, radius_deg: float,
                     timeout: float | None = None) -> list[Exposure]:
    """One SIA2 cone query, CSV output (smaller and faster to parse than VOTable).

    A quick refusal (IRSA answers "502" for a moment now and then) is tried
    once more; a query that ran into the timeout is not, because the second
    wait would be just as long.
    """
    params = {
        "COLLECTION": collection,
        "POS": f"circle {ra:.6f} {dec:.6f} {radius_deg:.5f}",
        "RESPONSEFORMAT": "csv",
    }
    url = config.SIA_URL + "?" + urlencode(params, quote_via=quote)

    started = time.time()
    try:
        resp = fetch("GET", url, what=f"SIA {collection}", timeout=timeout or config.SIA_TIMEOUT, retries=0)
    except TransientHTTPError:
        if time.time() - started > 20.0:
            raise
        time.sleep(2.0)
        resp = fetch("GET", url, what=f"SIA {collection}", timeout=timeout or config.SIA_TIMEOUT, retries=0)
    resp.raise_for_status()
    text = resp.text
    if not text.lstrip().startswith("s_ra") and "access_url" not in text[:2000]:
        raise RuntimeError(f"SIA {collection}: unexpected answer")
    exposures = []
    for row in csv.DictReader(io.StringIO(text)):
        exp = _parse_row(row, collection)
        if exp is not None:
            exposures.append(exp)
    return exposures


# Fields up to 15′ wide are searched with one circle around their centre. IRSA takes as long
# for a small circle as for this one, and every narrower request at that centre (another field
# of view, single exposures, a spectrum) is then cut from the same answer without asking again.
SHARED_RADIUS_DEG = 0.19

_refreshing: set[str] = set()
_refresh_lock = threading.Lock()
# One search per circle at a time: a second request for the same circle waits for the first answer.
_flights: dict[str, threading.Lock] = {}
_flights_guard = threading.Lock()


def _flight(key: str) -> threading.Lock:
    with _flights_guard:
        return _flights.setdefault(key, threading.Lock())


def _query_all(collections: list[str], ra: float, dec: float, radius_deg: float,
               timeout: float | None = None) -> tuple[list[Exposure], dict]:
    """Every collection in parallel: the merged exposures and ``{collection: exception}``."""
    def safe_query(collection: str):
        try:
            return query_collection(collection, ra, dec, radius_deg, timeout)
        except Exception as exc:  # noqa: BLE001 - reported by the caller
            return exc

    with ThreadPoolExecutor(max_workers=max(1, len(collections))) as pool:
        results = list(pool.map(safe_query, collections))
    failed = {name: r for name, r in zip(collections, results) if isinstance(r, Exception)}
    seen: dict[str, Exposure] = {}
    for exposures in results:
        if not isinstance(exposures, Exception):
            for exp in exposures:
                seen.setdefault(exp.key, exp)
    return sorted(seen.values(), key=lambda e: (e.mjd_start, e.detector)), failed


def _refresh_later(key: str, collections: list[str], ra: float, dec: float, radius_deg: float) -> None:
    """Renew an old cached search in the background; one refresh per search at a time."""
    with _refresh_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def work() -> None:
        try:
            merged, failed = _query_all(collections, ra, dec, radius_deg)
            if not failed:
                default_cache().put_json("sia", key, [asdict(e) for e in merged])
        finally:
            with _refresh_lock:
                _refreshing.discard(key)

    threading.Thread(target=work, name="sia-refresh", daemon=True).start()


def _cache_key(collections: list[str], ra: float, dec: float, radius_deg: float) -> str:
    return f"{'|'.join(collections)}|{ra:.5f}|{dec:.5f}|{radius_deg:.4f}"


def touches(exp: Exposure, ra: float, dec: float, radius_deg: float) -> bool:
    """Whether the footprint of *exp* reaches the circle, as IRSA's own search decides.

    The footprint is projected on the plane tangent at the centre of the circle,
    where great circles are straight lines and the distance from the centre is the
    tangent of the angle.  An exposure without a usable footprint is kept.
    """
    if len(exp.footprint) < 3:
        return True
    ra0, dec0 = math.radians(ra), math.radians(dec)
    sin0, cos0 = math.sin(dec0), math.cos(dec0)
    corners = []
    for p_ra, p_dec in exp.footprint:
        a, d = math.radians(p_ra) - ra0, math.radians(p_dec)
        cosc = sin0 * math.sin(d) + cos0 * math.cos(d) * math.cos(a)
        if cosc < 0.1:                                 # a corner far round the sky: nowhere near
            return False
        corners.append((math.cos(d) * math.sin(a) / cosc,
                        (cos0 * math.sin(d) - sin0 * math.cos(d) * math.cos(a)) / cosc))
    edges = list(zip(corners, corners[1:] + corners[:1]))
    inside = False
    for (x1, y1), (x2, y2) in edges:                   # the centre inside the footprint
        if (y1 > 0) != (y2 > 0) and x1 - y1 * (x2 - x1) / (y2 - y1) > 0:
            inside = not inside
    if inside:
        return True
    reach = math.tan(math.radians(radius_deg))
    for (x1, y1), (x2, y2) in edges:                   # or an edge within the radius
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else min(1.0, max(0.0, -(x1 * dx + y1 * dy) / length2))
        if math.hypot(x1 + t * dx, y1 + t * dy) <= reach:
            return True
    return False


def _answer(collections: list[str], ra: float, dec: float, radius_deg: float, ask: bool, stored: bool = True,
            failures: dict | None = None, timeout: float | None = None) -> list[Exposure] | None:
    """Exposures reaching the circle, or None when that needs IRSA and *ask* is false.

    A stored answer that is more than a day old, or that comes from the copies
    saved with the package, is returned at once and renewed in the background.
    """
    wide = max(radius_deg, SHARED_RADIUS_DEG)
    key = _cache_key(collections, ra, dec, wide)
    cache = default_cache()
    rows = cache.get_json("sia", key, ttl=config.SIA_CACHE_TTL)
    if rows is None and stored:
        older = cache.get_json("sia", _cache_key(collections, ra, dec, radius_deg)) if wide > radius_deg else None
        if older is not None:                          # searched with the field's own circle, before it was shared
            _refresh_later(key, collections, ra, dec, wide)
            return [Exposure.from_dict(d) for d in older]
        rows = cache.get_json("sia", key)
        if rows is None:
            rows = snapshots.search(ra, dec, wide)
        if rows is not None:
            _refresh_later(key, collections, ra, dec, wide)
    if rows is None:
        if not ask:
            return None
        with _flight(key):
            rows = cache.get_json("sia", key, ttl=config.SIA_CACHE_TTL)       # answered while this one waited
            if rows is None:
                merged, failed = _query_all(collections, ra, dec, wide, timeout)
                if failed:
                    if len(failed) == len(collections):
                        raise next(iter(failed.values()))
                    if failures is not None:
                        failures.update({name: f"{type(exc).__name__}: {exc}" for name, exc in failed.items()})
                else:
                    cache.put_json("sia", key, [asdict(e) for e in merged])
                rows = [asdict(e) for e in merged]
    exposures = [Exposure.from_dict(d) for d in rows]
    return exposures if wide == radius_deg else [e for e in exposures if touches(e, ra, dec, radius_deg)]


def cached_exposures(ra: float, dec: float, radius_deg: float) -> list[Exposure] | None:
    """The answer :func:`find_exposures` would give without asking IRSA, or None."""
    from .releases import active_collections
    return _answer(list(active_collections()), ra, dec, radius_deg, ask=False)


def find_exposures(ra: float, dec: float, radius_deg: float,
                   collections: list[str] | None = None,
                   stored: bool = True, failures: dict | None = None,
                   timeout: float | None = None) -> list[Exposure]:
    """All SPHEREx exposures intersecting the circle, sorted by time.

    Duplicate (obs_id, detector) pairs across collections keep the entry from
    the collection listed first in ``config.COLLECTIONS``.  Results are cached
    for a day (new data arrive weekly).  An older cached answer, or the copy
    saved with the package for a test target, is still returned at once and
    renewed in the background for the next request: a field that has been seen
    before never waits for the search, and keeps working while IRSA is out of
    reach.  With *stored* false only a fresh answer will do (the disk cache of
    the last day, or IRSA).

    A collection that cannot be searched leaves the answer incomplete (an
    epoch may be missing).  That is never cached, and when the caller passes a
    *failures* dict it is filled with ``{collection: error}`` so the gap can be
    reported instead of passing unnoticed.
    """
    if not collections:
        from .releases import active_collections
        collections = active_collections()
    return _answer(list(collections), ra, dec, radius_deg, ask=True, stored=stored,
                   failures=failures, timeout=timeout)
