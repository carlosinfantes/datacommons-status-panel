"""In-process TTL cache. Per instance, deliberately not shared or persisted."""

from __future__ import annotations

import threading
import time


class TTLCache:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, object]] = {}

    def get_or_call(self, key: str, ttl: float, producer):
        if ttl > 0:
            with self._lock:
                entry = self._entries.get(key)
                if entry and self._clock() < entry[0]:
                    return entry[1]
        value = producer()  # produced outside the lock: probes are slow
        if ttl > 0:
            with self._lock:
                self._entries[key] = (self._clock() + ttl, value)
        return value
