from __future__ import annotations
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, TypeVar

T = TypeVar("T")

@dataclass
class RateLimiter:
    requests_per_second: float
    _last_request: float = 0.0
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def wait(self, sleep: Callable[[float], None] = time.sleep) -> None:
        if self.requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        interval = 1.0 / self.requests_per_second
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._last_request + interval - now)
            if delay:
                sleep(delay)
            self._last_request = time.monotonic()

@dataclass(frozen=True)
class ExponentialBackoff:
    delays: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

    def run(self, operation: Callable[[], T], *, retryable: Callable[[Exception], bool], sleep: Callable[[float], None] = time.sleep) -> T:
        last_error: Exception | None = None
        for delay in (0.0, *self.delays):
            if delay:
                sleep(delay)
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if not retryable(exc) or delay == self.delays[-1]:
                    raise
        assert last_error is not None
        raise last_error
