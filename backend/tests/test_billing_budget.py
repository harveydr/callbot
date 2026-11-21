"""Monthly cap counts quoted lines only. At 100% new tasks stop and a live call stays up."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.errors import ApiError
from app.main import create_app
from app.models import Call, Task, Tenant, User
from app.models.extra import Alert, BillingItem
from app.security.passwords import hash_password
from app.services.billing_budget import apply_billing_budget
from app.services.control import billing_summary, start_campaign
from app.services.dialer import dial_pending, schedule_retries
from tests.test_dispatch import NOW, _world

PASSWORD = "password12"


@pytest.fixture
async def client(database: None):
    del database
    application = create_app()
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


def _item(tenant: Tenant, call: Call, amount: Decimal | None, created_at: datetime) -> BillingItem:
    return BillingItem(
        tenant_id=tenant.id,
        call_id=call.id,
        call_uuid=call.call_uuid,
        amount=amount,
        currency="CNY",
        rate_note="quoted" if amount is not None else "unquoted",
        created_at=created_at,
    )


async def test_reaching_the_cap_pauses_new_tasks_and_leaves_the_live_call(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client,
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "budget-stop", phone="13800006661")
        tenant.billing_budget = Decimal("10.0000")
        live = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=uuid.uuid4(),
            status="talking",
            trunk_id=trunk.id,
            template_version=1,
            start_at=NOW,
        )
        session.add(live)
        await session.flush()
        session.add(_item(tenant, live, Decimal("10.0000"), NOW))
        pending = Task(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            attempt_no=2,
            status="pending",
        )
        session.add(pending)
        await session.flush()
        started = await dial_pending(
            session,
            campaign=campaign,
            tenant=tenant,
            trunk=trunk,
            settings=settings,
            redis=redis_client,
            now=NOW,
        )
        again = await apply_billing_budget(session, tenant, NOW)
        alerts = (await session.scalars(select(Alert).where(Alert.tenant_id == tenant.id))).all()
        codes = [row.code for row in alerts]
        assert started == 0
        assert again is True
        assert campaign.status == "paused"
        assert live.status == "talking"
        assert pending.status == "pending"
        assert codes.count("billing_budget") == 1
        assert codes.count("billing_budget_exceeded") == 1
        assert all(row.severity == "critical" for row in alerts if row.code == "billing_budget_exceeded")


async def test_eighty_percent_warns_without_pausing(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "budget-warn", phone="13800006662")
        tenant.billing_budget = Decimal("10.0000")
        call = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=trunk.id,
            template_version=1,
            start_at=NOW,
        )
        session.add(call)
        await session.flush()
        session.add(_item(tenant, call, Decimal("8.0000"), NOW))
        await session.flush()
        stopped = await apply_billing_budget(session, tenant, NOW)
        alerts = (await session.scalars(select(Alert).where(Alert.tenant_id == tenant.id))).all()
        assert stopped is False
        assert campaign.status == "running"
        assert [row.code for row in alerts] == ["billing_budget"]
        assert alerts[0].severity == "warning"
        assert alerts[0].message == "2026-11 费用达到月度上限 10.0000 的 80%"


async def test_unquoted_lines_do_not_count_and_last_month_is_excluded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "budget-open", phone="13800006663")
        tenant.billing_budget = Decimal("10.0000")
        previous = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=trunk.id,
            template_version=1,
        )
        blank = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=trunk.id,
            template_version=1,
        )
        opening = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=trunk.id,
            template_version=1,
        )
        session.add_all([previous, blank, opening])
        await session.flush()
        session.add_all(
            [
                _item(tenant, previous, Decimal("100.0000"), datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)),
                _item(tenant, blank, None, NOW),
                _item(tenant, opening, Decimal("8.0000"), datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)),
            ]
        )
        await session.flush()
        stopped = await apply_billing_budget(session, tenant, NOW)
        summary = await billing_summary(session, tenant.id)
        codes = set((await session.scalars(select(Alert.code).where(Alert.tenant_id == tenant.id))).all())
        assert stopped is False
        assert campaign.status == "running"
        assert codes == {"billing_budget", "billing_budget_unquoted"}
        assert summary["month"] == "2026-11"
        assert summary["month_quoted"] == "8.0000"
        assert summary["month_unquoted"] == 1
        assert summary["billing_budget"] == "10.0000"
        assert summary["new_tasks_paused"] is False
        assert summary["amount"] is None


async def test_retry_and_start_wait_when_the_cap_is_reached(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client,
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "budget-start", phone="13800006664")
        tenant.billing_budget = Decimal("1.0000")
        done = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=uuid.uuid4(),
            status="no_answer",
            trunk_id=trunk.id,
            template_version=1,
            start_at=NOW,
        )
        session.add(done)
        await session.flush()
        session.add(_item(tenant, done, Decimal("1.0000"), NOW))
        session.add(
            Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="done")
        )
        campaign.retry_policy = {"max_retry": 1, "interval_min": [60]}
        await session.flush()
        created = await schedule_retries(session, campaign, NOW)
        assert created == 0
        assert campaign.status == "paused"
        campaign.status = "approved"
        with pytest.raises(ApiError) as caught:
            await start_campaign(
                session,
                campaign=campaign,
                settings=settings,
                redis=redis_client,
                user_id=uuid.uuid4(),
                trace_id="budget",
                now=NOW,
            )
        followups = (await session.scalars(select(Task).where(Task.attempt_no == 2))).all()
        assert followups == []
        assert caught.value.status == 409
        assert caught.value.message == "billing budget reached"
        assert campaign.status == "approved"


async def test_platform_admin_sets_and_clears_the_budget(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, _trunk, _campaign, _contact, _caller = await _world(session, "budget-api", phone="13800006665")
        session.add(
            User(
                tenant_id=None,
                username="budget-admin",
                password_hash=hash_password(PASSWORD),
                display_name="budget",
                role="platform_admin",
                must_change_password=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()
        tenant_id = str(tenant.id)
    login = await client.post("/api/auth/login", json={"username": "budget-admin", "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    rejected = await client.patch(
        f"/api/p/tenants/{tenant_id}",
        json={"version": 0, "billing_budget": "0"},
        headers=headers,
    )
    assert rejected.status_code == 422
    updated = await client.patch(
        f"/api/p/tenants/{tenant_id}",
        json={"version": 0, "billing_budget": "12.5000"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["billing_budget"] == "12.5000"
    kept = await client.patch(
        f"/api/p/tenants/{tenant_id}",
        json={"version": 1, "name": "预算租户"},
        headers=headers,
    )
    assert kept.status_code == 200
    assert kept.json()["data"]["billing_budget"] == "12.5000"
    cleared = await client.patch(
        f"/api/p/tenants/{tenant_id}",
        json={"version": 2, "billing_budget": None},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["billing_budget"] is None
    fetched = await client.get(f"/api/p/tenants/{tenant_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["data"]["billing_budget"] is None
