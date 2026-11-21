"""A 200k-row import can be repeated without duplicating contacts, and the ciphertext round-trips."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.models import ConsentRecord, Contact, ContactList, Tenant
from app.security.crypto import decrypt_phone, encrypt_phone, phone_hash
from app.services.control import import_contacts

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
