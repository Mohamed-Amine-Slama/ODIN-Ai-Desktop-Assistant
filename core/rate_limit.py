"""Token-bucket throttle for skills that reach outside the machine
(web_fetch, http_request, get_news) — the same limit whether the call came
from you, a scheduled task, or the Telegram/Discord bridge, so a runaway loop
or a prompt-injected page instructing "fetch this 50 times" can't hammer a
target unattended. MAX_TOOL_ITERATIONS already bounds one turn's tool calls;
this bounds calls across turns and across the process's whole run.

Deliberately not per-caller: this assistant has one user, so a global bucket
per skill is the whole threat model — there is no "other tenant" to isolate.
"""
import threading
import time

import config


class _Bucket:
    __slots__ = ("tokens", "last_refill", "lock")

    def __init__(self, capacity: float):
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()


_buckets: dict[str, _Bucket] = {}
_buckets_lock = threading.Lock()


def _get_bucket(key: str) -> _Bucket:
    with _buckets_lock:
        bucket = _buckets.get(key)
        if bucket is None:
            bucket = _Bucket(config.RATE_LIMIT_BURST)
            _buckets[key] = bucket
        return bucket


def allow(key: str) -> bool:
    """True if a call tagged `key` may proceed right now, consuming one
    token. RATE_LIMIT_PER_MINUTE=0 disables limiting entirely."""
    per_minute = config.RATE_LIMIT_PER_MINUTE
    if per_minute <= 0:
        return True

    rate = per_minute / 60.0
    bucket = _get_bucket(key)
    with bucket.lock:
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.tokens = min(config.RATE_LIMIT_BURST, bucket.tokens + elapsed * rate)
        bucket.last_refill = now

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True
        return False


def reset() -> None:
    """Clear all bucket state. Test-only — production never needs this since
    buckets refill on their own."""
    with _buckets_lock:
        _buckets.clear()
