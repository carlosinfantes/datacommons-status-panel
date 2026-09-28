import contextlib

from dc_status.cache import TTLCache


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
