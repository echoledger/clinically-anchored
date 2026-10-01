"""In-memory sliding-window rate limiting for patient-link routes.

v0 on a single Railway instance: state lives in this process, so it resets on
restart and is NOT shared if the service is ever scaled to more than one
instance or worker -- each would enforce its own limit. Moving to Redis-backed
limiting is a Pro-tier job; `hit()` is the one seam that would change.

Callers are keyed by the *verified* link token's patient (not the raw token,
which is a secret, and not the client IP: behind Railway's proxy
`request.client.host` is the proxy unless uvicorn is told to trust forwarded
headers). Invalid tokens are not counted: they fail an HMAC check before any
database work, so there is nothing for a limit to protect.
"""

import math
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

from clinically_anchored_api.core.config import get_settings

WINDOW_SECONDS = 60
_MAX_KEYS = 10_000  # sweep expired keys past this, so the dict can't grow forever

_clock = time.monotonic
_lock = threading.Lock()
_hits: dict[tuple, deque[float]] = {}


def reset() -> None:
    """Forget all counters (tests)."""
    with _lock:
        _hits.clear()


def hit(key: tuple, limit: int, window: float = WINDOW_SECONDS) -> int:
    """Record one request against `key`. Returns 0 if allowed, otherwise the
    seconds to wait before the oldest counted request leaves the window.
    Rejected requests are not counted, so backing off always works."""
    now = _clock()
    with _lock:
        if len(_hits) > _MAX_KEYS:
            _sweep(now, window)
        stamps = _hits.setdefault(key, deque())
        while stamps and now - stamps[0] >= window:
            stamps.popleft()
        if len(stamps) >= limit:
            return max(1, math.ceil(window - (now - stamps[0])))
        stamps.append(now)
        return 0


def _sweep(now: float, window: float) -> None:
    for key in [k for k, s in _hits.items() if not s or now - s[-1] >= window]:
        del _hits[key]


def limit_patient_link(request: Request, claims: dict[str, str]) -> None:
    """Throttle a request authenticated by a valid link token: 429 with
    Retry-After once the patient exceeds the per-minute budget. Reads (GET) and
    writes get separate budgets, shared across the patient's link scopes. A
    limit of 0 or less disables that budget."""
    settings = get_settings()
    is_read = request.method in ("GET", "HEAD")
    limit = (
        settings.patient_link_reads_per_minute
        if is_read
        else settings.patient_link_writes_per_minute
    )
    if limit <= 0:
        return
    kind = "read" if is_read else "write"
    wait = hit((claims["clinic_id"], claims["patient_id"], kind), limit)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": str(wait)},
        )
