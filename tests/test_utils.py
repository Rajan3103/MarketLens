"""Tests for marketlens/utils.py. No real waiting: time is faked."""
import pytest

from marketlens import config, utils
from marketlens.utils import RateLimiter, retry


# Record the waits instead of really sleeping, so tests run instantly.
@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(utils.time, "sleep", recorded.append)
    return recorded


# Make a function that fails `failures` times, then returns "ok".
def flaky(failures: int, error=ConnectionError):
    calls = {"n": 0}

    def func():
        calls["n"] += 1
        if calls["n"] <= failures:
            raise error("network down")
        return "ok"
    return func, calls


# Two failures, then success: waits 1 s and 2 s, and returns the result.
def test_retry_succeeds_after_failures(waits):
    func, calls = flaky(2)
    assert retry(func)() == "ok"
    assert calls["n"] == 3
    assert waits == [1, 2]


# Always failing: 1 try + 3 retries, waits 1, 2, 4 s, then the error is raised.
def test_retry_gives_up(waits):
    func, calls = flaky(99)
    with pytest.raises(ConnectionError):
        retry(func)()
    assert calls["n"] == 4
    assert waits == [1, 2, 4]


# Each failure is written to the log.
def test_retry_logs_failures(waits, monkeypatch):
    messages = []
    monkeypatch.setattr(utils.log, "warning", lambda *a: messages.append(a))
    monkeypatch.setattr(utils.log, "error", lambda *a: messages.append(a))
    func, _ = flaky(99)
    with pytest.raises(ConnectionError):
        retry(func)()
    assert len(messages) == 4   # 3 "trying again" + 1 "failed after 4 tries"


# Errors not in the list are raised at once, without retrying.
def test_retry_only_listed_errors(waits):
    func, calls = flaky(1, error=KeyError)
    with pytest.raises(KeyError):
        retry(exceptions=(ConnectionError,))(func)()
    assert calls["n"] == 1 and waits == []


# @retry(...) with settings, used as a decorator.
def test_retry_with_settings(waits):
    @retry(retries=1, first_wait=0.5)
    def always_fails():
        raise TimeoutError("slow")
    with pytest.raises(TimeoutError):
        always_fails()
    assert waits == [0.5]
    assert always_fails.__name__ == "always_fails"   # name kept for logs


class FakeClock:
    """A clock that only moves when sleep() is called."""

    # Start the fake time at 0.
    def __init__(self):
        self.now = 0.0

    # Return the fake time.
    def __call__(self):
        return self.now

    # Move the fake time forward instead of really waiting.
    def sleep(self, seconds):
        self.now += seconds


# 3 calls per second: the first 3 go at once, the 4th waits until 1 s has passed.
def test_rate_limiter_waits_when_full():
    clock = FakeClock()
    limiter = RateLimiter(3, clock=clock, sleep=clock.sleep)
    assert [limiter.wait() for _ in range(3)] == [0, 0, 0]
    assert limiter.wait() == pytest.approx(1.0)
    assert clock.now == pytest.approx(1.0)


# Never more than N calls inside any 1-second window.
def test_rate_limiter_never_exceeds_limit():
    clock = FakeClock()
    limiter = RateLimiter(5, clock=clock, sleep=clock.sleep)
    times = []
    for _ in range(23):
        limiter.wait()
        times.append(clock.now)
    for t in times:
        assert sum(1 for u in times if t <= u < t + 1.0) <= 5


# Calls spread out over time never wait.
def test_rate_limiter_no_wait_when_slow():
    clock = FakeClock()
    limiter = RateLimiter(2, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        assert limiter.wait() == 0
        clock.now += 0.6


# Works as `with limiter:` and as `@limiter`.
def test_rate_limiter_with_and_decorator():
    clock = FakeClock()
    limiter = RateLimiter(1, clock=clock, sleep=clock.sleep)

    @limiter
    def place_order():
        return clock.now

    with limiter:
        pass
    assert place_order() == pytest.approx(1.0)   # had to wait for the `with` call


# The default limit comes from MAX_ORDERS_PER_SECOND in .env.
def test_rate_limiter_default_from_config():
    assert RateLimiter().max_calls == config.MAX_ORDERS_PER_SECOND


# A limit below 1 makes no sense and is refused.
def test_rate_limiter_bad_limit():
    with pytest.raises(ValueError):
        RateLimiter(0)


# ---- TtlCache ----

# A saved answer is reused until it is older than the limit; errors are never saved.
def test_ttl_cache():
    now = {"t": 100.0}
    cache = utils.TtlCache(clock=lambda: now["t"])
    made = []
    make = lambda: made.append(1) or len(made)          # noqa: E731  (returns 1, then 2, ...)
    assert cache.get("k", 60, make) == 1
    now["t"] += 59
    assert cache.get("k", 60, make) == 1 and len(made) == 1
    assert cache.peek("k", 60) == (59.0, 1)
    now["t"] += 1                                         # exactly 60 s old: too old
    assert cache.peek("k", 60) is None and cache.peek("k") == (60.0, 1)
    assert cache.get("k", 60, make) == 2

    def broken():
        raise ConnectionError("offline")
    with pytest.raises(ConnectionError):
        cache.get("other", 60, broken)
    assert cache.peek("other") is None
    cache.clear()
    assert cache.peek("k") is None
