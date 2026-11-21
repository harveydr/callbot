"""Dispatch worker. Due callbacks and retries leave the start request and run here."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import redis.asyncio as redis_async
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.compliance.offwindow import flag_offwindow_calls
from app.services.ops_alerts import raise_ops_alerts
from app.config import Settings, load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.services.dialer import dispatch_campaign_due, dispatch_due
from app.tasks import celery_app


async def _open() -> tuple[Settings, AsyncEngine, async_sessionmaker[AsyncSession], redis_async.Redis]:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    redis = redis_async.from_url(settings.redis_url, decode_responses=True)
    return settings, engine, make_session_factory(engine), redis


async def _run_one(campaign_id: str, tenant_id: str) -> str:
    settings, engine, factory, redis = await _open()
    try:
        async with factory() as session:
            await set_bypass(session)
            started = await dispatch_campaign_due(
                session,
                campaign_id=uuid.UUID(campaign_id),
                tenant_id=uuid.UUID(tenant_id),
                settings=settings,
                redis=redis,
                now=datetime.now(timezone.utc),
            )
            await session.commit()
            return f"started:{started}"
    finally:
        await redis.aclose()
        await engine.dispose()


async def _run_due() -> str:
    settings, engine, factory, redis = await _open()
    try:
        async with factory() as session:
            moment = datetime.now(timezone.utc)
            await set_bypass(session)
            flagged = await flag_offwindow_calls(session, moment)
            started = await dispatch_due(session, settings=settings, redis=redis, now=moment)
            ops = await raise_ops_alerts(session, redis, now=moment)
            await session.commit()
            return f"started:{started} offwindow:{flagged} ops:{ops}"
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="dispatch_campaign")
def dispatch_campaign(campaign_id: str, tenant_id: str) -> str:
    return asyncio.run(_run_one(campaign_id, tenant_id))


@celery_app.task(name="dispatch_due_campaigns")
def dispatch_due_campaigns() -> str:
    return asyncio.run(_run_due())
