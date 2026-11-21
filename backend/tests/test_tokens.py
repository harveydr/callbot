"""The eleventh live call waits. A hung-up token returns, and an abandoned token expires."""

from __future__ import annotations

import asyncio

from app.services.tokens import TokenBucket


async def test_eleventh_call_waits_until_one_hangs_up(redis_client) -> None:
    bucket = TokenBucket(redis_client, prefix="test:")
    for _ in range(10):
        assert await bucket.acquire("live", limit=10, ttl_sec=30)
    assert await bucket.acquire("live", limit=10, ttl_sec=30) is False
    assert await bucket.release("live") == 9
    assert await bucket.acquire("live", limit=10, ttl_sec=30)


async def test_abandoned_token_expires(redis_client) -> None:
    bucket = TokenBucket(redis_client, prefix="test:")
    assert await bucket.acquire("orphan", limit=1, ttl_sec=1)
    assert await bucket.acquire("orphan", limit=1, ttl_sec=1) is False
    await asyncio.sleep(1.1)
    assert await bucket.acquire("orphan", limit=1, ttl_sec=30)
