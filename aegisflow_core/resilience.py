"""Reusable resilience primitives: breaker, retry, bulkhead, timeout."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TypeVar

from .config import settings
from .observability import breaker_state

T = TypeVar("T")


class TransientError(Exception):
    """Worth retrying (timeouts, broker hiccups, injected latency faults)."""


class PermanentError(Exception):
    """Never retried - bad input, unknown model, payload too large."""


class CircuitOpen(TransientError):
    pass


class BulkheadFull(TransientError):
    pass


@dataclass
class CircuitBreaker:
    """Classic three-state breaker with a single trial call in half-open."""

    name: str
    failure_threshold: int = settings.breaker_failure_threshold
    reset_timeout_s: float = settings.breaker_reset_timeout_s
    half_open_max: int = 1
    _failures: int = field(default=0, init=False)
    _state: str = field(default="closed", init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._publish()

    @property
    def state(self) -> str:
        if self._state == "open" and (time.monotonic() - self._opened_at) >= self.reset_timeout_s:
            self._state = "half_open"
            self._half_open_calls = 0
            self._publish()
        return self._state

    def _publish(self) -> None:
        breaker_state.labels(name=self.name).set({"closed": 0, "half_open": 1, "open": 2}[self._state])

    def allows(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open":
            if self._half_open_calls < self.half_open_max:
                self._half_open_calls += 1
                return True
            return False
        return False

    def record_success(self) -> None:
        self._failures = 0
        if self._state != "closed":
            self._state = "closed"
            self._publish()

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == "half_open" or self._failures >= self.failure_threshold:
            self._state = "open"
            self._opened_at = time.monotonic()
            self._publish()

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if not self.allows():
            raise CircuitOpen(f"circuit '{self.name}' is open")
        try:
            result = await fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def snapshot(self) -> dict:
        return {"name": self.name, "state": self.state, "failures": self._failures}


def backoff_delay(attempt: int, base_ms: int | None = None, max_ms: int | None = None) -> float:
    """Exponential backoff with full jitter, in seconds."""
    base = (base_ms if base_ms is not None else settings.retry_base_ms) / 1000
    ceiling = (max_ms if max_ms is not None else settings.retry_max_ms) / 1000
    window = min(ceiling, base * (2 ** max(0, attempt - 1)))
    return random.uniform(base * 0.5, max(base * 0.5, window))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    retry_on: Iterable[type[BaseException]] = (TransientError, TimeoutError, ConnectionError),
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    retry_types = tuple(retry_on)
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_types as exc:
            last = exc
            if attempt >= attempts:
                break
            if on_retry:
                on_retry(attempt, exc)
            await asyncio.sleep(backoff_delay(attempt))
    raise last if last else RuntimeError("retry_async: no attempt executed")


class Bulkhead:
    """Bounded concurrency so one slow model cannot exhaust the process."""

    def __init__(self, limit: int, name: str = "default") -> None:
        self.name = name
        self.limit = limit
        self._sem = asyncio.Semaphore(limit)
        self.inflight = 0

    async def __aenter__(self) -> Bulkhead:
        acquired = await asyncio.wait_for(self._sem.acquire(), timeout=30)
        del acquired
        self.inflight += 1
        return self

    async def __aexit__(self, *_exc) -> None:
        self.inflight -= 1
        self._sem.release()

    @property
    def available(self) -> int:
        return max(0, self.limit - self.inflight)


async def with_timeout(coro: Awaitable[T], timeout_ms: int, label: str = "operation") -> T:
    try:
        return await asyncio.wait_for(coro, timeout=timeout_ms / 1000)
    except TimeoutError as exc:
        raise TransientError(f"{label} timed out after {timeout_ms}ms") from exc
