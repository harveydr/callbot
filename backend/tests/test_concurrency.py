"""Live calls stop at the tenant and trunk caps, and a locked task is not dialed twice."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.models import Call, Campaign, ConsentRecord, Contact, Task, Tenant, Trunk
from app.security.crypto import encrypt_phone, phone_hash
from app.services.dialer import dial_pending
from app.services.tokens import TokenBucket
from tests.test_dispatch import NOW, _world


async def _another(session: AsyncSession, tenant: Tenant, campaign: Campaign, phone: str) -> Contact:
    settings = load_settings()
    hashed = phone_hash(phone, settings.phone_pepper)
    contact = Contact(
        tenant_id=tenant.id,
        list_id=campaign.list_id,
        phone_enc=encrypt_phone(phone, settings.field_enc_key),
        phone_last4=phone[-4:],
        phone_hash=hashed,
        dial_class="dialable",
    )
    session.add_all(
        [
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
        ]
    )
    await session.flush()
    session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
    return contact


async def test_tenant_cap_stops_the_third_call_until_a_token_returns(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "cap-tenant", phone="13800007001")
        tenant.max_concurrency = 2
        trunk.max_concurrency = 10
        campaign.max_concurrency = 10
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await _another(session, tenant, campaign, "13800007002")
        await _another(session, tenant, campaign, "13800007003")
        await session.flush()
        started = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        pending = int(
            await session.scalar(
                select(func.count())
                .select_from(Task)
                .where(
                    Task.campaign_id == campaign.id,
                    Task.status == "pending",
                    Task.attempt_no == 1,
                )
            )
            or 0
        )
        assert started == 2
        assert pending == 1
        assert int(await redis_client.get(f"tenant:{tenant.id}:conc") or 0) == 0
        assert int(await redis_client.get(f"trunk:{trunk.id}:conc") or 0) == 0
        bucket = TokenBucket(redis_client)
        assert await bucket.acquire(f"tenant:{tenant.id}:conc", limit=2, ttl_sec=60)
        assert await bucket.acquire(f"tenant:{tenant.id}:conc", limit=2, ttl_sec=60)
        blocked = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        assert blocked == 0
        await bucket.release(f"tenant:{tenant.id}:conc")
        await bucket.release(f"tenant:{tenant.id}:conc")
        resumed = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        assert resumed == 1
        assert int(await redis_client.get(f"tenant:{tenant.id}:conc") or 0) == 0


async def test_a_full_trunk_blocks_even_when_the_tenant_has_room(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "cap-trunk", phone="13800007011")
        tenant.max_concurrency = 10
        trunk.max_concurrency = 2
        campaign.max_concurrency = 10
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        bucket = TokenBucket(redis_client)
        assert await bucket.acquire(f"trunk:{trunk.id}:conc", limit=2, ttl_sec=60)
        assert await bucket.acquire(f"trunk:{trunk.id}:conc", limit=2, ttl_sec=60)
        started = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        waiting = await session.scalar(select(Task).where(Task.campaign_id == campaign.id))
        assert started == 0
        assert waiting is not None and waiting.status == "pending"
        await bucket.release(f"trunk:{trunk.id}:conc")
        await bucket.release(f"trunk:{trunk.id}:conc")
        assert int(await redis_client.get(f"tenant:{tenant.id}:conc") or 0) == 0


async def test_a_locked_task_is_left_for_the_worker_that_holds_it(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as setup:
        await set_bypass(setup)
        tenant, trunk, campaign, contact, _caller = await _world(session=setup, code="cap-lock", phone="13800007021")
        tenant.max_concurrency = 10
        trunk.max_concurrency = 10
        campaign.max_concurrency = 10
        first = Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending")
        setup.add(first)
        second = await _another(setup, tenant, campaign, "13800007022")
        await setup.commit()
        tenant_id, trunk_id, campaign_id, first_id, second_id = tenant.id, trunk.id, campaign.id, first.id, second.id
    async with session_factory() as locker:
        await set_bypass(locker)
        locked = await locker.scalar(select(Task).where(Task.id == first_id).with_for_update())
        assert locked is not None
        async with session_factory() as worker:
            await set_bypass(worker)
            loaded_tenant = await worker.get(Tenant, tenant_id)
            loaded_trunk = await worker.get(Trunk, trunk_id)
            loaded_campaign = await worker.get(Campaign, campaign_id)
            assert loaded_tenant is not None and loaded_trunk is not None and loaded_campaign is not None
            started = await dial_pending(
                worker,
                campaign=loaded_campaign,
                tenant=loaded_tenant,
                trunk=loaded_trunk,
                settings=settings,
                redis=redis_client,
                now=NOW,
            )
            dialed = await worker.scalar(select(Call.contact_id).where(Call.campaign_id == campaign_id))
            assert started == 1
            assert dialed == second_id
        still = await locker.scalar(select(Task.status).where(Task.id == first_id))
        assert still == "pending"


async def test_a_failed_dial_returns_the_token(
    session_factory: async_sessionmaker[AsyncSession], redis_client, monkeypatch
) -> None:
    settings = load_settings()

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("trunk down")

    monkeypatch.setattr("app.services.dialer._place", _boom)
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "cap-fail", phone="13800007031")
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        try:
            await dial_pending(
                session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
            )
        except RuntimeError as exc:
            assert str(exc) == "trunk down"
        else:
            raise AssertionError("dial should surface the trunk failure")
        failed = await session.scalar(select(Task.status).where(Task.campaign_id == campaign.id))
        assert failed == "failed"
        assert int(await redis_client.get(f"tenant:{tenant.id}:conc") or 0) == 0
        assert int(await redis_client.get(f"trunk:{trunk.id}:conc") or 0) == 0
        assert int(await redis_client.get(f"campaign:{campaign.id}:conc") or 0) == 0
