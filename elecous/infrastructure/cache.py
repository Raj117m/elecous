"""Lightweight TTL cache (spec 10.25: cache frequently used results).

Deliberately minimal (Appendix A Principle 4 — scientific minimalism):
no external cache dependency, just a bounded dict with expiry, safe for the
single-process MacBook deployment target. Used to avoid recomputing
expensive aggregations (e.g. environmental signal extraction) when called
repeatedly for the same or nearby instants within a short window, which
happens constantly during pattern mining and historical replay.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable


class TTLCache:
    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 512):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and entry[0] > now:
                self.hits += 1
                return entry[1]
        value = compute()
        with self._lock:
            if len(self._store) >= self.max_entries:
                # Evict the oldest entry rather than growing unbounded.
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest_key]
            self._store[key] = (now + self.ttl_seconds, value)
            self.misses += 1
        return value

    def invalidate(self, key: Hashable | None = None) -> None:
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else None,
            "entries": len(self._store),
        }
