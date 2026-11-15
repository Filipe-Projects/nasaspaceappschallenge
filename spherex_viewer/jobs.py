"""Background jobs with progress reporting.

Building a frame sequence means hundreds of small archive requests, which can
take longer than a hosting platform's HTTP timeout.  Requests therefore
create a job, and the browser polls for progress until the result is ready.
Identical requests share one job.

A finished result is turned into gzip-compressed JSON once, in the job's own
thread: the answer is then sent without any work on the request path (several
times faster on a small shared CPU), and a kept job holds about half the memory
of the Python objects.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

ProgressCallback = Callable[[int, int, str], None]
Runner = Callable[[dict, ProgressCallback], Any]

JOB_TTL_SECONDS = 3600.0
# Finished jobs kept at most, by number and by size; the oldest go first (the
# browser starts a job it no longer finds again, and its result then comes
# from the disk cache).
MAX_FINISHED_JOBS = 48
MAX_FINISHED_BYTES = 64_000_000
GZIP_LEVEL = 6                     # same size as level 9 for this data, in half the time


def encode_result(result: Any) -> bytes:
    """The result as the JSON a JSONResponse would send, gzip-compressed."""
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    key: str
    state: str = "queued"          # queued | running | done | error
    done: int = 0
    total: int = 0
    message: str = "queued"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    body: bytes | None = None      # the result, see encode_result()
    snapshot: bool = False         # answered from a saved copy
    error: str | None = None

    @property
    def result(self) -> Any:
        """The result as Python objects (decoded on demand: only the FITS export needs it)."""
        return None if self.body is None else json.loads(gzip.decompress(self.body))

    def status(self) -> dict:
        elapsed = (self.finished or time.time()) - (self.started or self.created)
        return {
            "job_id": self.id,
            "kind": self.kind,
            "state": self.state,
            "done": self.done,
            "total": self.total,
            "progress": (self.done / self.total) if self.total else 0.0,
            "message": self.message,
            "error": self.error,
            "elapsed_s": round(elapsed, 1),
        }


def params_key(kind: str, params: dict) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(f"{kind}|{payload}".encode("utf-8")).hexdigest()


class JobManager:
    def __init__(self, max_workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, str] = {}
        self._lock = threading.Lock()
        # Encoded saved copies, by (request, date saved): a new job is started for every
        # request answered from a saved copy, and the copy itself never changes.
        self._saved_bodies: dict[tuple[str, str], bytes] = {}

    def submit(self, kind: str, params: dict, runner: Runner) -> Job:
        key = params_key(kind, params)
        with self._lock:
            self._cleanup_locked()
            existing_id = self._by_key.get(key)
            if existing_id and existing_id in self._jobs:
                existing = self._jobs[existing_id]
                # an answer served from a saved copy is not reused: the next request tries IRSA again
                if existing.state != "error" and not existing.snapshot:
                    return existing
            job = Job(id=uuid.uuid4().hex, kind=kind, params=params, key=key)
            self._jobs[job.id] = job
            self._by_key[key] = job.id
        self._pool.submit(self._run, job, runner)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, runner: Runner) -> None:
        job.state = "running"
        job.started = time.time()
        job.message = "starting"

        def progress(done: int, total: int, message: str = "") -> None:
            job.done, job.total = int(done), int(total)
            if message:
                job.message = message

        try:
            result = runner(job.params, progress)
            saved = result.get("snapshot") if isinstance(result, dict) else None
            job.snapshot = bool(saved)
            saved_key = (job.key, json.dumps(saved, sort_keys=True)) if saved else None
            with self._lock:
                body = self._saved_bodies.get(saved_key) if saved_key else None
            if body is None:
                body = encode_result(result)
                if saved_key:
                    with self._lock:
                        self._saved_bodies[saved_key] = body
                        while len(self._saved_bodies) > 16:
                            self._saved_bodies.pop(next(iter(self._saved_bodies)))
            job.body = body
            del result
            if job.total:
                job.done = job.total
            job.message = "done"
            job.state = "done"             # last: a poll that sees "done" finds the body ready
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "failed"
            traceback.print_exc()
        finally:
            job.finished = time.time()

    def _cleanup_locked(self) -> None:
        now = time.time()
        finished = sorted((job.finished, jid) for jid, job in self._jobs.items() if job.finished)
        expired = {jid for when, jid in finished if now - when > JOB_TTL_SECONDS}
        expired.update(jid for _, jid in finished[:max(0, len(finished) - MAX_FINISHED_JOBS)])
        kept = 0
        for _, jid in reversed(finished):                 # newest first
            kept += len(self._jobs[jid].body or b"")
            if kept > MAX_FINISHED_BYTES:
                expired.add(jid)
        for jid in expired:
            job = self._jobs.pop(jid)
            if self._by_key.get(job.key) == jid:
                self._by_key.pop(job.key, None)
