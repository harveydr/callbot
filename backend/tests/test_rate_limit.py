"""Login, user, and tenant write limits answer 429 with Retry-After."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.main import create_app
from app.security.rate_limit import write_key
from tests.test_product import PASSWORD, _tenant, _user


@pytest.fixture
async def client(database: None):
    del database
    application = create_app()
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def test_the_sixth_login_from_one_address_waits(client: AsyncClient) -> None:
    body = {"username": "missing-user", "password": PASSWORD}
    for _ in range(5):
        response = await client.post("/api/auth/login", json=body)
        assert response.status_code == 401
    blocked = await client.post("/api/auth/login", json=body)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "1"
    assert blocked.json()["code"] == "RATE_LIMITED"


async def test_the_thirty_first_request_waits(client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "rate-user")
        await _user(session, role="tenant_admin", username="rate-user", tenant=tenant)
        await session.commit()
    logged = await client.post("/api/auth/login", json={"username": "rate-user", "password": PASSWORD})
    assert logged.status_code == 200
    headers = {"Authorization": f"Bearer {logged.json()['data']['access_token']}"}
    for _ in range(30):
        seen = await client.get("/api/users/me", headers=headers)
        assert seen.status_code == 200
    blocked = await client.get("/api/users/me", headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "1"


async def test_a_full_tenant_write_window_waits_while_reads_continue(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "rate-write")
        await _user(session, role="tenant_admin", username="rate-write", tenant=tenant)
        await session.commit()
        tenant_id = str(tenant.id)
    logged = await client.post("/api/auth/login", json={"username": "rate-write", "password": PASSWORD})
    assert logged.status_code == 200
    headers = {"Authorization": f"Bearer {logged.json()['data']['access_token']}"}
    await redis_client.set(write_key(tenant_id), "30", ex=5)
    blocked = await client.post("/api/t/complaints", headers=headers, json={"phone": "13800000000", "detail": "x"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    seen = await client.get("/api/users/me", headers=headers)
    assert seen.status_code == 200
