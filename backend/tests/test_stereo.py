"""Caller audio stays on the left channel and assistant audio stays on the right."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.models import Call, Campaign, ConsentRecord, Contact, ContactList, ScenarioTemplate, Task, Tenant, Trunk
from app.security.crypto import encrypt_phone, phone_hash
from app.services.dialer import dial_pending
from app.voice.stereo import (
    ASSISTANT_SAMPLE,
    BLOCK_SIZE,
    CALLER_SAMPLE,
    EXPLICIT_LABEL,
    label_meta,
    read_labels,
    split_channels,
    stereo_wav,
)

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)


def test_file_starts_with_the_explicit_label_and_aigc_chunk() -> None:
    call_uuid = uuid.uuid4()
    meta = label_meta(call_uuid, "")
    raw = meta["AIGC"]
    assert isinstance(raw, dict)
    wav = stereo_wav(
        [{"speaker": "caller", "channel": "caller", "text": "好的"}],
        aigc=raw,
        explicit_label=EXPLICIT_LABEL,
    )
    labels = read_labels(wav)
    assert labels["explicit_label"] == EXPLICIT_LABEL
    stored = labels["AIGC"]
    assert isinstance(stored, dict)
    assert stored["Label"] == "1" and stored["ContentProducer"] == "UNREGISTERED"
    assert stored["ProduceID"] == str(call_uuid)
    left, right = split_channels(wav)
    assert left[:BLOCK_SIZE] == [0] * BLOCK_SIZE
    assert right[:BLOCK_SIZE] == [ASSISTANT_SAMPLE] * BLOCK_SIZE
    assert left[BLOCK_SIZE : BLOCK_SIZE * 2] == [CALLER_SAMPLE] * BLOCK_SIZE


def test_channels_are_not_swapped() -> None:
    wav = stereo_wav(
        [
            {"speaker": "assistant", "channel": "assistant", "text": "开场"},
            {"speaker": "caller", "channel": "caller", "text": "好的"},
        ]
    )
    left, right = split_channels(wav)
    assert CALLER_SAMPLE in left and ASSISTANT_SAMPLE not in left
    assert ASSISTANT_SAMPLE in right and CALLER_SAMPLE not in right


async def test_dial_writes_a_stereo_recording(
    session_factory: async_sessionmaker[AsyncSession], redis_client, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant = Tenant(
            name="stereo",
            code="stereo-co",
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
        phone = "13800007721"
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
        )
        session.add(campaign)
        await session.flush()
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW, heard="好的"
        )
        call = await session.scalar(select(Call).where(Call.campaign_id == campaign.id))
        assert call is not None
        wav = (tmp_path / str(tenant.id) / f"{call.call_uuid}.wav").read_bytes()
        left, right = split_channels(wav)
        labels = read_labels(wav)
        assert labels["explicit_label"] == EXPLICIT_LABEL
        stored = labels["AIGC"]
        assert isinstance(stored, dict) and stored["ProduceID"] == str(call.call_uuid) and stored["Label"] == "1"
        assert left[:BLOCK_SIZE] == [0] * BLOCK_SIZE
        assert right[:BLOCK_SIZE] == [ASSISTANT_SAMPLE] * BLOCK_SIZE
        assert CALLER_SAMPLE in left and ASSISTANT_SAMPLE not in left
        assert ASSISTANT_SAMPLE in right and CALLER_SAMPLE not in right
