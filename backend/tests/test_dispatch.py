"""Dial-path rules: script lines, caller health, reject cooldown, and no-agent callback."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.models import Call, CallerNumber, Campaign, ConsentRecord, Contact, ContactList, ScenarioTemplate, Task, Tenant, Trunk, User
from app.models.extra import CallTurn, NumberHealthStat, ScriptNode
from app.security.crypto import encrypt_phone, phone_hash
from app.security.passwords import hash_password
from app.services.control import open_complaint
from app.services.dialer import dial_pending, dispatch_due, schedule_retries
from app.tasks import celery_app

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)


async def _world(
    session: AsyncSession, code: str, *, phone: str
) -> tuple[Tenant, Trunk, Campaign, Contact, CallerNumber]:
    settings = load_settings()
    tenant = Tenant(
        name=code,
        code=code,
        status="active",
        allowed_scenarios=["notification"],
        timezone="Asia/Shanghai",
        is_demo=False,
        created_at=NOW,
        updated_at=NOW,
    )
    template = ScenarioTemplate(
        tenant_id=None,
        name="n",
        scenario_code="notification",
        status="approved",
        nodes={},
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all([tenant, template])
    await session.flush()
    contact_list = ContactList(tenant_id=tenant.id, name="l", scenario_code="notification")
    trunk = Trunk(
        tenant_id=tenant.id,
        name="t",
        mode="byoc",
        provider="byoc",
        sip_host="sip.example.net",
        allowed_scenarios=["notification"],
        status="active",
    )
    session.add_all([contact_list, trunk])
    await session.flush()
    caller = CallerNumber(tenant_id=tenant.id, trunk_id=trunk.id, number="02180001111", region="SH", status="active")
    hashed = phone_hash(phone, settings.phone_pepper)
    contact = Contact(
        tenant_id=tenant.id,
        list_id=contact_list.id,
        phone_enc=encrypt_phone(phone, settings.field_enc_key),
        phone_last4=phone[-4:],
        phone_hash=hashed,
        dial_class="dialable",
    )
    session.add_all(
        [
            caller,
            contact,
            ConsentRecord(
                tenant_id=tenant.id,
                phone_enc=encrypt_phone(phone, settings.field_enc_key),
                phone_last4=phone[-4:],
                phone_hash=hashed,
                scenario_code="notification",
                source="contract",
                consented_at=NOW - timedelta(days=1),
            ),
            ScriptNode(
                template_id=template.id,
                tenant_id=tenant.id,
                node_key="n_open",
                node_type="mandatory",
                content={"text": "必须播报的开场要点。", "next": "n_hand"},
            ),
            ScriptNode(
                template_id=template.id,
                tenant_id=tenant.id,
                node_key="n_hand",
                node_type="mandatory",
                content={"text": "为您转接人工。", "action": "transfer"},
            ),
        ]
    )
    await session.flush()
    campaign = Campaign(
        tenant_id=tenant.id,
        name="提醒",
        scenario_code="notification",
        template_id=template.id,
        template_version=1,
        list_id=contact_list.id,
        status="running",
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        trunk_id=trunk.id,
        caller_number_id=caller.id,
        freq_per_phone_daily=6,
        retry_policy={"max_retry": 1, "interval_min": [60]},
    )
    session.add(campaign)
    await session.flush()
    return tenant, trunk, campaign, contact, caller


async def test_script_and_caller_are_used(session_factory: async_sessionmaker[AsyncSession], redis_client) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, caller = await _world(session, "dispatch-a", phone="13800005555")
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        started = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW, heard="好的"
        )
        call = await session.scalar(select(Call).where(Call.campaign_id == campaign.id))
        spoken = await session.scalar(select(CallTurn.text_content).where(CallTurn.speaker == "assistant"))
        callback = await session.scalar(select(Task).where(Task.attempt_no == 2))
        health = await session.scalar(select(NumberHealthStat).where(NumberHealthStat.caller_number_id == caller.id))
        assert started == 1
        assert call is not None and call.caller_number == "02180001111" and call.status == "transfer_failed"
        assert spoken is not None and "必须播报的开场要点。" in spoken and "为您转接人工。" in spoken
        assert callback is not None and callback.next_run_at == NOW + timedelta(minutes=30)
        assert health is not None and health.answer_rate == 0


async def test_future_callback_waits_until_it_is_due(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "dispatch-wait", phone="13800005556")
        session.add(
            Task(
                tenant_id=tenant.id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                attempt_no=1,
                status="pending",
                next_run_at=NOW + timedelta(minutes=30),
            )
        )
        await session.flush()
        early = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        waiting = await session.scalar(select(Task).where(Task.campaign_id == campaign.id))
        assert early == 0
        assert waiting is not None and waiting.status == "pending"
        started = await dial_pending(
            session,
            campaign=campaign,
            tenant=tenant,
            trunk=trunk,
            settings=settings,
            redis=redis_client,
            now=NOW + timedelta(minutes=30),
        )
        assert started == 1


async def test_dispatch_due_skips_paused_campaigns_and_future_tasks(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        due_tenant, _trunk, due_campaign, due_contact, _caller = await _world(session, "dispatch-due", phone="13800005557")
        paused_tenant, _paused_trunk, paused, paused_contact, _paused_caller = await _world(
            session, "dispatch-paused", phone="13800005558"
        )
        future_tenant, _future_trunk, future, future_contact, _future_caller = await _world(
            session, "dispatch-future", phone="13800005559"
        )
        paused.status = "paused"
        session.add_all(
            [
                Task(
                    tenant_id=due_tenant.id,
                    campaign_id=due_campaign.id,
                    contact_id=due_contact.id,
                    attempt_no=1,
                    status="pending",
                    next_run_at=NOW,
                ),
                Task(
                    tenant_id=paused_tenant.id,
                    campaign_id=paused.id,
                    contact_id=paused_contact.id,
                    attempt_no=1,
                    status="pending",
                    next_run_at=NOW,
                ),
                Task(
                    tenant_id=future_tenant.id,
                    campaign_id=future.id,
                    contact_id=future_contact.id,
                    attempt_no=1,
                    status="pending",
                    next_run_at=NOW + timedelta(minutes=30),
                ),
            ]
        )
        await session.flush()
        started = await dispatch_due(session, settings=settings, redis=redis_client, now=NOW)
        paused_task = await session.scalar(select(Task).where(Task.campaign_id == paused.id))
        future_task = await session.scalar(select(Task).where(Task.campaign_id == future.id))
        due_call = await session.scalar(select(Call).where(Call.campaign_id == due_campaign.id))
        assert started == 1
        assert paused_task is not None and paused_task.status == "pending"
        assert future_task is not None and future_task.status == "pending"
        assert due_call is not None


def test_due_dispatch_runs_every_minute() -> None:
    entry = celery_app.conf.beat_schedule["dispatch-due-campaigns"]
    assert entry["task"] == "dispatch_due_campaigns"
    assert entry["schedule"] == timedelta(minutes=1)
    assert celery_app.conf.task_annotations["dispatch_due_campaigns"]["time_limit"] == 60
    assert celery_app.conf.task_routes["dispatch_due_campaigns"]["queue"] == "dispatch"


async def test_blocked_caller_is_not_dialed(session_factory: async_sessionmaker[AsyncSession], redis_client) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, caller = await _world(session, "dispatch-b", phone="13800006666")
        caller.status = "blocked"
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        started = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        task = await session.scalar(select(Task).where(Task.campaign_id == campaign.id))
        call = await session.scalar(select(Call).where(Call.campaign_id == campaign.id))
        assert started == 0
        assert task is not None and task.status == "blocked"
        assert call is not None and call.block_reason == "caller_blocked"


async def test_rejected_call_is_not_retried_the_same_day(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "dispatch-c", phone="13800007777")
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW, heard="拒接"
        )
        assert await schedule_retries(session, campaign, NOW) == 0
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=2, status="pending"))
        await session.flush()
        started = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW, heard="好的"
        )
        blocked = await session.scalar(select(Call).where(Call.status == "blocked_compliance"))
        assert started == 0
        assert blocked is not None and blocked.block_reason == "rejected_same_day"


async def test_reject_burst_pauses_the_campaign(session_factory: async_sessionmaker[AsyncSession], redis_client) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "dispatch-d", phone="13800008888")
        settings_key = settings.field_enc_key
        for index in range(4):
            other_phone = f"1390000000{index}"
            other = Contact(
                tenant_id=tenant.id,
                list_id=contact.list_id,
                phone_enc=encrypt_phone(other_phone, settings_key),
                phone_last4=other_phone[-4:],
                phone_hash=phone_hash(other_phone, settings.phone_pepper),
                dial_class="dialable",
            )
            session.add(other)
            await session.flush()
            session.add(
                Call(
                    tenant_id=tenant.id,
                    campaign_id=campaign.id,
                    contact_id=other.id,
                    call_uuid=uuid.uuid4(),
                    status="rejected",
                    trunk_id=trunk.id,
                    template_version=1,
                    start_at=NOW,
                    end_at=NOW,
                )
            )
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW, heard="拒接"
        )
        assert campaign.status == "paused"


async def test_one_complaint_blocks_a_caller_at_the_stop_rate(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, caller = await _world(session, "dispatch-e", phone="13800009999")
        user = User(
            tenant_id=tenant.id,
            username="dispatch-e-admin",
            password_hash=hash_password("password12"),
            display_name="admin",
            role="tenant_admin",
            must_change_password=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(user)
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW, heard="好的"
        )
        call = await session.scalar(select(Call).where(Call.campaign_id == campaign.id, Call.status == "transfer_failed"))
        assert call is not None
        await open_complaint(
            session,
            tenant_id=tenant.id,
            phone="13800009999",
            detail="号码骚扰",
            settings=settings,
            user_id=user.id,
            trace_id="trace",
            call_id=call.id,
        )
        assert caller.complaint_count_30d == 1
        assert caller.status == "blocked"
