"""Fixed-window request limits. A full window returns Retry-After seconds."""

from __future__ import annotations

from typing import Any, cast

import redis.asyncio as redis_async
from redis.exceptions import RedisError

LOGIN_RPS = 5
USER_RPS = 30
TENANT_WRITE_RPS = 30

_SCRIPT = """
local current = redis.call('incr', KEYS[1])
if current == 1 then
  redis.call('expire', KEYS[1], 1)
end
if current > tonumber(ARGV[1]) then
  local ttl = redis.call('ttl', KEYS[1])
  if ttl < 1 then
    ttl = 1
  end
  return ttl
end
return 0
"""


def login_key(ip: str) -> str:
    return f"rl:login:{ip}"


def user_key(user_id: str) -> str:
    return f"rl:user:{user_id}"


def write_key(tenant_id: str) -> str:
    return f"rl:write:{tenant_id}"


async def reserve(client: redis_async.Redis, key: str, limit: int) -> int:
    """Return 0 when the call is inside the limit, otherwise the Retry-After delay."""
    try:
        result = await cast(Any, client.eval(_SCRIPT, 1, key, str(limit)))
    except RedisError:
        return 0
    return int(result)
