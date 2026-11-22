"""An expired business license stops new calls and warns before that day."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.compliance.qualification import apply_qualification_clock, sweep_qualifications
from app.tasks import celery_app
from app.config import load_settings
from app.db import set_bypass
from app.models import Call, Campaign, ConsentRecord, Contact, ContactList, Qualification, ScenarioTemplate, Task, Tenant, Trunk
from app.models.extra import Alert
from app.security.crypto import encrypt_phone, phone_hash
from app.services.dialer import dial_pending

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)


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


async def test_windows_alert_once_and_expiry_suspends(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        soon = await _tenant(session, "qual-soon")
        later = await _tenant(session, "qual-later")
        safe = await _tenant(session, "qual-safe")
        due = Qualification(
            tenant_id=soon.id,
            doc_type="business_license",
            file_key="soon",
            status="approved",
            expires_at=NOW + timedelta(days=6),
        )
        expired = Qualification(
            tenant_id=later.id,
            doc_type="business_license",
            file_key="later",
            status="approved",
            expires_at=NOW - timedelta(days=1),
        )
        distant = Qualification(
            tenant_id=safe.id,
            doc_type="business_license",
            file_key="safe",
            status="approved",
            expires_at=NOW + timedelta(days=40),
        )
        session.add_all([due, expired, distant])
        await session.flush()
        await apply_qualification_clock(session, soon, NOW)
        await apply_qualification_clock(session, soon, NOW)
        await apply_qualification_clock(session, later, NOW)
        await apply_qualification_clock(session, safe, NOW)
        due_alerts = int(
            await session.scalar(select(func.count()).select_from(Alert).where(Alert.tenant_id == soon.id)) or 0
        )
        expired_alert = await session.scalar(
            select(Alert).where(Alert.tenant_id == later.id, Alert.code == "qualification_expired")
        )
        safe_alert = await session.scalar(select(Alert.id).where(Alert.tenant_id == safe.id))
        assert soon.status == "active"
        assert due_alerts == 1
        assert later.status == "suspended" and expired.status == "expired"
        assert expired_alert is not None and expired_alert.severity == "critical"
        assert safe_alert is None


async def test_sweep_suspends_an_expired_license_without_a_dial(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        licensed = await _tenant(session, "qual-sweep")
        other_doc = await _tenant(session, "qual-other")
        closed = await _tenant(session, "qual-closed")
        closed.status = "closed"
        session.add_all(
            [
                Qualification(
                    tenant_id=licensed.id,
                    doc_type="business_license",
                    file_key="sweep",
                    status="approved",
                    expires_at=NOW - timedelta(hours=1),
                ),
                Qualification(
                    tenant_id=other_doc.id,
                    doc_type="script_filing",
                    file_key="other",
                    status="approved",
                    expires_at=NOW - timedelta(hours=1),
                ),
                Qualification(
                    tenant_id=closed.id,
                    doc_type="business_license",
                    file_key="closed",
                    status="approved",
                    expires_at=NOW - timedelta(hours=1),
                ),
            ]
        )
        await session.flush()
        first = await sweep_qualifications(session, NOW)
        second = await sweep_qualifications(session, NOW)
        alerts = int(
            await session.scalar(
                select(func.count()).select_from(Alert).where(Alert.tenant_id == licensed.id, Alert.code == "qualification_expired")
            )
            or 0
        )
        other = await session.scalar(select(Qualification).where(Qualification.tenant_id == other_doc.id))
        assert first == 1
        assert second == 0
        assert licensed.status == "suspended"
        assert other_doc.status == "active"
        assert other is not None and other.status == "expired"
        assert closed.status == "closed"
        assert alerts == 1


def test_qualification_sweep_runs_before_the_calling_window() -> None:
    entry = celery_app.conf.beat_schedule["sweep-qualifications"]
    schedule = entry["schedule"]
    assert entry["task"] == "sweep_qualifications"
    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert schedule.hour == {0}
    assert schedule.minute == {10}


async def test_expired_license_blocks_the_dial(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "qual-dial")
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
        session.add(template)
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
        phone = "13800008821"
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
                Qualification(
                    tenant_id=tenant.id,
                    doc_type="business_license",
                    file_key="dial",
                    status="approved",
                    expires_at=NOW - timedelta(hours=1),
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
        started = await dial_pending(
            session, campaign=campaign, tenant=tenant, trunk=trunk, settings=settings, redis=redis_client, now=NOW
        )
        call = await session.scalar(select(Call).where(Call.campaign_id == campaign.id))
        assert started == 0
        assert tenant.status == "suspended"
        assert call is not None and call.status == "blocked_compliance"
        assert call.block_reason == "qualification_expired"
