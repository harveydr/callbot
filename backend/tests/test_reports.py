"""Tenant overview counts real calls and leaves rates empty when nothing was dialed."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import Call, Campaign, Contact, ContactList, ScenarioTemplate, Tenant, Trunk
from app.models.extra import BillingItem
from app.services.control import overview

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)


async def _shell(session: AsyncSession, code: str, *, demo: bool) -> tuple[Tenant, Campaign, Contact]:
    tenant = Tenant(
        name=code,
        code=code,
        status="active",
        allowed_scenarios=["notification"],
        timezone="Asia/Shanghai",
        is_demo=demo,
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
    contact = Contact(
        tenant_id=tenant.id,
        list_id=contact_list.id,
        phone_enc=b"enc",
        phone_last4="0000",
        phone_hash=f"hash-{code}",
        dial_class="dialable",
    )
    session.add(contact)
    await session.flush()
    campaign = Campaign(
        tenant_id=tenant.id,
        name="提醒",
        scenario_code="notification",
        template_id=template.id,
        template_version=1,
        list_id=contact_list.id,
        status="done",
        start_at=NOW,
        end_at=NOW,
        trunk_id=trunk.id,
    )
    session.add(campaign)
    await session.flush()
    return tenant, campaign, contact


def _call(
    tenant: Tenant,
    campaign: Campaign,
    contact: Contact,
    status: str,
    *,
    duration_sec: int = 0,
    intent_grade: str | None = None,
    block_reason: str | None = None,
) -> Call:
    return Call(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        call_uuid=uuid.uuid4(),
        status=status,
        trunk_id=campaign.trunk_id,
        template_version=1,
        duration_sec=duration_sec,
        intent_grade=intent_grade,
        block_reason=block_reason,
    )


async def test_overview_rates_and_demo_exclusion(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, campaign, contact = await _shell(session, "report-co", demo=False)
        demo, demo_campaign, demo_contact = await _shell(session, "report-demo", demo=True)
        completed = _call(tenant, campaign, contact, "completed", duration_sec=30, intent_grade="B")
        session.add_all(
            [
                completed,
                _call(tenant, campaign, contact, "completed", duration_sec=10, intent_grade="A"),
                _call(tenant, campaign, contact, "transfer_failed", duration_sec=20, intent_grade="C"),
                _call(tenant, campaign, contact, "rejected"),
                _call(tenant, campaign, contact, "no_answer"),
                _call(tenant, campaign, contact, "blocked_compliance", block_reason="no_valid_consent"),
                _call(tenant, campaign, contact, "blocked_compliance", block_reason="no_valid_consent"),
                _call(tenant, campaign, contact, "blocked_compliance", block_reason="tenant_dnd"),
                _call(demo, demo_campaign, demo_contact, "completed", duration_sec=99, intent_grade="A"),
            ]
        )
        await session.flush()
        session.add(
            BillingItem(
                tenant_id=tenant.id,
                call_id=completed.id,
                call_uuid=completed.call_uuid,
                amount=None,
                currency="CNY",
                rate_note="unquoted",
            )
        )
        await session.flush()
        report = await overview(session, tenant.id)
        hidden = await overview(session, demo.id)
        assert report["answer_rate"] == "0.6000"
        assert report["avg_duration_sec"] == 20
        assert report["reject_rate"] == "0.2000"
        assert report["transfer_rate"] == "0.3333"
        assert report["ai_complete_rate"] == "0.6667"
        assert report["intent_a"] == 1 and report["intent_b"] == 1 and report["intent_c"] == 1
        assert report["top_compliance_rule"] == "no_valid_consent"
        assert report["no_consent_blocks"] == 2 and report["dnd_blocks"] == 1
        assert report["unquoted_items"] == 1
        assert report["calls"] == 8 and report["demo_excluded"] is False
        assert hidden["demo_excluded"] is True and hidden["calls"] == 0 and hidden["answer_rate"] is None
