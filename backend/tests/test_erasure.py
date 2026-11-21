"""A closed tenant keeps evidence until retention ends, then the erasure is reported."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.main import create_app
from app.models import AuditLog, Call, Campaign, ConsentRecord, Contact, User
from app.models.extra import Recording
from app.security.passwords import hash_password
from app.services.erasure import purge_closed_tenants
from app.tasks import celery_app
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


async def test_closure_waits_out_retention_then_writes_one_report(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    assert celery_app.conf.beat_schedule["purge-closed-tenants"]["task"] == "purge_closed_tenants"
    async with session_factory() as session:
        await set_bypass(session)
        tenant, _trunk, campaign, contact, _caller = await _world(session, "erasure-co", phone="13800008111")
        session.add(
            User(
                tenant_id=None,
                username="erasure-admin",
                password_hash=hash_password(PASSWORD),
                display_name="erasure",
                role="platform_admin",
                must_change_password=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        expired_call = uuid.uuid4()
        held_call = uuid.uuid4()
        held = Contact(
            tenant_id=tenant.id,
            list_id=contact.list_id,
            phone_enc=b"held-phone",
            phone_last4="2222",
            phone_hash="held-hash",
            dial_class="dialable",
            created_at=NOW,
        )
        session.add(held)
        await session.flush()
        session.add_all(
            [
                Call(
                    tenant_id=tenant.id,
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    call_uuid=expired_call,
                    status="completed",
                    template_version=1,
                    created_at=NOW - timedelta(days=40),
                ),
                Call(
                    tenant_id=tenant.id,
                    campaign_id=campaign.id,
                    contact_id=held.id,
                    call_uuid=held_call,
                    status="completed",
                    template_version=1,
                    created_at=NOW - timedelta(days=40),
                ),
            ]
        )
        await session.flush()
        expired_row = await session.scalar(select(Call).where(Call.call_uuid == expired_call))
        held_row = await session.scalar(select(Call).where(Call.call_uuid == held_call))
        assert expired_row is not None and held_row is not None
        session.add_all(
            [
                Recording(
                    tenant_id=tenant.id,
                    call_id=expired_row.id,
                    object_key=f"recordings/{tenant.id}/{expired_call}.wav",
                    aigc_label_meta={"AIGC": {"Label": "1"}},
                    explicit_label="以下为人工智能生成合成语音",
                    retain_until=NOW - timedelta(days=1),
                ),
                Recording(
                    tenant_id=tenant.id,
                    call_id=held_row.id,
                    object_key=f"recordings/{tenant.id}/{held_call}.wav",
                    aigc_label_meta={"AIGC": {"Label": "1"}},
                    explicit_label="以下为人工智能生成合成语音",
                    retain_until=NOW + timedelta(days=10),
                ),
            ]
        )
        await session.commit()
        tenant_id = tenant.id
        campaign_id = campaign.id
        contact_id = contact.id
        held_id = held.id
    expired_path = tmp_path / str(tenant_id) / f"{expired_call}.wav"
    held_path = tmp_path / str(tenant_id) / f"{held_call}.wav"
    expired_path.parent.mkdir(parents=True)
    expired_path.write_bytes(b"expired")
    held_path.write_bytes(b"held")

    logged = await client.post("/api/auth/login", json={"username": "erasure-admin", "password": PASSWORD})
    assert logged.status_code == 200
    headers = {"Authorization": f"Bearer {logged.json()['data']['access_token']}"}
    closed = await client.patch(
        f"/api/p/tenants/{tenant_id}",
        headers=headers,
        json={"version": 0, "status": "closed"},
    )
    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "closed"
    resumed = await client.post(f"/api/p/tenants/{tenant_id}/resume", headers=headers)
    assert resumed.status_code == 409

    async with session_factory() as session:
        await set_bypass(session)
        early = await purge_closed_tenants(session, NOW)
        stored = await session.get(Contact, contact_id)
        running = await session.get(Campaign, campaign_id)
        assert early == []
        assert stored is not None and stored.phone_last4 != "----"
        assert running is not None and running.status == "aborted"
        marker = await session.scalar(
            select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == "tenant.close")
        )
        assert marker is not None
        marker.created_at = NOW - timedelta(days=31)
        await session.commit()

    async with session_factory() as session:
        await set_bypass(session)
        erased = await purge_closed_tenants(session, NOW)
        again = await purge_closed_tenants(session, NOW)
        wiped = await session.get(Contact, contact_id)
        kept = await session.get(Contact, held_id)
        consent = await session.scalar(select(ConsentRecord).where(ConsentRecord.tenant_id == tenant_id, ConsentRecord.phone_last4 == "----"))
        reports = int(
            await session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == "tenant.deletion_report")
            )
            or 0
        )
        assert erased == [str(tenant_id)]
        assert again == []
        assert reports == 1
        assert wiped is not None and wiped.phone_last4 == "----" and wiped.phone_enc == b"\x00"
        assert kept is not None and kept.phone_last4 == "2222"
        assert consent is not None
        assert not expired_path.exists()
        assert held_path.read_bytes() == b"held"
