"""Unit tests for the circuit breaker.

Uses an in-memory fake of the Redis async client surface we touch. Keeps
the test pure and fast (no Redis container needed for unit tests).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from sentinel.providers.circuit_breaker import CircuitBreaker, CircuitState


class FakeRedis:
    """Just-enough Redis stand-in for CircuitBreaker tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: object) -> None:
        self._store[key] = str(value)

    async def incr(self, key: str) -> int:
        new = int(self._store.get(key, "0")) + 1
        self._store[key] = str(new)
        return new

    async def expire(self, key: str, _seconds: int) -> None:
        return

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, parent: FakeRedis) -> None:
        self._parent = parent
        self._ops: list[tuple[str, str]] = []

    def delete(self, key: str) -> FakePipeline:
        self._ops.append(("delete", key))
        return self

    async def execute(self) -> None:
        for op, key in self._ops:
            if op == "delete":
                self._parent._store.pop(key, None)


@pytest.mark.asyncio
class TestCircuitBreaker:
    async def test_starts_closed(self) -> None:
        cb = CircuitBreaker(FakeRedis(), "test")  # type: ignore[arg-type]
        assert await cb.state() == CircuitState.CLOSED
        assert await cb.is_callable() is True

    async def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(FakeRedis(), "test", failure_threshold=3)  # type: ignore[arg-type]
        for _ in range(3):
            await cb.record_failure()
        assert await cb.state() == CircuitState.OPEN
        assert await cb.is_callable() is False

    async def test_half_open_after_recovery(self) -> None:
        redis = FakeRedis()
        cb = CircuitBreaker(redis, "test", failure_threshold=2, recovery_seconds=1)  # type: ignore[arg-type]
        await cb.record_failure()
        await cb.record_failure()
        assert await cb.state() == CircuitState.OPEN

        # Simulate time passing.
        redis._store["cb:test:opened_at"] = str(time.time() - 5)
        assert await cb.state() == CircuitState.HALF_OPEN
        assert await cb.is_callable() is True

    async def test_success_resets(self) -> None:
        cb = CircuitBreaker(FakeRedis(), "test", failure_threshold=2)  # type: ignore[arg-type]
        await cb.record_failure()
        await cb.record_failure()
        assert not await cb.is_callable()
        await cb.record_success()
        assert await cb.is_callable()

    async def test_failures_can_run_concurrently(self) -> None:
        cb = CircuitBreaker(FakeRedis(), "test", failure_threshold=10)  # type: ignore[arg-type]
        await asyncio.gather(*(cb.record_failure() for _ in range(5)))
        # Not yet at threshold
        assert await cb.is_callable()
