"""Hot-read cache with a Redis-compatible seam.

Offline/demo: a tiny in-process TTL cache (zero dependencies) fronting SQLite reads that are
hit on every chat turn — per-tenant policy chunks (the RAG corpus), company lookups, platform
users. Invalidation is explicit on writes.

Production: set ``REDIS_URL`` and swap ``_Backend`` for a redis client with the same four
methods (get/set/delete/clear) — call sites don't change. The interface is deliberately the
redis-py subset (``get``/``setex``-style semantics) so the swap is mechanical.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_DEFAULT_TTL = 30.0  # seconds — demo-fresh but saves re-reading + re-parsing every turn


class _Backend:
    """In-process TTL store. Replace with redis-py behind the same methods for multi-worker."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            hit = self._d.get(key)
            if not hit:
                return None
            exp, val = hit
            if time.time() > exp:
                self._d.pop(key, None)
                return None
            return val

    def set(self, key: str, val: Any, ttl: float = _DEFAULT_TTL) -> None:
        with self._lock:
            if len(self._d) > 2048:  # simple bound; Redis handles eviction in production
                self._d.clear()
            self._d[key] = (time.time() + ttl, val)

    def delete(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._d if k.startswith(prefix)]:
                self._d.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


_backend = _Backend()

get = _backend.get
set = _backend.set
invalidate = _backend.delete
clear = _backend.clear
