"""Background jobs with progress reporting.

Building a frame sequence means hundreds of small archive requests, which can
take longer than a hosting platform's HTTP timeout.  Requests therefore
create a job, and the browser polls for progress until the result is ready.
Identical requests share one job.
"""
from __future__ import annotations

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
    result: Any = None
    error: str | None = None

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

    def submit(self, kind: str, params: dict, runner: Runner) -> Job:
        key = params_key(kind, params)
        with self._lock:
            self._cleanup_locked()
            existing_id = self._by_key.get(key)
            if existing_id and existing_id in self._jobs:
                existing = self._jobs[existing_id]
                # an answer served from a saved copy is not reused: the next request tries IRSA again
                stand_in = isinstance(existing.result, dict) and existing.result.get("snapshot")
                if existing.state != "error" and not stand_in:
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
            job.result = runner(job.params, progress)
            job.state = "done"
            job.message = "done"
            if job.total:
                job.done = job.total
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "failed"
            traceback.print_exc()
        finally:
            job.finished = time.time()

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired = [jid for jid, job in self._jobs.items()
                   if job.finished and now - job.finished > JOB_TTL_SECONDS]
        for jid in expired:
            job = self._jobs.pop(jid)
            if self._by_key.get(job.key) == jid:
                self._by_key.pop(job.key, None)
