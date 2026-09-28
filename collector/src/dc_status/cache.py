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
