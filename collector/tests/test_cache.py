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

import contextlib
import threading

import pytest

from dc_status.cache import DocumentCache, TTLCache


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_calls_the_producer_once_within_the_ttl():
    clock = FakeClock()
    cache = TTLCache(clock=clock)
    calls = []

    def producer():
        calls.append(1)
        return "value"

    assert cache.get_or_call("k", 300, producer) == "value"
    clock.now += 299
    assert cache.get_or_call("k", 300, producer) == "value"
    assert len(calls) == 1


def test_recomputes_after_the_ttl_expires():
    clock = FakeClock()
    cache = TTLCache(clock=clock)
    values = iter(["first", "second"])

    def producer():
        return next(values)

    assert cache.get_or_call("k", 300, producer) == "first"
    clock.now += 301
    assert cache.get_or_call("k", 300, producer) == "second"


def test_keys_are_independent():
    cache = TTLCache(clock=FakeClock())
    assert cache.get_or_call("a", 300, lambda: 1) == 1
    assert cache.get_or_call("b", 300, lambda: 2) == 2


def test_a_ttl_of_zero_never_caches():
    clock = FakeClock()
    cache = TTLCache(clock=clock)
    values = iter([1, 2])

    def producer():
        return next(values)

    assert cache.get_or_call("k", 0, producer) == 1
    assert cache.get_or_call("k", 0, producer) == 2


def test_a_failing_producer_does_not_poison_the_entry():
    cache = TTLCache(clock=FakeClock())
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return "ok"

    with contextlib.suppress(RuntimeError):
        cache.get_or_call("k", 300, flaky)
    assert cache.get_or_call("k", 300, flaky) == "ok"


def test_a_value_keep_turns_down_is_returned_but_not_stored():
    cache = TTLCache(clock=FakeClock())
    values = iter(["bad", "good", "never"])

    def producer():
        return next(values)

    def keep(value):
        return value != "bad"

    assert cache.get_or_call("k", 300, producer, keep=keep) == "bad"
    assert cache.get_or_call("k", 300, producer, keep=keep) == "good"
    assert cache.get_or_call("k", 300, producer, keep=keep) == "good"


class Counter:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"n": self.calls}


def test_the_document_is_reused_within_its_ttl_and_rebuilt_after():
    clock = FakeClock()
    cache = DocumentCache(ttl=30, fresh_interval=10, clock=clock)
    produce = Counter()
    assert cache.get(produce) == {"n": 1}
    clock.now += 29
    assert cache.get(produce) == {"n": 1}
    clock.now += 2
    assert cache.get(produce) == {"n": 2}


def test_fresh_bypasses_a_document_older_than_the_fresh_interval():
    clock = FakeClock()
    cache = DocumentCache(ttl=30, fresh_interval=10, clock=clock)
    produce = Counter()
    cache.get(produce)
    clock.now += 10
    assert cache.get(produce, fresh=True) == {"n": 2}


def test_fresh_is_rate_limited_so_a_held_refresh_key_cannot_hammer_the_apis():
    clock = FakeClock()
    cache = DocumentCache(ttl=30, fresh_interval=10, clock=clock)
    produce = Counter()
    cache.get(produce)
    clock.now += 9
    assert cache.get(produce, fresh=True) == {"n": 1}
    assert produce.calls == 1


def test_fresh_with_nothing_cached_collects():
    cache = DocumentCache(clock=FakeClock())
    produce = Counter()
    assert cache.get(produce, fresh=True) == {"n": 1}


def test_concurrent_requests_share_one_collection():
    # Two page loads arriving together must not run every probe twice: the second
    # waits for the first's collection instead of starting its own.
    cache = DocumentCache()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        started.set()
        release.wait(timeout=5)
        return {"n": len(calls)}

    results = []

    def request(fresh=False):
        results.append(cache.get(slow, fresh=fresh))

    first = threading.Thread(target=request)
    first.start()
    assert started.wait(timeout=5)
    # fresh=True too: an in-flight collection is as fresh as it gets, so even a
    # bypass joins it rather than starting a second one.
    second = threading.Thread(target=request, kwargs={"fresh": True})
    second.start()
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert len(calls) == 1
    assert results == [{"n": 1}, {"n": 1}]


def test_a_failed_collection_is_not_cached_and_reaches_every_waiter():
    cache = DocumentCache(clock=FakeClock())
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    with pytest.raises(RuntimeError):
        cache.get(flaky)
    assert cache.get(flaky) == {"ok": True}
