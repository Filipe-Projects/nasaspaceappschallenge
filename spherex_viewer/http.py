"""HTTP with patience: one session per thread, retries that cover the body too.

Archive servers answer "503 busy" during bursts and long links drop
connections halfway through a response.  :func:`fetch` therefore retries the
whole exchange (request *and* reading the body) with exponential back-off, so
callers either get complete bytes or an exception.
"""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass

import requests

from . import config

_thread_local = threading.local()
TRANSIENT_STATUS = (408, 429, 500, 502, 503, 504)


class TransientHTTPError(RuntimeError):
    """Raised when a request keeps failing with a retryable error."""


class HTTPStatusError(RuntimeError):
    """A definitive HTTP error status (not worth retrying)."""


@dataclass
class Fetched:
    """A fully-read response."""
    status_code: int
    headers: requests.structures.CaseInsensitiveDict
    content: bytes
    url: str
    encoding: str | None = None

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def json(self):
        return json.loads(self.content.decode("utf-8", errors="replace"))

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPStatusError(f"HTTP {self.status_code} from {self.url.split('?')[0]}")


def session() -> requests.Session:
    """One requests.Session per thread (Session objects are not thread-safe)."""
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update({"User-Agent": config.USER_AGENT})
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        _thread_local.session = sess
    return sess


def fetch(method: str, url: str, *, what: str = "request", retries: int | None = None,
          timeout: float | None = None, read_if=None, **kwargs) -> Fetched:
    """Send a request and read the whole body, retrying transient failures.

    *read_if(status)* lets a caller skip the body of an unwanted answer (the
    range reader uses it to avoid downloading a 70 MB file when the server
    ignores a Range header).  The body counts as part of the attempt: a
    connection dropped halfway is retried like a refused one.
    """
    attempts = (config.HTTP_RETRIES if retries is None else retries) + 1
    delay = 1.0
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = session().request(method, url, timeout=timeout or config.HTTP_TIMEOUT, stream=True, **kwargs)
            try:
                if resp.status_code in TRANSIENT_STATUS:
                    last = TransientHTTPError(f"{what}: HTTP {resp.status_code}")
                else:
                    wanted = read_if is None or read_if(resp.status_code)
                    content = resp.content if wanted else b""
                    return Fetched(resp.status_code, resp.headers, content, resp.url, resp.encoding)
            finally:
                resp.close()
        except (requests.RequestException, OSError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(delay + random.random() * 0.5)
            delay = min(delay * 2, 16)
    raise TransientHTTPError(f"{what} failed after {attempts} attempt{'s' if attempts > 1 else ''}: {last}")
