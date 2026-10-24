"""Small shared helpers: retry a failing call, and limit calls per second.

Both are used around Dhan API calls and order placement:
    from marketlens.utils import retry, RateLimiter

    @retry                    # try again if the internet hiccups
    def get_option_chain(): ...

    order_limiter = RateLimiter()          # MAX_ORDERS_PER_SECOND from .env
    with order_limiter:                    # waits if we are going too fast
        place_order(...)
"""
import functools
import threading
import time
from collections import deque

from marketlens import config
from marketlens.logger import get_logger

log = get_logger(__name__)

RETRIES = 3          # extra tries after the first one fails
FIRST_WAIT_S = 1.0   # wait before the 1st retry; doubles each time: 1 s, 2 s, 4 s


# Run a function again if it fails, waiting a bit longer each time.
def retry(func=None, *, retries: int = RETRIES, first_wait: float = FIRST_WAIT_S,
          exceptions: tuple = (Exception,)):
    """Decorator. Use it as @retry or @retry(retries=5, exceptions=(TimeoutError,)).

    The first call runs straight away. If it raises one of `exceptions`,
    the failure is logged, we wait 1 s and try again, then 2 s, then 4 s.
    If the last try also fails, that error is raised to the caller.
    """
    # Called as @retry(...) with settings: return the real decorator
    if func is None:
        return functools.partial(retry, retries=retries, first_wait=first_wait,
                                 exceptions=exceptions)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        wait = first_wait
        for attempt in range(1, retries + 2):  # first try + `retries` more
            try:
                return func(*args, **kwargs)
            except exceptions as err:
                if attempt > retries:
                    log.error("%s failed after %d tries: %s", func.__name__, attempt, err)
                    raise
                log.warning("%s failed (try %d of %d): %s. Trying again in %.0f s",
                            func.__name__, attempt, retries + 1, err, wait)
                time.sleep(wait)
                wait *= 2

    return wrapper


class RateLimiter:
    """Allows at most `max_calls` calls in any `period` seconds (default: 1 second).

    Brokers reject orders sent too fast (Dhan's limit is in MAX_ORDERS_PER_SECOND).
    Call limiter.wait() before each call, or use `with limiter:` or `@limiter`.
    Safe to share between threads.
    """

    # Set the limit. clock and sleep can be swapped for fake ones in tests.
    def __init__(self, max_calls: int | None = None, period: float = 1.0,
                 clock=time.monotonic, sleep=time.sleep):
        self.max_calls = max_calls if max_calls is not None else config.MAX_ORDERS_PER_SECOND
        if self.max_calls < 1 or period <= 0:
            raise ValueError("max_calls must be at least 1 and period above 0")
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._calls = deque()          # times of the most recent calls
        self._lock = threading.Lock()

    # Block until another call is allowed; return how many seconds we waited.
    def wait(self) -> float:
        """Forget calls older than `period`; if the window is full, sleep until it is not."""
        waited = 0.0
        with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return waited
                pause = self.period - (now - self._calls[0])
                self._sleep(pause)
                waited += pause

    # `with limiter:` waits for a free slot before the block runs.
    def __enter__(self):
        self.wait()
        return self

    # Nothing to clean up when the `with` block ends.
    def __exit__(self, *exc):
        return False

    # `@limiter` above a function makes every call to it wait for a free slot.
    def __call__(self, func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            self.wait()
            return func(*args, **kwargs)
        return wrapper
