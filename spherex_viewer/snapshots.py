"""Saved answers for the test targets, served when IRSA cannot be reached.

The viewer reads everything live from IRSA.  When the archive is down, a field
that is not in the disk cache cannot be shown at all, and the five test targets
listed under the search box are the first thing a visitor tries.  ``scripts/snapshot.py``
therefore stores the finished frame set, the target spectrum and the image
search of each of them in this package (gzip JSON, a few megabytes in total).  A snapshot is shown
while the same field is being read from IRSA for the first time, and whenever
that fails; the answer carries the date it was saved, and the viewer shows it.
"""
from __future__ import annotations

import gzip
import json
import math
import threading
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent / "snapshots"
# The field centre follows the target in quarter-year steps, so a snapshot may be
# a fraction of a pixel away from the centre asked for.
FIELD_TOLERANCE_ARCSEC = 8.0
POSITION_TOLERANCE_ARCSEC = 1.0


def _separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    cosd = math.cos(math.radians(0.5 * (dec1 + dec2)))
    dra = ((ra1 - ra2 + 180.0) % 360.0 - 180.0) * cosd
    return math.hypot(dra, dec1 - dec2) * 3600.0


_index_cache: dict = {"mtime": None, "entries": []}


def _index() -> list[dict]:
    """The list of saved answers (re-read only when index.json changes)."""
    path = DIR / "index.json"
    try:
        mtime = path.stat().st_mtime
        if _index_cache["mtime"] != mtime:
            _index_cache["entries"] = json.loads(path.read_text(encoding="utf-8"))
            _index_cache["mtime"] = mtime
    except (OSError, ValueError):
        return []
    return _index_cache["entries"]


def has(kind: str, params: dict) -> bool:
    """Whether a saved result matches *params* (without loading it)."""
    return any(entry.get("kind") == kind and _matches(kind, entry["params"], params) for entry in _index())


def _matches(kind: str, saved: dict, params: dict) -> bool:
    if kind == "search":
        return (abs(saved["radius"] - params["radius"]) < 1e-4
                and _separation_arcsec(saved["ra"], saved["dec"], params["ra"], params["dec"]) < FIELD_TOLERANCE_ARCSEC)
    if kind == "frames":
        return (saved.get("group") == params.get("group") and saved.get("detector") == params.get("detector")
                and bool(saved.get("flags")) == bool(params.get("flags"))
                and abs(saved["fov"] - params["fov"]) < 0.01
                and _separation_arcsec(saved["ra"], saved["dec"], params["ra"], params["dec"]) < FIELD_TOLERANCE_ARCSEC)
    field, wanted = saved.get("field"), params.get("field")
    if not field or not wanted:
        return False
    return (abs(saved["r_ap"] - params["r_ap"]) < 1e-6 and abs(field["fov"] - wanted["fov"]) < 0.01
            and _separation_arcsec(field["ra"], field["dec"], wanted["ra"], wanted["dec"]) < FIELD_TOLERANCE_ARCSEC
            and _separation_arcsec(saved["ra"], saved["dec"], params["ra"], params["dec"]) < POSITION_TOLERANCE_ARCSEC
            and abs(saved["pmra"] - params["pmra"]) < 1.0 and abs(saved["pmdec"] - params["pmdec"]) < 1.0)


# Saved results already read, by (file, date saved): a few megabytes in all, and
# each would otherwise be read and parsed again on every visit to a test target.
_loaded: dict[tuple[str, str], dict] = {}
_loaded_lock = threading.Lock()


def find(kind: str, params: dict) -> dict | None:
    """The saved result for *params*, marked as a snapshot, or None.

    The caller gets its own top-level dict; what it holds is shared and must
    not be changed.
    """
    for entry in _index():
        if entry.get("kind") != kind or not _matches(kind, entry["params"], params):
            continue
        key = (entry["file"], entry["saved"])
        with _loaded_lock:
            result = _loaded.get(key)
        if result is None:
            try:
                with gzip.open(DIR / entry["file"], "rt", encoding="utf-8") as handle:
                    result = json.load(handle)
            except (OSError, ValueError):
                continue
            result["cached"] = True
            result["snapshot"] = {"saved": entry["saved"]}
            with _loaded_lock:
                _loaded[key] = result
        return dict(result)
    return None


def search(ra: float, dec: float, radius_deg: float) -> list[dict] | None:
    """The saved image search around a test target (rows of ``sia.Exposure``), or None.

    IRSA's image search is the slowest step of a new field (a minute on a bad
    day), so the test targets carry theirs: any view of them starts reading
    files at once, while the search is renewed in the background.
    """
    found = find("search", {"ra": ra, "dec": dec, "radius": radius_deg})
    return found["exposures"] if found else None


def save(kind: str, name: str, params: dict, result: dict) -> Path:
    """Store *result* as the snapshot *name* (used by scripts/snapshot.py)."""
    DIR.mkdir(parents=True, exist_ok=True)
    file = f"{name}-{kind}.json.gz"
    with gzip.open(DIR / file, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(result, handle, separators=(",", ":"))
    entries = [e for e in _index() if e.get("file") != file]
    entries.append({"kind": kind, "file": file, "saved": time.strftime("%Y-%m-%d", time.gmtime()), "params": params})
    entries.sort(key=lambda e: e["file"])
    (DIR / "index.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return DIR / file
