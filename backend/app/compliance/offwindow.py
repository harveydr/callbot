"""A dialed call outside the tenant window raises one critical alert. It does not place another call."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.windows import window_block_reason
from app.models import Call, Campaign, Tenant
from app.models.extra import Alert

_DIALED = (
    "queued",
    "dialing",
    "ringing",
    "answered",
    "talking",
    "interrupted",
    "transferring",
    "transfer_failed",
    "completed",
    "no_answer",
    "busy",
    "rejected",
    "failed",
)


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now.astimezone(timezone.utc)


async def flag_offwindow_calls(session: AsyncSession, now: datetime) -> int:
    """Alert once for each real dial in the last day that sits outside the call window."""
    moment = _aware(now)
    earliest = moment - timedelta(hours=24)
    calls = (
        await session.scalars(
            select(Call).where(
                Call.status.in_(_DIALED),
                Call.start_at.is_not(None),
                Call.start_at >= earliest,
                Call.start_at <= moment,
            )
        )
    ).all()
    opened = 0
    for call in calls:
        started = call.start_at
        if started is None:
            continue
        campaign = await session.scalar(select(Campaign).where(Campaign.id == call.campaign_id))
        tenant = await session.scalar(select(Tenant).where(Tenant.id == call.tenant_id))
        if campaign is None or tenant is None:
            continue
        zone = campaign.timezone or tenant.timezone
        reason = window_block_reason(_aware(started), timezone_name=zone, call_window=campaign.call_window)
        if reason is None:
            continue
        message = f"通话 {call.call_uuid} 在禁呼时段 {reason}"
        existing = await session.scalar(
            select(Alert.id).where(
                Alert.tenant_id == call.tenant_id,
                Alert.code == "offwindow_call",
                Alert.message == message,
            )
        )
        if existing is not None:
            continue
        session.add(
            Alert(
                tenant_id=call.tenant_id,
                severity="critical",
                code="offwindow_call",
                message=message,
                created_at=moment,
            )
        )
        opened += 1
    await session.flush()
    return opened
