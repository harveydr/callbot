"""Vendor CDR posts once. A missing local row is filled. A price gap becomes a dispute."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import Call, CallEvent, Campaign, Contact, ContactList, ScenarioTemplate, Tenant, Trunk
from app.models.extra import BillingDispute, BillingItem
from app.security.crypto import encrypt_phone, phone_hash
from app.services.exports import export_expires_at, export_is_usable
from app.tasks.billing import VendorCdr, apply_vendor_cdr

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)
KEY = __import__("base64").b64decode("AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA=")
PEPPER = b"test-pepper-not-for-production"


async def _call(session: AsyncSession, *, amount: Decimal | None) -> Call:
    await set_bypass(session)
    tenant = Tenant(
        name="账单租户",
        code=f"bill-{uuid.uuid4().hex[:8]}",
        status="active",
        allowed_scenarios=["notification"],
        timezone="Asia/Shanghai",
        is_demo=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(tenant)
    await session.flush()
    template = ScenarioTemplate(
        tenant_id=tenant.id,
        name="通知",
        scenario_code="notification",
        status="approved",
        nodes={},
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    contact_list = ContactList(tenant_id=tenant.id, name="名单", scenario_code="notification")
    trunk = Trunk(
        tenant_id=tenant.id,
        name="线路",
        mode="byoc",
        provider="byoc",
        sip_host="sip.example.net",
        allowed_scenarios=["notification"],
        status="active",
    )
    session.add_all([template, contact_list, trunk])
    await session.flush()
    phone = "13900003333"
    contact = Contact(
        tenant_id=tenant.id,
        list_id=contact_list.id,
        phone_enc=encrypt_phone(phone, KEY),
        phone_last4=phone[-4:],
        phone_hash=phone_hash(phone, PEPPER),
        dial_class="dialable",
    )
    session.add(contact)
    campaign = Campaign(
        tenant_id=tenant.id,
        name="通知",
        scenario_code="notification",
        template_id=template.id,
        template_version=1,
        list_id=contact_list.id,
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        trunk_id=trunk.id,
    )
    session.add(campaign)
    await session.flush()
    call = Call(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        call_uuid=uuid.uuid4(),
        status="completed",
        trunk_id=trunk.id,
        template_version=1,
        duration_sec=0,
        start_at=NOW,
        end_at=NOW,
    )
    session.add(call)
    await session.flush()
    if amount is not None:
        session.add(
            BillingItem(
                tenant_id=tenant.id,
                call_id=call.id,
                call_uuid=call.call_uuid,
                amount=amount,
                currency="CNY",
                rate_note="quoted",
            )
        )
        await session.flush()
    return call


async def test_duplicate_cdr_posts_once(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        call = await _call(session, amount=Decimal("0.0800"))
        cdr = VendorCdr("evt-1", 30, Decimal("0.0800"))
        first = await apply_vendor_cdr(session, call=call, cdr=cdr, now=NOW)
        second = await apply_vendor_cdr(session, call=call, cdr=cdr, now=NOW)
        events = int(
            await session.scalar(
                select(func.count()).select_from(CallEvent).where(CallEvent.call_id == call.id, CallEvent.event_type == "cdr_applied")
            )
            or 0
        )
        items = int(
            await session.scalar(select(func.count()).select_from(BillingItem).where(BillingItem.call_uuid == call.call_uuid)) or 0
        )
        assert first == "applied"
        assert second == "duplicate"
        assert events == 1
        assert items == 1
        assert call.duration_sec == 30


async def test_missing_cdr_is_filled_and_price_gap_is_a_dispute(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        call = await _call(session, amount=None)
        filled = await apply_vendor_cdr(
            session,
            call=call,
            cdr=VendorCdr("evt-2", 18, Decimal("0.0500")),
            now=NOW,
        )
        item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == call.call_uuid))
        assert item is not None
        dispute = await session.scalar(select(BillingDispute).where(BillingDispute.billing_item_id == item.id))
        assert filled == "disputed"
        assert item.amount is None
        assert item.rate_note == "unquoted"
        assert dispute is not None
        assert dispute.reason == "local_amount_unquoted"
        assert call.duration_sec == 18


def test_export_link_expires_after_fifteen_minutes() -> None:
    issued = export_expires_at(NOW)
    assert export_is_usable(expires_at=issued, now=NOW + timedelta(minutes=14))
    assert export_is_usable(expires_at=issued, now=NOW + timedelta(minutes=15)) is False
