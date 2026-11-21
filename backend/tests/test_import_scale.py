"""A 200k-row import can be repeated without duplicating contacts, and the ciphertext round-trips."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.errors import ApiError
from app.main import create_app
from app.models import ConsentRecord, Contact, ContactList, Tenant, User
from app.security.crypto import decrypt_phone, encrypt_phone, phone_hash
from app.security.passwords import hash_password
from app.services.control import accept_import, import_contacts, run_import_job

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)
ROWS = 200_000
SAMPLE = "13000000000"


def _csv() -> bytes:
    lines = ["phone,name", "not-a-phone,bad", f"{SAMPLE},Ann"]
    lines.extend(f"{13000000001 + index},n{index}" for index in range(ROWS - 1))
    return ("\n".join(lines) + "\n").encode("utf-8")


async def test_two_hundred_thousand_rows_import_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = load_settings()
    content = _csv()
    hashed = phone_hash(SAMPLE, settings.phone_pepper)
    async with session_factory() as session:
        await set_bypass(session)
        tenant = Tenant(
            name="大批量",
            code=f"bulk-{uuid.uuid4().hex[:8]}",
            status="active",
            allowed_scenarios=["notification"],
            timezone="Asia/Shanghai",
            is_demo=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(tenant)
        await session.flush()
        contact_list = ContactList(tenant_id=tenant.id, name="二十万", scenario_code="notification")
        session.add(contact_list)
        session.add(
            ConsentRecord(
                tenant_id=tenant.id,
                phone_enc=encrypt_phone(SAMPLE, settings.field_enc_key),
                phone_last4=SAMPLE[-4:],
                phone_hash=hashed,
                scenario_code="notification",
                source="contract",
                consented_at=NOW,
            )
        )
        await session.flush()
        first = await import_contacts(
            session, tenant_id=tenant.id, contact_list=contact_list, content=content, settings=settings
        )
        await session.commit()
        await set_bypass(session)
        second = await import_contacts(
            session, tenant_id=tenant.id, contact_list=contact_list, content=content, settings=settings
        )
        stored = await session.scalar(select(Contact).where(Contact.list_id == contact_list.id, Contact.phone_hash == hashed))
        total = int(
            await session.scalar(select(func.count()).select_from(Contact).where(Contact.list_id == contact_list.id)) or 0
        )
        assert first.id == second.id
        assert first.total == ROWS + 1
        assert first.invalid_format == 1
        assert first.dialable == 1
        assert first.no_consent == ROWS - 1
        assert total == ROWS
        assert stored is not None
        assert decrypt_phone(stored.phone_enc, settings.field_enc_key) == SAMPLE
        assert stored.phone_last4 == SAMPLE[-4:]


async def test_a_running_import_rejects_a_different_file(session_factory: async_sessionmaker[AsyncSession]) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant = Tenant(
            name="导入",
            code=f"imp-{uuid.uuid4().hex[:8]}",
            status="active",
            allowed_scenarios=["notification"],
            timezone="Asia/Shanghai",
            is_demo=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(tenant)
        await session.flush()
        contact_list = ContactList(tenant_id=tenant.id, name="名单", scenario_code="notification")
        session.add(contact_list)
        await session.flush()
        first_body = b"phone,name\nnot-a-phone,bad\n13800001111,Ann\n"
        job = await accept_import(session, tenant_id=tenant.id, contact_list=contact_list, content=first_body)
        with pytest.raises(ApiError) as caught:
            await accept_import(
                session,
                tenant_id=tenant.id,
                contact_list=contact_list,
                content=b"phone,name\n13800002222,Bo\n",
            )
        assert caught.value.status == 409
        assert caught.value.code == "CONFLICT"
        done = await run_import_job(session, job.id, settings)
        stored = int(
            await session.scalar(select(func.count()).select_from(Contact).where(Contact.list_id == contact_list.id)) or 0
        )
        again = await accept_import(session, tenant_id=tenant.id, contact_list=contact_list, content=first_body)
        assert done.status == "done"
        assert done.payload is None
        assert done.invalid_format == 1
        assert done.no_consent == 1
        assert stored == 1
        assert again.id == done.id


async def test_enqueued_import_returns_before_contacts_exist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = replace(load_settings(), celery_enqueue=True)
    async with session_factory() as session:
        await set_bypass(session)
        tenant = Tenant(
            name="排队",
            code=f"queue-{uuid.uuid4().hex[:8]}",
            status="active",
            allowed_scenarios=["notification"],
            timezone="Asia/Shanghai",
            is_demo=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(tenant)
        await session.flush()
        session.add(
            User(
                tenant_id=tenant.id,
                username=f"imp-{tenant.code}",
                password_hash=hash_password("password12"),
                display_name="imp",
                role="operator",
                must_change_password=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        contact_list = ContactList(tenant_id=tenant.id, name="名单", scenario_code="notification")
        session.add(contact_list)
        await session.commit()
        list_id = contact_list.id
        username = f"imp-{tenant.code}"
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            logged = await http.post("/api/auth/login", json={"username": username, "password": "password12"})
            token = logged.json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            body = b"phone,name\nnot-a-phone,bad\n13800003333,Ann\n"
            queued = []

            def capture(job_id: str) -> None:
                queued.append(job_id)

            import app.tasks.imports as imports

            imports.import_contacts_task.delay = capture  # type: ignore[method-assign]
            response = await http.post(
                f"/api/t/lists/{list_id}/import",
                headers=headers,
                files={"file": ("people.csv", body, "text/csv")},
            )
            assert response.status_code == 202
            data = response.json()["data"]
            assert data["status"] == "running"
            assert data["total"] == 0
            assert queued == [data["task_id"]]
    async with session_factory() as session:
        await set_bypass(session)
        waiting = int(
            await session.scalar(select(func.count()).select_from(Contact).where(Contact.list_id == list_id)) or 0
        )
        assert waiting == 0
        done = await run_import_job(session, uuid.UUID(queued[0]), load_settings())
        await session.commit()
        assert done.status == "done"
        assert done.invalid_format == 1
        assert done.no_consent == 1
        assert done.payload is None
