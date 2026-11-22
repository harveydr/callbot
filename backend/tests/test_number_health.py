"""A caller under 0.15 answer rate in 30 minutes is flagged. The number stays active."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import Call, CallEvent
from app.models.extra import Alert, NumberHealthStat
from app.services.number_health import refresh_caller_health
from tests.test_dispatch import NOW, _world


def _call(tenant_id, campaign_id, contact_id, trunk_id, number, start: datetime, status: str) -> Call:
    return Call(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        contact_id=contact_id,
        call_uuid=uuid.uuid4(),
        status=status,
        caller_number=number,
        trunk_id=trunk_id,
        template_version=1,
        start_at=start,
    )


async def test_five_misses_in_thirty_minutes_warn_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, caller = await _world(session, "health-low", phone="13800007771")
        for offset in range(5):
            session.add(
                _call(
                    tenant.id,
                    campaign.id,
                    contact.id,
                    trunk.id,
                    caller.number,
                    NOW - timedelta(minutes=offset),
                    "no_answer",
                )
            )
        await session.flush()
        first = await refresh_caller_health(session, caller, NOW)
        second = await refresh_caller_health(session, caller, NOW)
        alerts = (await session.scalars(select(Alert).where(Alert.tenant_id == tenant.id))).all()
        event = await session.scalar(select(CallEvent).where(CallEvent.event_type == "call_answer_rate"))
        health = await session.scalar(select(NumberHealthStat).where(NumberHealthStat.caller_number_id == caller.id))
        assert first == Decimal("0.0000")
        assert second == Decimal("0.0000")
        assert caller.status == "active"
        assert health is not None and health.answer_rate == Decimal("0.0000")
        assert len(alerts) == 1
        assert alerts[0].severity == "warning" and alerts[0].code == "call_answer_rate"
        assert alerts[0].message == "2026-11-22 10:00 主叫 02180001111 接通率 0.0000，低于 0.15，号码可能被标记"
        assert event is not None and event.detail["hint"] == "号码可能被标记"


async def test_one_answer_in_five_and_old_misses_do_not_warn(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, caller = await _world(session, "health-ok", phone="13800007772")
        session.add(_call(tenant.id, campaign.id, contact.id, trunk.id, caller.number, NOW, "completed"))
        for offset in range(4):
            session.add(
                _call(
                    tenant.id,
                    campaign.id,
                    contact.id,
                    trunk.id,
                    caller.number,
                    NOW - timedelta(minutes=offset + 1),
                    "no_answer",
                )
            )
        await session.flush()
        held = await refresh_caller_health(session, caller, NOW)
        assert held == Decimal("0.2000")
        assert (await session.scalars(select(Alert).where(Alert.code == "call_answer_rate"))).all() == []

        for offset in range(5):
            session.add(
                _call(
                    tenant.id,
                    campaign.id,
                    contact.id,
                    trunk.id,
                    caller.number,
                    NOW - timedelta(minutes=40 + offset),
                    "no_answer",
                )
            )
        await session.flush()
        mixed = await refresh_caller_health(session, caller, NOW)
        alerts = (await session.scalars(select(Alert).where(Alert.code == "call_answer_rate"))).all()
        assert mixed == Decimal("0.1000")
        assert alerts == []
