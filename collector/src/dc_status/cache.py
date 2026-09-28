# Copyright 2026 Carlos Infantes
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""In-process caches. Per instance, deliberately not shared or persisted."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

DOCUMENT_TTL_SECONDS = 30.0
FRESH_INTERVAL_SECONDS = 10.0


class _Flight:
    """One collection in progress, and what everyone waiting on it will get."""

    def __init__(self):
        self.done = threading.Event()
        self.value: object = None
        self.error: BaseException | None = None


class DocumentCache:
    """The whole status document: a short TTL, one collection at a time.

    Single flight: while a collection runs, every other request waits for it
    rather than starting its own. A page load fans out to every probe and to
    Cloud Monitoring, so N concurrent readers must not mean N collections.

    `fresh` bypasses the TTL, but only for a document at least `fresh_interval`
    old. The Refresh button is the one thing a reader controls, and holding it
    down must not turn into a collection per click against the platform's APIs.
    A collection already in flight is joined even when `fresh` is asked for: it
    is as fresh as a new one would be.

    A failed collection is not cached; it reaches every request that waited on
    it, and the next request tries again.
    """

    def __init__(
        self,
        *,
        ttl: float = DOCUMENT_TTL_SECONDS,
        fresh_interval: float = FRESH_INTERVAL_SECONDS,
        clock=time.monotonic,
    ):
        self._ttl = ttl
        self._fresh_interval = fresh_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._value: object = None
        self._stored_at: float | None = None
        self._flight: _Flight | None = None

    def get(self, producer: Callable[[], object], *, fresh: bool = False):
        with self._lock:
            flight = self._flight
            if flight is None:
                if self._usable(fresh):
                    return self._value
                flight = self._flight = _Flight()
                leader = True
            else:
                leader = False

        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value

        try:
            value = producer()  # outside the lock: a collection takes seconds
        except BaseException as exc:
            flight.error = exc
            raise
        else:
            flight.value = value
            with self._lock:
                self._value, self._stored_at = value, self._clock()
            return value
        finally:
            with self._lock:
                self._flight = None
            flight.done.set()

    def _usable(self, fresh: bool) -> bool:
        if self._stored_at is None:
            return False
        age = self._clock() - self._stored_at
        if fresh and age >= self._fresh_interval:
            return False
        return age < self._ttl


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
