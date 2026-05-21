"""Token-bucket rate limiter.

Per-tenant, per-minute budget. Implemented as a sliding window counter
in Redis with a Lua script for atomicity — every gateway instance hits
the same Redis, and we can't tolerate a TOCTOU race that lets a tenant
burst past their limit.

Why sliding window instead of fixed bucket: fixed windows have the
classic boundary problem where a tenant can do 2x their limit by hitting
the end of one window and the start of the next. Sliding window
approximation (Cloudflare's algorithm) gets the right answer cheaply.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis

# Returns: (allowed: 0/1, remaining: int, retry_after_ms: int)
_LUA_SLIDING_WINDOW = """
local current_key = KEYS[1]
local previous_key = KEYS[2]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', current_key) or '0')
local previous = tonumber(redis.call('GET', previous_key) or '0')

local elapsed_in_window = now_ms % window_ms
local weight = 1 - (elapsed_in_window / window_ms)
local estimated = math.floor(previous * weight) + current

if estimated >= limit then
    local retry = window_ms - elapsed_in_window
    return {0, 0, retry}
end

redis.call('INCR', current_key)
redis.call('PEXPIRE', current_key, window_ms * 2)
return {1, limit - estimated - 1, 0}
"""


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_ms: int


class RateLimiter:
    def __init__(self, redis: Redis, window_seconds: int = 60) -> None:
        self._redis = redis
        self._window_ms = window_seconds * 1000
        self._script = redis.register_script(_LUA_SLIDING_WINDOW)

    async def check(self, key: str, limit: int) -> RateLimitResult:
        """Check and decrement the bucket for ``key``. Atomic."""
        now_ms = int(time.time() * 1000)
        window = now_ms // self._window_ms
        current_key = f"rl:{key}:{window}"
        previous_key = f"rl:{key}:{window - 1}"

        result = await self._script(
            keys=[current_key, previous_key],
            args=[limit, self._window_ms, now_ms],
        )
        allowed, remaining, retry_after_ms = result
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=int(remaining),
            retry_after_ms=int(retry_after_ms),
        )
