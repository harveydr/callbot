"""Async engine helpers. The session timezone is UTC."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        pool_recycle=1800,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(text("SELECT set_config('app.rls_bypass', 'off', true)"))
    await session.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})


async def set_bypass(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
    await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
