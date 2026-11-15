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
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from . import config
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


_refreshing: set[str] = set()
_refresh_lock = threading.Lock()


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


def search_is_cached(ra: float, dec: float, radius_deg: float) -> bool:
    """True when this search can be answered without asking IRSA."""
    from .releases import active_collections
    path = default_cache().path("sia", _cache_key(list(active_collections()), ra, dec, radius_deg), ".json")
    return path.exists()


def cached_exposures(ra: float, dec: float, radius_deg: float) -> list[Exposure] | None:
    """The answer :func:`find_exposures` would give from the cache, or None; never waits on IRSA.

    An old answer is renewed in the background, as in :func:`find_exposures`.
    """
    from .releases import active_collections
    collections = list(active_collections())
    cache = default_cache()
    key = _cache_key(collections, ra, dec, radius_deg)
    data = cache.get_json("sia", key, ttl=config.SIA_CACHE_TTL)
    if data is None:
        data = cache.get_json("sia", key)
        if data is None:
            return None
        _refresh_later(key, collections, ra, dec, radius_deg)
    return [Exposure.from_dict(d) for d in data]


def find_exposures(ra: float, dec: float, radius_deg: float,
                   collections: list[str] | None = None,
                   use_cache: bool = True, failures: dict | None = None,
                   timeout: float | None = None) -> list[Exposure]:
    """All SPHEREx exposures intersecting the circle, sorted by time.

    Duplicate (obs_id, detector) pairs across collections keep the entry from
    the collection listed first in ``config.COLLECTIONS``.  Results are cached
    for a day (new data arrive weekly).  An older cached answer is still
    returned at once, and renewed in the background for the next request: a
    field that has been seen before never waits for the search, and keeps
    working while IRSA is out of reach.

    A collection that cannot be searched leaves the answer incomplete (an
    epoch may be missing).  That is never cached, and when the caller passes a
    *failures* dict it is filled with ``{collection: error}`` so the gap can be
    reported instead of passing unnoticed.
    """
    if not collections:
        from .releases import active_collections
        collections = active_collections()
    collections = list(collections)
    cache = default_cache()
    key = _cache_key(collections, ra, dec, radius_deg)
    if use_cache:
        cached = cache.get_json("sia", key, ttl=config.SIA_CACHE_TTL)
        if cached is not None:
            return [Exposure.from_dict(d) for d in cached]
        stale = cache.get_json("sia", key)
        if stale is not None:
            _refresh_later(key, collections, ra, dec, radius_deg)
            return [Exposure.from_dict(d) for d in stale]

    merged, failed = _query_all(collections, ra, dec, radius_deg, timeout)
    if failed:
        if len(failed) == len(collections):
            raise next(iter(failed.values()))
        if failures is not None:
            failures.update({name: f"{type(exc).__name__}: {exc}" for name, exc in failed.items()})
    if not failed:
        cache.put_json("sia", key, [asdict(e) for e in merged])
    return merged
