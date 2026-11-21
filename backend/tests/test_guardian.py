"""A minor contact is dialed only with guardian consent and a guardian script line."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.models import Call, ContactList, Qualification, Task
from app.models.extra import CallTurn, ImportJob, ScriptNode
from app.services.control import _parse_import
from app.services.dialer import dial_pending
from tests.test_dispatch import NOW, _world

_LINE = "请确认您是监护人后再继续。"


async def _pending(session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID, contact_id: uuid.UUID) -> None:
    task = await session.scalar(
        select(Task).where(Task.campaign_id == campaign_id, Task.contact_id == contact_id, Task.attempt_no == 1)
    )
    if task is None:
        session.add(
            Task(tenant_id=tenant_id, campaign_id=campaign_id, contact_id=contact_id, attempt_no=1, status="pending")
        )
    else:
        task.status = "pending"
    await session.flush()


async def _fresh_call(session: AsyncSession, contact_id: uuid.UUID, seen: set[uuid.UUID]) -> Call:
    rows = (await session.scalars(select(Call).where(Call.contact_id == contact_id))).all()
    fresh = [row for row in rows if row.id not in seen]
    assert len(fresh) == 1
    seen.add(fresh[0].id)
    return fresh[0]


async def test_minor_without_consent_or_script_is_blocked(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    seen: set[uuid.UUID] = set()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "minor-a", phone="13800006611")
        contact.minor = True
        await _pending(session, tenant.id, campaign.id, contact.id)
        blocked = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        first = await _fresh_call(session, contact.id, seen)
        assert blocked == 0
        assert first.status == "blocked_compliance" and first.block_reason == "guardian_consent_missing"

        session.add(
            Qualification(
                tenant_id=tenant.id,
                doc_type="guardian_consent",
                file_key="guardian-pending.pdf",
                status="pending",
            )
        )
        await _pending(session, tenant.id, campaign.id, contact.id)
        await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        pending_call = await _fresh_call(session, contact.id, seen)
        assert pending_call.block_reason == "guardian_consent_missing"

        approved = await session.scalar(select(Qualification).where(Qualification.file_key == "guardian-pending.pdf"))
        assert approved is not None
        approved.status = "approved"
        approved.expires_at = NOW
        await _pending(session, tenant.id, campaign.id, contact.id)
        await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        expired = await _fresh_call(session, contact.id, seen)
        assert expired.block_reason == "guardian_consent_missing"

        approved.status = "approved"
        approved.expires_at = NOW + timedelta(days=30)
        await _pending(session, tenant.id, campaign.id, contact.id)
        await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        missing_script = await _fresh_call(session, contact.id, seen)
        assert missing_script.block_reason == "guardian_script_missing"

        session.add(
            ScriptNode(
                template_id=campaign.template_id,
                tenant_id=tenant.id,
                node_key="n_guardian",
                node_type="mandatory",
                content={"text": _LINE, "guardian_check": True},
            )
        )
        await _pending(session, tenant.id, campaign.id, contact.id)
        started = await dial_pending(
            session,
            campaign=campaign,
            tenant=tenant,
            trunk=trunk,
            settings=settings,
            redis=redis_client,
            now=NOW,
            heard="好的",
        )
        placed = await _fresh_call(session, contact.id, seen)
        spoken = await session.scalar(
            select(CallTurn.text_content).where(CallTurn.call_id == placed.id, CallTurn.speaker == "assistant")
        )
        assert started == 1
        assert placed.status != "blocked_compliance"
        assert spoken is not None and _LINE in spoken and "必须播报的开场要点。" in spoken


async def test_import_sets_the_minor_mark(session_factory: async_sessionmaker[AsyncSession]) -> None:
    settings = load_settings()
    phone = "13800006612"
    async with session_factory() as session:
        await set_bypass(session)
        tenant, _trunk, _campaign, contact, _caller = await _world(session, "minor-import", phone=phone)
        listed = await session.get(ContactList, contact.list_id)
        assert listed is not None
        job = ImportJob(tenant_id=tenant.id, list_id=contact.list_id, file_md5="abc", status="running")
        session.add(job)
        await session.flush()
        marked = f"phone,name,minor\n{phone},学生,是\n".encode()
        await _parse_import(session, job=job, contact_list=listed, content=marked, settings=settings)
        await session.refresh(contact)
        assert contact.minor is True
        cleared = f"phone,name\n{phone},学生\n".encode()
        await _parse_import(session, job=job, contact_list=listed, content=cleared, settings=settings)
        await session.refresh(contact)
        assert contact.minor is False
