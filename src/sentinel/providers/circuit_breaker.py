"""Redis-backed circuit breaker per provider.

States: CLOSED (normal) -> OPEN (failing, fail fast) -> HALF_OPEN (probe).

The state is stored in Redis so that all gateway instances share the
same view — otherwise a single bad instance keeps hammering the upstream
while others have already given up.

The implementation deliberately uses simple INCR + EXPIRE rather than a
Lua script, because debuggability matters more than the marginal
performance for a gateway that's already bounded by upstream latency.
"""

from __future__ import annotations

import time
from enum import StrEnum

from redis.asyncio import Redis

from sentinel.core.logging import get_logger

log = get_logger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider circuit breaker.

    Parameters
    ----------
    redis
        Async Redis client.
    name
        Provider name, used as part of the Redis key namespace.
    failure_threshold
        Consecutive failures that open the circuit.
    recovery_seconds
        How long the circuit stays open before a single probe is allowed.
    """

    def __init__(
        self,
        redis: Redis,
        name: str,
        failure_threshold: int = 5,
        recovery_seconds: int = 30,
    ) -> None:
        self._redis = redis
        self._name = name
        self._threshold = failure_threshold
        self._recovery = recovery_seconds

    def _key(self, suffix: str) -> str:
        return f"cb:{self._name}:{suffix}"

    async def state(self) -> CircuitState:
        opened_at_raw = await self._redis.get(self._key("opened_at"))
        if opened_at_raw is None:
            return CircuitState.CLOSED

        opened_at = float(opened_at_raw)
        if time.time() - opened_at >= self._recovery:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    async def record_success(self) -> None:
        """Reset failure counter and close the circuit."""
        pipe = self._redis.pipeline()
        pipe.delete(self._key("failures"))
        pipe.delete(self._key("opened_at"))
        await pipe.execute()

    async def record_failure(self) -> None:
        """Increment failure count; open the circuit if threshold crossed."""
        failures = await self._redis.incr(self._key("failures"))
        # Failures expire after 60s of inactivity so transient blips don't
        # accumulate forever.
        await self._redis.expire(self._key("failures"), 60)

        if failures >= self._threshold:
            await self._redis.set(self._key("opened_at"), time.time())
            log.warning(
                "circuit.opened",
                provider=self._name,
                failures=failures,
                recovery_seconds=self._recovery,
            )

    async def is_callable(self) -> bool:
        """Return True if the caller should attempt the upstream call."""
        state = await self.state()
        return state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)
