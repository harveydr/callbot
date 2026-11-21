"""Vendor CDR posts once. A missing local row is filled. A price gap becomes a dispute."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import Call, CallEvent, Campaign, Contact, ContactList, ScenarioTemplate, Tenant, Trunk
from app.models.extra import Alert, BillingDispute, BillingItem
from app.security.crypto import encrypt_phone, phone_hash
from app.services.exports import export_expires_at, export_is_usable
from app.tasks import celery_app
from app.tasks.billing import (
    VendorCdr,
    apply_vendor_cdr,
    duration_gap,
    probe_missing_cdr,
    reconcile_previous_day,
    reconcile_vendor_bill,
)

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


def test_duration_gap_uses_a_five_percent_threshold() -> None:
    assert duration_gap(100, 105) is False
    assert duration_gap(100, 106) is True
    assert duration_gap(30, 0) is True
    assert duration_gap(0, 30) is False


async def test_duration_mismatch_opens_a_dispute_without_changing_the_price(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        call = await _call(session, amount=Decimal("1.0000"))
        call.duration_sec = 100
        result = await apply_vendor_cdr(
            session,
            call=call,
            cdr=VendorCdr("evt-gap", 106, Decimal("1.0000")),
            now=NOW,
        )
        item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == call.call_uuid))
        dispute = await session.scalar(select(BillingDispute).where(BillingDispute.billing_item_id == item.id)) if item else None
        alert = await session.scalar(select(Alert).where(Alert.tenant_id == call.tenant_id, Alert.code == "billing_dispute"))
        assert result == "disputed"
        assert call.duration_sec == 100
        assert item is not None and item.amount == Decimal("1.0000")
        assert dispute is not None and dispute.reason == "duration_mismatch"
        assert alert is not None and "未自动扣费" in alert.message


async def test_duration_inside_five_percent_is_not_a_dispute(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        call = await _call(session, amount=Decimal("1.0000"))
        call.duration_sec = 100
        result = await apply_vendor_cdr(
            session,
            call=call,
            cdr=VendorCdr("evt-close", 104, Decimal("1.0000")),
            now=NOW,
        )
        item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == call.call_uuid))
        dispute = None
        if item is not None:
            dispute = await session.scalar(select(BillingDispute).where(BillingDispute.billing_item_id == item.id))
        assert result == "applied"
        assert call.duration_sec == 100
        assert dispute is None


async def test_local_call_missing_from_the_vendor_bill_is_disputed_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        listed = await _call(session, amount=Decimal("1.0000"))
        listed.duration_sec = 40
        missing = Call(
            tenant_id=listed.tenant_id,
            campaign_id=listed.campaign_id,
            contact_id=listed.contact_id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=listed.trunk_id,
            template_version=1,
            duration_sec=40,
            start_at=NOW,
            end_at=NOW,
        )
        session.add(missing)
        await session.flush()
        session.add(
            BillingItem(
                tenant_id=listed.tenant_id,
                call_id=missing.id,
                call_uuid=missing.call_uuid,
                amount=Decimal("1.5000"),
                currency="CNY",
                rate_note="quoted",
            )
        )
        await session.flush()
        first = await reconcile_vendor_bill(
            session,
            tenant_id=listed.tenant_id,
            present_call_uuids={listed.call_uuid},
            period_start=NOW,
            period_end=NOW + timedelta(days=1),
            now=NOW,
        )
        second = await reconcile_vendor_bill(
            session,
            tenant_id=listed.tenant_id,
            present_call_uuids={listed.call_uuid},
            period_start=NOW,
            period_end=NOW + timedelta(days=1),
            now=NOW,
        )
        item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == missing.call_uuid))
        dispute = await session.scalar(select(BillingDispute).where(BillingDispute.billing_item_id == item.id)) if item else None
        assert first == 1
        assert second == 0
        assert item is not None and item.amount == Decimal("1.5000")
        assert dispute is not None and dispute.reason == "vendor_bill_missing"


async def test_previous_local_day_reconcile_skips_calls_with_a_vendor_cdr(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_at = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        listed = await _call(session, amount=Decimal("1.0000"))
        missing = Call(
            tenant_id=listed.tenant_id,
            campaign_id=listed.campaign_id,
            contact_id=listed.contact_id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=listed.trunk_id,
            template_version=1,
            duration_sec=40,
            start_at=NOW,
            end_at=NOW,
        )
        outside = Call(
            tenant_id=listed.tenant_id,
            campaign_id=listed.campaign_id,
            contact_id=listed.contact_id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=listed.trunk_id,
            template_version=1,
            duration_sec=40,
            start_at=datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc),
        )
        session.add_all([missing, outside])
        await session.flush()
        session.add(
            CallEvent(
                tenant_id=listed.tenant_id,
                call_id=listed.id,
                event_type="cdr_applied",
                detail={"vendor_event_id": "evt-day"},
            )
        )
        session.add(
            BillingItem(
                tenant_id=listed.tenant_id,
                call_id=missing.id,
                call_uuid=missing.call_uuid,
                amount=Decimal("2.0000"),
                currency="CNY",
                rate_note="quoted",
            )
        )
        closed = await _call(session, amount=Decimal("3.0000"))
        closed_tenant = await session.get(Tenant, closed.tenant_id)
        assert closed_tenant is not None
        closed_tenant.status = "closed"
        await session.flush()
        first = await reconcile_previous_day(session, job_at)
        second = await reconcile_previous_day(session, job_at)
        missing_item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == missing.call_uuid))
        listed_item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == listed.call_uuid))
        disputes = (
            await session.scalars(select(BillingDispute).where(BillingDispute.tenant_id == listed.tenant_id))
        ).all()
        closed_dispute = await session.scalar(select(BillingDispute.id).where(BillingDispute.tenant_id == closed.tenant_id))
        assert first == 1
        assert second == 0
        assert missing_item is not None and missing_item.amount == Decimal("2.0000")
        assert listed_item is not None and listed_item.amount == Decimal("1.0000")
        assert len(disputes) == 1
        assert disputes[0].billing_item_id == missing_item.id
        assert disputes[0].reason == "vendor_bill_missing"
        assert closed_dispute is None


def test_vendor_bill_reconcile_is_scheduled_at_two() -> None:
    entry = celery_app.conf.beat_schedule["reconcile-vendor-bills"]
    assert entry["task"] == "billing_reconcile"
    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert entry["schedule"].hour == {2}
    assert entry["schedule"].minute == {0}


async def _no_cdr(_call: Call) -> VendorCdr | None:
    return None


async def test_missing_cdr_is_probed_three_times_without_changing_the_price(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        call = await _call(session, amount=Decimal("1.2500"))
        call.duration_sec = 30
        call.end_at = NOW - timedelta(seconds=10)
        await session.flush()
        too_soon = await probe_missing_cdr(session, NOW, _no_cdr)
        call.end_at = NOW - timedelta(seconds=200)
        await session.flush()
        first = await probe_missing_cdr(session, NOW, _no_cdr)
        second = await probe_missing_cdr(session, NOW, _no_cdr)
        third = await probe_missing_cdr(session, NOW, _no_cdr)
        fourth = await probe_missing_cdr(session, NOW, _no_cdr)
        item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == call.call_uuid))
        dispute = await session.scalar(select(BillingDispute).where(BillingDispute.tenant_id == call.tenant_id))
        probes = (
            await session.scalars(select(CallEvent).where(CallEvent.call_id == call.id, CallEvent.event_type == "cdr_probe"))
        ).all()
        assert too_soon == 0
        assert (first, second, third, fourth) == (1, 1, 1, 0)
        assert sorted(row.detail.get("attempt") for row in probes) == [1, 2, 3]
        assert item is not None and item.amount == Decimal("1.2500") and call.duration_sec == 30
        assert dispute is not None and dispute.reason == "cdr_unavailable"


async def test_cdr_probe_applies_a_returned_vendor_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        found = await _call(session, amount=Decimal("1.0000"))
        found.duration_sec = 30
        found.end_at = NOW - timedelta(seconds=45)
        known = await _call(session, amount=Decimal("2.0000"))
        known.duration_sec = 30
        known.end_at = NOW - timedelta(seconds=45)
        stale = await _call(session, amount=Decimal("3.0000"))
        stale.end_at = NOW - timedelta(hours=25)
        await session.flush()
        session.add(
            CallEvent(
                tenant_id=known.tenant_id,
                call_id=known.id,
                event_type="cdr_applied",
                detail={"vendor_event_id": "already"},
            )
        )
        await session.flush()

        async def lookup(call: Call) -> VendorCdr | None:
            if call.id == found.id:
                return VendorCdr("evt-probe", 30, None)
            return None

        probed = await probe_missing_cdr(session, NOW, lookup)
        again = await probe_missing_cdr(session, NOW, lookup)
        applied = await session.scalar(
            select(CallEvent.id).where(CallEvent.call_id == found.id, CallEvent.event_type == "cdr_applied")
        )
        known_probes = await session.scalar(
            select(func.count()).select_from(CallEvent).where(CallEvent.call_id == known.id, CallEvent.event_type == "cdr_probe")
        )
        stale_probes = await session.scalar(
            select(func.count()).select_from(CallEvent).where(CallEvent.call_id == stale.id, CallEvent.event_type == "cdr_probe")
        )
        dispute = await session.scalar(select(BillingDispute.id).where(BillingDispute.tenant_id == found.tenant_id))
        item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == found.call_uuid))
        assert probed == 1
        assert again == 0
        assert applied is not None
        assert known_probes == 0
        assert stale_probes == 0
        assert dispute is None
        assert item is not None and item.amount == Decimal("1.0000")


def test_missing_cdr_probe_runs_every_minute() -> None:
    entry = celery_app.conf.beat_schedule["probe-missing-cdr"]
    assert entry["task"] == "probe_missing_cdr"
    assert entry["schedule"] == timedelta(minutes=1)
    assert celery_app.conf.task_routes["probe_missing_cdr"]["queue"] == "billing"
    assert celery_app.conf.task_annotations["probe_missing_cdr"]["time_limit"] == 120


def test_export_link_expires_after_fifteen_minutes() -> None:
    issued = export_expires_at(NOW)
    assert export_is_usable(expires_at=issued, now=NOW + timedelta(minutes=14))
    assert export_is_usable(expires_at=issued, now=NOW + timedelta(minutes=15)) is False
