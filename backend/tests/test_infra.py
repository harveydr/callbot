"""Day-one proof that pytest, asyncio, PostgreSQL, and Redis work together."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import set_bypass, set_tenant
from app.models import Tenant
from app.services.tokens import TokenBucket


async def test_postgres_roundtrip_and_rls_hides_unset_tenant(session_factory) -> None:
    tenant_id = uuid.uuid4()
    async with session_factory() as session:
        await set_bypass(session)
        session.add(
            Tenant(
                id=tenant_id,
                name="基础设施租户",
                code=f"infra-{tenant_id.hex[:8]}",
                status="active",
                allowed_scenarios=["notification"],
                timezone="Asia/Shanghai",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        found = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        assert found is not None
        assert found.code.startswith("infra-")

    async with session_factory() as session:
        hidden = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        assert hidden is None


async def test_redis_db15_and_concurrency_token(redis_client) -> None:
    await redis_client.set("callbot:hello", "ready")
    assert await redis_client.get("callbot:hello") == "ready"

    bucket = TokenBucket(redis_client, prefix="test:")
    granted = [await bucket.acquire("tenant:demo:conc", limit=10) for _ in range(10)]
    assert granted == [True] * 10
    assert await bucket.acquire("tenant:demo:conc", limit=10) is False
    await bucket.release("tenant:demo:conc")
    assert await bucket.acquire("tenant:demo:conc", limit=10) is True
