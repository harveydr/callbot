"""CDR application. A repeated vendor event posts once. A price gap opens a dispute and does not invent a rate."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, CallEvent
from app.models.extra import BillingDispute, BillingItem


@dataclass(frozen=True)
class VendorCdr:
    vendor_event_id: str
    duration_sec: int
    amount: Decimal | None


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now.astimezone(timezone.utc)


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
    if item.amount is None and cdr.amount is not None:
        reason = "local_amount_unquoted"
    elif item.amount is not None and cdr.amount is not None and item.amount != cdr.amount:
        reason = "amount_mismatch"
    if reason:
        open_dispute = await session.scalar(
            select(BillingDispute.id).where(
                BillingDispute.billing_item_id == item.id,
                BillingDispute.status == "open",
                BillingDispute.reason == reason,
            )
        )
        if open_dispute is None:
            session.add(
                BillingDispute(
                    tenant_id=call.tenant_id,
                    billing_item_id=item.id,
                    reason=reason,
                    status="open",
                    created_at=moment,
                )
            )
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
