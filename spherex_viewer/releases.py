"""Which SPHEREx image collections does IRSA serve right now?

SPHEREx data arrive weekly and the release names change over time: QR1 was
retired when QR2 arrived, and QR2 in turn gave way to DR1 (the first year
reprocessed) while this tool was being written.  Hard-coding a list is
therefore a liability.  The viewer asks IRSA which ``spherex_*`` collections
exist and orders them itself:

* data releases (``dr``) before quick releases (``qr``), newest number first,
  so when the same exposure exists twice the better calibration wins;
* calibration and cube collections are ignored (the viewer reads Level 2
  spectral images only).

The answer is cached for a day and refreshed in the background, and the
static fallback keeps the tool working when the registry cannot be reached.
Setting ``SPHEREX_COLLECTIONS`` pins the list by hand.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import threading
import time

from . import config
from .cache import default_cache
from .http import fetch

log = logging.getLogger("spherex_viewer")

_NAME = re.compile(r"^spherex_(dr|qr)(\d+)(_deep)?$")
_QUERY = "SELECT DISTINCT collection FROM caom.observation WHERE collection LIKE 'spherex%'"
_TTL = 24 * 3600.0
_lock = threading.Lock()
_state = {"names": None, "checked": 0.0, "refreshing": False}


def preference(name: str) -> tuple:
    """Sort key: data releases first, then newest, wide survey before deep."""
    match = _NAME.match(name)
    if not match:
        return (9, 0, 0, name)
    return (0 if match.group(1) == "dr" else 1, -int(match.group(2)), 1 if match.group(3) else 0, name)


def order(names) -> list[str]:
    return sorted({n for n in names if _NAME.match(n)}, key=preference)


def query_irsa() -> list[str]:
    """Ask IRSA's TAP service for the SPHEREx collections it holds."""
    resp = fetch("POST", config.IRSA_TAP_URL, data={"QUERY": _QUERY, "FORMAT": "CSV"}, timeout=120,
                 what="IRSA TAP collections", retries=1)
    resp.raise_for_status()
    text = resp.text
    names = [row.get("collection", "").strip() for row in csv.DictReader(io.StringIO(text))]
    found = order(names)
    if not found:
        raise RuntimeError("IRSA returned no SPHEREx image collections")
    return found


def _refresh() -> None:
    try:
        names = query_irsa()
        default_cache().put_json("meta", "collections", {"names": names, "checked": time.time()})
        with _lock:
            if names != _state["names"]:
                log.info("SPHEREx collections at IRSA: %s", ", ".join(names))
            _state["names"], _state["checked"] = names, time.time()
    except Exception as exc:  # noqa: BLE001 - keep whatever list we have
        log.warning("could not refresh the SPHEREx collection list: %s", exc)
        with _lock:
            _state["checked"] = time.time() - _TTL + 900      # try again in 15 minutes
    finally:
        with _lock:
            _state["refreshing"] = False


def active_collections(wait: bool = False) -> list[str]:
    """Collections to search, best first.  Never blocks unless *wait* is set."""
    if config.COLLECTIONS_OVERRIDE:
        return list(config.COLLECTIONS_OVERRIDE)
    with _lock:
        if _state["names"] is None:
            cached = default_cache().get_json("meta", "collections")
            if cached and cached.get("names"):
                _state["names"], _state["checked"] = order(cached["names"]), float(cached.get("checked", 0.0))
        stale = (time.time() - _state["checked"]) > _TTL
        start = stale and not _state["refreshing"]
        if start:
            _state["refreshing"] = True
        names = _state["names"]
    if start:
        if wait:
            _refresh()
            with _lock:
                names = _state["names"]
        else:
            threading.Thread(target=_refresh, name="collections", daemon=True).start()
    return list(names) if names else list(config.DEFAULT_COLLECTIONS)
