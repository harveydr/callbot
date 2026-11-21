"""Post-call review writes a factual summary and stops a script that hit a redline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.compliance.opening import render_opening
from app.compliance.qc_post import block_followups, review_call
from app.config import load_settings
from app.db import set_bypass
from app.models import Call, Campaign, ConsentRecord, Contact, ContactList, DndEntry, ScenarioTemplate, Task, Tenant, Trunk
from app.models.extra import Alert, QcResult, Transcript
from app.security.crypto import encrypt_phone, phone_hash
from app.services.dialer import dial_pending

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)


def test_review_uses_the_transcript_and_does_not_invent_a_summary() -> None:
    opening = render_opening(company="回声", purpose="通知")
    agreed = review_call(assistant=opening, caller="好的", grade="B", redline_hits=[], transcript_present=True)
    missing = review_call(assistant="", caller="", grade="NA", redline_hits=[], transcript_present=False)
    redline = review_call(assistant=f"{opening}保证收益", caller="", grade="C", redline_hits=[], transcript_present=True)
    long_text = review_call(
        assistant=opening + "。" * 5000, caller="", grade="NA", redline_hits=[], transcript_present=True
    )
    assert agreed.result == "pass" and agreed.summary is not None
    assert len(agreed.summary) <= 60 and "对方同意" in agreed.summary
    assert "购买" not in agreed.summary
    assert missing.summary is None and missing.result == "fail"
    assert redline.result == "fail" and redline.block_script is True
    assert long_text.summary is None and long_text.result == "pass"


async def _ready(
    session: AsyncSession, code: str, phone: str
) -> tuple[Tenant, Trunk, Campaign, Contact]:
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
        end_at=NOW + timedelta(days=2),
        trunk_id=trunk.id,
    )
    session.add(campaign)
    await session.flush()
    return tenant, trunk, campaign, contact


async def test_completed_call_stores_summary_and_reschedule(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact = await _ready(session, "qc-co", "13800001221")
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending"))
        await session.flush()
        started = await dial_pending(
            session,
            campaign=campaign,
            tenant=tenant,
            trunk=trunk,
            settings=settings,
            redis=redis_client,
            now=NOW,
            heard="明天再打",
        )
        call = await session.scalar(select(Call).where(Call.campaign_id == campaign.id))
        transcript = await session.scalar(select(Transcript).where(Transcript.call_id == call.id)) if call else None
        qc = await session.scalar(select(QcResult).where(QcResult.call_id == call.id)) if call else None
        follow = await session.scalar(select(Task).where(Task.attempt_no == 2))
        dnd = await session.scalar(select(DndEntry).where(DndEntry.tenant_id == tenant.id))
        assert started == 1
        assert call is not None and call.status == "completed"
        assert transcript is not None and transcript.summary is not None and "对方询问" in transcript.summary
        assert qc is not None and qc.result == "pass"
        qc_body = qc.detail["qc"]
        assert isinstance(qc_body, dict) and qc_body["purpose_stated"] is True and qc_body["opt_out_offered"] is True
        assert follow is not None and follow.next_run_at == NOW + timedelta(days=1) and follow.status == "pending"
        assert dnd is None


async def test_redline_failure_pauses_later_tasks(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, _trunk, campaign, contact = await _ready(session, "qc-stop", "13800001222")
        session.add(Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=2, status="pending"))
        await session.flush()
        await block_followups(session, campaign)
        task = await session.scalar(select(Task).where(Task.campaign_id == campaign.id))
        alert = await session.scalar(select(Alert).where(Alert.tenant_id == tenant.id, Alert.code == "qc_redline"))
        assert campaign.status == "paused"
        assert task is not None and task.status == "cancelled"
        assert alert is not None
