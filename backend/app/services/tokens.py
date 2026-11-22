"""Two-level concurrency tokens. Keys used in tests are prefixed with test:."""

from __future__ import annotations

from typing import Any, cast

import redis.asyncio as redis_async

_ACQUIRE = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = tonumber(redis.call('get', key) or '0')
if current >= limit then
  return 0
end
current = redis.call('incr', key)
if current == 1 then
  redis.call('expire', key, ttl)
end
return 1
"""

_RELEASE = """
local key = KEYS[1]
local current = tonumber(redis.call('get', key) or '0')
if current <= 0 then
  redis.call('del', key)
  return 0
end
return redis.call('decr', key)
"""


class TokenBucket:
    def __init__(self, client: redis_async.Redis, *, prefix: str = "") -> None:
        self._client = client
        self._prefix = prefix

    def _key(self, name: str) -> str:
        return f"{self._prefix}{name}"

    async def acquire(self, name: str, *, limit: int, ttl_sec: int = 3600) -> bool:
        result = await cast(Any, self._client.eval(self._script_acquire(), 1, self._key(name), str(limit), str(ttl_sec)))
        return int(result) == 1

    async def release(self, name: str) -> int:
        result = await cast(Any, self._client.eval(self._script_release(), 1, self._key(name)))
        return int(result)

    async def acquire_all(self, layers: list[tuple[str, int]], *, ttl_sec: int) -> bool:
        """Take every layer or none. A failed layer rolls back the ones already held."""
        held: list[str] = []
        for name, limit in layers:
            if not await self.acquire(name, limit=limit, ttl_sec=ttl_sec):
                await self.release_all(held)
                return False
            held.append(name)
        return True

    async def release_all(self, names: list[str]) -> None:
        for name in names:
            await self.release(name)

    @staticmethod
    def _script_acquire() -> str:
        return _ACQUIRE

    @staticmethod
    def _script_release() -> str:
        return _RELEASE
