"""CDR application. A repeated vendor event posts once. A price gap opens a dispute and does not invent a rate."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, CallEvent
from app.models.extra import Alert, BillingDispute, BillingItem


@dataclass(frozen=True)
class VendorCdr:
    vendor_event_id: str
    duration_sec: int
    amount: Decimal | None


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now.astimezone(timezone.utc)


def duration_gap(local_sec: int, vendor_sec: int) -> bool:
    """True when both sides exist and the relative difference is above 5 percent."""
    if local_sec <= 0:
        return False
    gap = abs(Decimal(local_sec) - Decimal(vendor_sec)) / Decimal(local_sec)
    return gap > Decimal("0.05")


async def _already_applied(session: AsyncSession, call_id: uuid.UUID, vendor_event_id: str) -> bool:
    rows = (
        await session.scalars(
            select(CallEvent).where(CallEvent.call_id == call_id, CallEvent.event_type == "cdr_applied")
        )
    ).all()
    return any(row.detail.get("vendor_event_id") == vendor_event_id for row in rows)


async def apply_vendor_cdr(
    session: AsyncSession,
    *,
    call: Call,
    cdr: VendorCdr,
    now: datetime,
) -> str:
    moment = _aware(now)
    if await _already_applied(session, call.id, cdr.vendor_event_id):
        return "duplicate"
    item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == call.call_uuid))
    filled = False
    if item is None:
        item = BillingItem(
            tenant_id=call.tenant_id,
            call_id=call.id,
            call_uuid=call.call_uuid,
            amount=None,
            currency="CNY",
            rate_note="unquoted",
            created_at=moment,
        )
        session.add(item)
        await session.flush()
        filled = True
    if call.duration_sec == 0 and cdr.duration_sec > 0:
        call.duration_sec = cdr.duration_sec
    reason = ""
    if duration_gap(call.duration_sec, cdr.duration_sec):
        reason = "duration_mismatch"
    elif item.amount is None and cdr.amount is not None:
        reason = "local_amount_unquoted"
    elif item.amount is not None and cdr.amount is not None and item.amount != cdr.amount:
        reason = "amount_mismatch"
    if reason:
        await _open_dispute(session, call=call, item=item, reason=reason, now=moment)
    session.add(
        CallEvent(
            tenant_id=call.tenant_id,
            call_id=call.id,
            event_type="cdr_applied",
            detail={
                "vendor_event_id": cdr.vendor_event_id,
                "duration_sec": cdr.duration_sec,
                "filled": filled,
                "dispute": reason,
            },
            created_at=moment,
        )
    )
    await session.flush()
    if reason:
        return "disputed"
    if filled:
        return "filled"
    return "applied"


async def _open_dispute(
    session: AsyncSession,
    *,
    call: Call,
    item: BillingItem | None,
    reason: str,
    now: datetime,
) -> bool:
    if item is not None:
        existing = await session.scalar(
            select(BillingDispute.id).where(
                BillingDispute.billing_item_id == item.id,
                BillingDispute.status == "open",
                BillingDispute.reason == reason,
            )
        )
        if existing is not None:
            return False
    else:
        prior = (
            await session.scalars(
                select(CallEvent).where(CallEvent.call_id == call.id, CallEvent.event_type == "billing_dispute")
            )
        ).all()
        if any(row.detail.get("reason") == reason for row in prior):
            return False
    session.add(
        BillingDispute(
            tenant_id=call.tenant_id,
            billing_item_id=None if item is None else item.id,
            reason=reason,
            status="open",
            created_at=now,
        )
    )
    session.add(
        Alert(
            tenant_id=call.tenant_id,
            severity="warning",
            code="billing_dispute",
            message=f"对账差异 {reason}，未自动扣费",
            created_at=now,
        )
    )
    session.add(
        CallEvent(
            tenant_id=call.tenant_id,
            call_id=call.id,
            event_type="billing_dispute",
            detail={"reason": reason},
            created_at=now,
        )
    )
    return True


async def reconcile_vendor_bill(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    present_call_uuids: set[uuid.UUID],
    period_start: datetime,
    period_end: datetime,
    now: datetime,
) -> int:
    """Local completed calls missing from the trunk bill become disputes. Amounts stay unchanged."""
    moment = _aware(now)
    calls = (
        await session.scalars(
            select(Call).where(
                Call.tenant_id == tenant_id,
                Call.status == "completed",
                Call.start_at.is_not(None),
                Call.start_at >= period_start,
                Call.start_at < period_end,
            )
        )
    ).all()
    opened = 0
    for call in calls:
        if call.call_uuid in present_call_uuids:
            continue
        item = await session.scalar(select(BillingItem).where(BillingItem.call_uuid == call.call_uuid))
        if await _open_dispute(session, call=call, item=item, reason="vendor_bill_missing", now=moment):
            opened += 1
    await session.flush()
    return opened
