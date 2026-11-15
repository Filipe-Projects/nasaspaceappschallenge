"""Tiny disk cache: JSON documents and numpy archives keyed by a string.

Everything the viewer fetches from IRSA is immutable (a given Level 2 file
never changes), so entries never go stale; an optional TTL is used only for
search results, which grow as new data arrive every week.  The cache is an
optimisation, never a source of truth: every read or write may fail quietly.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import config


class DiskCache:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.CACHE_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._made: set[str] = set()

    # -- helpers -------------------------------------------------------------
    def path(self, namespace: str, key: str, suffix: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        folder = self.root / namespace / digest[:2]
        marker = f"{namespace}/{digest[:2]}"
        if marker not in self._made:
            folder.mkdir(parents=True, exist_ok=True)
            self._made.add(marker)
        return folder / f"{digest}{suffix}"

    @staticmethod
    def _fresh(path: Path, ttl: float | None) -> bool:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        return ttl is None or (time.time() - mtime) < ttl

    def _write_atomic(self, path: Path, writer) -> None:
        """Write to a temp file, then rename it into place.

        On Windows the rename fails with PermissionError while another thread
        is reading the destination; retry briefly, then give up quietly.
        """
        tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                writer(fh)
            for attempt in range(8):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    time.sleep(0.03 * (attempt + 1))
        except OSError:
            pass
        try:
            os.remove(tmp)
        except OSError:
            pass

    # -- JSON ----------------------------------------------------------------
    def get_json(self, namespace: str, key: str, ttl: float | None = None) -> Any | None:
        path = self.path(namespace, key, ".json")
        if not self._fresh(path, ttl):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def put_json(self, namespace: str, key: str, value: Any) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._write_atomic(self.path(namespace, key, ".json"), lambda fh: fh.write(payload))

    # -- numpy ---------------------------------------------------------------
    def get_arrays(self, namespace: str, key: str) -> dict[str, np.ndarray] | None:
        path = self.path(namespace, key, ".npz")
        try:
            with np.load(path, allow_pickle=False) as data:
                return {name: data[name] for name in data.files}
        except (OSError, ValueError, EOFError, KeyError):
            return None
        except Exception:  # noqa: BLE001 - a truncated archive must not break a job
            return None

    def put_arrays(self, namespace: str, key: str, arrays: dict[str, np.ndarray],
                   compressed: bool = False) -> None:
        save = np.savez_compressed if compressed else np.savez
        self._write_atomic(self.path(namespace, key, ".npz"), lambda fh: save(fh, **arrays))

    # -- housekeeping ----------------------------------------------------------
    def size_bytes(self) -> int:
        total = 0
        for _dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(_dirpath, name))
                except OSError:
                    pass
        return total

    def prune(self, max_bytes: float | None = None) -> int:
        """Delete the least recently written files until the cache fits."""
        limit = config.CACHE_MAX_GB * 1e9 if max_bytes is None else max_bytes
        entries = []
        total = 0
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, full))
                total += st.st_size
        if total <= limit:
            return 0
        removed = 0
        for _mtime, size, full in sorted(entries):
            try:
                os.remove(full)
            except OSError:
                continue
            total -= size
            removed += 1
            if total <= 0.8 * limit:
                break
        return removed


_default: DiskCache | None = None
_default_lock = threading.Lock()


def default_cache() -> DiskCache:
    global _default
    with _default_lock:
        if _default is None or _default.root != Path(config.CACHE_DIR):
            _default = DiskCache()
        return _default
