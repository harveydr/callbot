"""Auth, isolation, campaign idempotency, and the documented API surface."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass, set_tenant
from app.main import create_app
from app.models import Call, Campaign, ConsentRecord, Contact, ContactList, DndEntry, ScenarioTemplate, Task, Tenant, Trunk, User
from app.security.crypto import encrypt_phone, phone_hash
from app.security.passwords import hash_password
from app.services.dialer import dial_pending, schedule_retries
from app.services.tokens import TokenBucket

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)
PASSWORD = "password12"


@pytest.fixture
async def client(database: None):
    del database
    application = create_app()
    application.state.now = lambda: NOW
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def _user(
    session: AsyncSession,
    *,
    role: str,
    username: str,
    tenant: Tenant | None,
    password: str = PASSWORD,
) -> User:
    user = User(
        tenant_id=None if tenant is None else tenant.id,
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        role=role,
        must_change_password=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    await session.flush()
    return user


async def _tenant(session: AsyncSession, code: str) -> Tenant:
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
    session.add(tenant)
    await session.flush()
    return tenant


async def login(http: AsyncClient, username: str, password: str = PASSWORD) -> dict[str, object]:
    response = await http.post("/api/auth/login", json={"username": username, "password": password})
    return {"status": response.status_code, "body": response.json()}


async def test_login_success_and_lockout(client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        await _user(session, role="tenant_admin", username="lock-user", tenant=await _tenant(session, "lock-co"))
        await session.commit()
    ok = await login(client, "lock-user")
    assert ok["status"] == 200
    body = ok["body"]
    assert isinstance(body, dict)
    assert body["code"] == 0
    assert body["data"]["role"] == "tenant_admin"
    for _ in range(4):
        failed = await login(client, "lock-user", "wrong-password")
        assert failed["status"] == 401
    locked = await login(client, "lock-user", "wrong-password")
    assert locked["status"] == 423
    still = await login(client, "lock-user", PASSWORD)
    assert still["status"] == 423
    assert "unlock_at" in still["body"]["data"]


async def test_cross_tenant_is_forbidden_and_auditor_cannot_write(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        first = await _tenant(session, "alpha")
        second = await _tenant(session, "beta")
        await _user(session, role="tenant_admin", username="alpha-admin", tenant=first)
        await _user(session, role="auditor", username="beta-auditor", tenant=second)
        await set_tenant(session, first.id)
        template = ScenarioTemplate(
            tenant_id=first.id,
            name="notice",
            scenario_code="notification",
            status="approved",
            nodes={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
        contact_list = ContactList(tenant_id=first.id, name="list", scenario_code="notification")
        trunk = Trunk(
            tenant_id=first.id,
            name="trunk",
            mode="byoc",
            provider="byoc",
            sip_host="sip.example.net",
            allowed_scenarios=["notification"],
            status="active",
        )
        session.add_all([template, contact_list, trunk])
        await session.flush()
        campaign = Campaign(
            tenant_id=first.id,
            name="camp",
            scenario_code="notification",
            template_id=template.id,
            template_version=1,
            list_id=contact_list.id,
            start_at=NOW,
            end_at=NOW + timedelta(days=1),
            trunk_id=trunk.id,
        )
        session.add(campaign)
        await session.commit()
        campaign_id = campaign.id
    alpha = await login(client, "alpha-admin")
    beta = await login(client, "beta-auditor")
    alpha_token = alpha["body"]["data"]["access_token"]
    beta_token = beta["body"]["data"]["access_token"]
    seen = await client.get(f"/api/t/campaigns/{campaign_id}", headers={"Authorization": f"Bearer {alpha_token}"})
    assert seen.status_code == 200
    hidden = await client.get(f"/api/t/campaigns/{campaign_id}", headers={"Authorization": f"Bearer {beta_token}"})
    assert hidden.status_code == 403
    assert hidden.json()["code"] == "FORBIDDEN"
    rejected = await client.post(
        "/api/t/campaigns",
        headers={"Authorization": f"Bearer {beta_token}", "Idempotency-Key": str(uuid.uuid4())},
        json={
            "name": "nope",
            "scenario_code": "notification",
            "template_id": str(uuid.uuid4()),
            "list_id": str(uuid.uuid4()),
            "trunk_id": str(uuid.uuid4()),
            "start_at": "2026-11-22T02:00:00Z",
            "end_at": "2026-11-23T02:00:00Z",
        },
    )
    assert rejected.status_code == 403


async def test_version_conflict_start_idempotent_and_pause(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "dial-co")
        await _user(session, role="operator", username="dial-op", tenant=tenant)
        template = ScenarioTemplate(
            tenant_id=tenant.id,
            name="notice",
            scenario_code="notification",
            status="approved",
            nodes={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
        contact_list = ContactList(tenant_id=tenant.id, name="list", scenario_code="notification")
        trunk = Trunk(
            tenant_id=tenant.id,
            name="trunk",
            mode="byoc",
            provider="byoc",
            sip_host="sip.example.net",
            allowed_scenarios=["notification"],
            status="active",
            max_concurrency=10,
        )
        session.add_all([template, contact_list, trunk])
        await session.flush()
        pepper = b"test-pepper-not-for-production"
        key = __import__("base64").b64decode("AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA=")
        for index in range(11):
            phone = f"1380000{index:04d}"
            hashed = phone_hash(phone, pepper)
            session.add(
                ConsentRecord(
                    tenant_id=tenant.id,
                    phone_enc=encrypt_phone(phone, key),
                    phone_last4=phone[-4:],
                    phone_hash=hashed,
                    scenario_code="notification",
                    source="contract",
                    consented_at=NOW - timedelta(days=1),
                    revoked=False,
                )
            )
            session.add(
                Contact(
                    tenant_id=tenant.id,
                    list_id=contact_list.id,
                    phone_enc=encrypt_phone(phone, key),
                    phone_last4=phone[-4:],
                    phone_hash=hashed,
                    dial_class="dialable",
                )
            )
        campaign = Campaign(
            tenant_id=tenant.id,
            name="burst",
            scenario_code="notification",
            template_id=template.id,
            template_version=1,
            list_id=contact_list.id,
            status="approved",
            start_at=NOW,
            end_at=NOW + timedelta(days=1),
            max_concurrency=10,
            trunk_id=trunk.id,
            created_by=None,
        )
        session.add(campaign)
        await session.commit()
        campaign_id = str(campaign.id)
        tenant_id = tenant.id
    token = (await login(client, "dial-op"))["body"]["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    stale = await client.patch(f"/api/t/campaigns/{campaign_id}", headers=headers, json={"version": 9, "name": "other"})
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_CONFLICT"
    first = await client.post(f"/api/t/campaigns/{campaign_id}/start", headers=headers)
    assert first.status_code == 202
    second_headers = {**headers, "Idempotency-Key": str(uuid.uuid4())}
    second = await client.post(f"/api/t/campaigns/{campaign_id}/start", headers=second_headers)
    assert second.status_code == 202
    assert second.json()["data"]["idempotent"] is True
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        calls = int(await session.scalar(select(func.count()).select_from(Call).where(Call.campaign_id == uuid.UUID(campaign_id))) or 0)
        tasks = int(await session.scalar(select(func.count()).select_from(Task).where(Task.campaign_id == uuid.UUID(campaign_id))) or 0)
        assert tasks == 11
        assert calls == 10
        assert first.json()["data"]["started"] == 10
    paused = await client.post(
        f"/api/t/campaigns/{campaign_id}/pause",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert paused.status_code == 200
    from app.config import load_settings

    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        campaign = await session.scalar(select(Campaign).where(Campaign.id == uuid.UUID(campaign_id)))
        tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        trunk = await session.scalar(select(Trunk).where(Trunk.tenant_id == tenant_id))
        assert campaign is not None and tenant is not None and trunk is not None
        assert campaign.status == "paused"
        again = await dial_pending(
            session,
            campaign=campaign,
            tenant=tenant,
            trunk=trunk,
            settings=load_settings(),
            redis=redis_client,
            now=NOW,
        )
        assert again == 0


async def test_complaint_writes_dnd_and_import_is_idempotent(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "list-co")
        await _user(session, role="operator", username="list-op", tenant=tenant)
        contact_list = ContactList(tenant_id=tenant.id, name="people", scenario_code="notification")
        session.add(contact_list)
        await session.commit()
        list_id = str(contact_list.id)
        tenant_id = tenant.id
    token = (await login(client, "list-op"))["body"]["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    csv_body = "phone,name\n13800001111,Ann\nnot-a-phone,Bad\n13800002222,Bo\n"
    files = {"file": ("people.csv", csv_body, "text/csv")}
    first = await client.post(f"/api/t/lists/{list_id}/import", headers=headers, files=files)
    second = await client.post(f"/api/t/lists/{list_id}/import", headers=headers, files=files)
    assert first.status_code == 202
    assert first.json()["data"]["invalid_format"] == 1
    assert first.json()["data"]["no_consent"] == 2
    assert second.json()["data"]["task_id"] == first.json()["data"]["task_id"]
    complaint = await client.post(
        "/api/t/complaints",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"phone": "13800001111", "detail": "请不要再打"},
    )
    assert complaint.status_code == 200
    assert complaint.json()["data"]["dnd"] is True
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        rows = (await session.scalars(select(DndEntry).where(DndEntry.tenant_id == tenant_id))).all()
        assert len(rows) == 1
        assert rows[0].source == "complaint"
        contacts = int(await session.scalar(select(func.count()).select_from(Contact).where(Contact.list_id == uuid.UUID(list_id))) or 0)
        assert contacts == 2


async def test_retries_follow_policy_and_token_ttl(session_factory: async_sessionmaker[AsyncSession], redis_client) -> None:
    bucket = TokenBucket(redis_client, prefix="")
    assert await bucket.acquire("live:ttl", limit=1, ttl_sec=30) is True
    ttl = await redis_client.ttl("live:ttl")
    assert ttl > 0
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "retry-co")
        template = ScenarioTemplate(
            tenant_id=tenant.id, name="n", scenario_code="notification", status="approved", nodes={}, version=1, created_at=NOW, updated_at=NOW
        )
        contact_list = ContactList(tenant_id=tenant.id, name="l", scenario_code="notification")
        trunk = Trunk(
            tenant_id=tenant.id, name="t", mode="byoc", provider="byoc", sip_host="sip.example.net", allowed_scenarios=["notification"], status="active"
        )
        session.add_all([template, contact_list, trunk])
        await session.flush()
        pepper = b"test-pepper-not-for-production"
        key = __import__("base64").b64decode("AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA=")
        phone = "13800003333"
        hashed = phone_hash(phone, pepper)
        contact = Contact(
            tenant_id=tenant.id,
            list_id=contact_list.id,
            phone_enc=encrypt_phone(phone, key),
            phone_last4=phone[-4:],
            phone_hash=hashed,
            dial_class="dialable",
        )
        session.add(contact)
        await session.flush()
        campaign = Campaign(
            tenant_id=tenant.id,
            name="retry",
            scenario_code="notification",
            template_id=template.id,
            template_version=1,
            list_id=contact_list.id,
            status="running",
            start_at=NOW,
            end_at=NOW + timedelta(days=1),
            trunk_id=trunk.id,
            retry_policy={"max_retry": 1, "interval_min": [60]},
        )
        session.add(campaign)
        await session.flush()
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="done"))
        session.add(
            Call(
                tenant_id=tenant.id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                call_uuid=uuid.uuid4(),
                status="no_answer",
                trunk_id=trunk.id,
                template_version=1,
            )
        )
        await session.flush()
        campaign.retry_policy = {"max_retry": 0, "interval_min": [60]}
        assert await schedule_retries(session, campaign, NOW) == 0
        campaign.retry_policy = {"max_retry": 1, "interval_min": [60]}
        created = await schedule_retries(session, campaign, NOW)
        created_again = await schedule_retries(session, campaign, NOW)
        assert created == 1
        assert created_again == 0


async def test_body_cannot_set_tenant_and_routes_exist(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"username": "abc", "password": "password12", "tenant_id": str(uuid.uuid4())})
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"
    paths = {getattr(route, "path", "") for route in client._transport.app.routes}  # type: ignore[attr-defined]
    required = {
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/change-password",
        "/api/users/me",
        "/api/t/members",
        "/api/p/tenants",
        "/api/p/tenants/{tenant_id}",
        "/api/p/tenants/{tenant_id}/qualifications",
        "/api/p/qualifications/{qualification_id}/review",
        "/api/p/tenants/{tenant_id}/suspend",
        "/api/p/tenants/{tenant_id}/resume",
        "/api/t/trunks",
        "/api/t/trunks/{trunk_id}",
        "/api/t/trunks/{trunk_id}/test-call",
        "/api/t/caller-numbers",
        "/api/t/lists",
        "/api/t/lists/{list_id}/import",
        "/api/t/imports/{task_id}",
        "/api/t/contacts",
        "/api/t/consents",
        "/api/t/consents/{consent_id}/revoke",
        "/api/t/campaigns",
        "/api/t/campaigns/{campaign_id}",
        "/api/t/campaigns/{campaign_id}/submit-review",
        "/api/t/campaigns/{campaign_id}/start",
        "/api/t/campaigns/{campaign_id}/pause",
        "/api/t/campaigns/{campaign_id}/abort",
        "/api/t/campaigns/{campaign_id}/stats",
        "/api/t/calls",
        "/api/t/calls/{call_id}",
        "/api/t/calls/{call_id}/transcript",
        "/api/t/qc-results",
        "/api/t/qc-results/{qc_id}/review",
        "/api/t/compliance/events",
        "/api/t/dnd",
        "/api/t/complaints",
        "/api/t/complaints/{complaint_id}",
        "/api/t/audit/evidence-pack",
        "/api/t/billing/summary",
        "/api/t/billing/items",
        "/api/t/reports/overview",
        "/api/t/reports/export",
        "/api/t/ws/tickets",
        "/api/p/metrics",
        "/api/p/circuit-status",
        "/api/p/circuit/{tenant_id}/reset",
        "/api/p/alerts",
    }
    missing = required - paths
    assert not missing


async def test_opening_is_stored_on_a_completed_call(session_factory: async_sessionmaker[AsyncSession], redis_client) -> None:
    from app.config import load_settings
    from app.models.extra import CallTurn, Recording

    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "voice-co")
        template = ScenarioTemplate(
            tenant_id=tenant.id, name="n", scenario_code="notification", status="approved", nodes={}, version=1, created_at=NOW, updated_at=NOW
        )
        contact_list = ContactList(tenant_id=tenant.id, name="l", scenario_code="notification")
        trunk = Trunk(
            tenant_id=tenant.id, name="t", mode="byoc", provider="byoc", sip_host="sip.example.net", allowed_scenarios=["notification"], status="active"
        )
        session.add_all([template, contact_list, trunk])
        await session.flush()
        phone = "13800004444"
        hashed = phone_hash(phone, settings.phone_pepper)
        contact = Contact(
            tenant_id=tenant.id,
            list_id=contact_list.id,
            phone_enc=encrypt_phone(phone, settings.field_enc_key),
            phone_last4=phone[-4:],
            phone_hash=hashed,
            dial_class="dialable",
        )
        session.add(contact)
        session.add(
            ConsentRecord(
                tenant_id=tenant.id,
                phone_enc=encrypt_phone(phone, settings.field_enc_key),
                phone_last4=phone[-4:],
                phone_hash=hashed,
                scenario_code="notification",
                source="contract",
                consented_at=NOW - timedelta(days=1),
            )
        )
        await session.flush()
        campaign = Campaign(
            tenant_id=tenant.id,
            name="提醒",
            scenario_code="notification",
            template_id=template.id,
            template_version=3,
            list_id=contact_list.id,
            status="running",
            start_at=NOW,
            end_at=NOW + timedelta(days=1),
            trunk_id=trunk.id,
        )
        session.add(campaign)
        await session.flush()
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        started = await dial_pending(session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW)
        assert started == 1
        call = await session.scalar(select(Call).where(Call.campaign_id == campaign.id))
        assert call is not None and call.status == "completed" and call.template_version == 3
        spoken = await session.scalar(select(CallTurn.text_content).where(CallTurn.call_id == call.id, CallTurn.speaker == "assistant"))
        assert spoken is not None and "人工智能" in spoken and "录音" in spoken
        recording = await session.scalar(select(Recording).where(Recording.call_id == call.id))
        assert recording is not None
        label = recording.aigc_label_meta["AIGC"]
        assert isinstance(label, dict) and label["Label"] == "1"
