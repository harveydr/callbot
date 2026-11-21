"""Qualification clock. An expired business license suspends new calling."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Qualification, Tenant
from app.models.extra import Alert

_WINDOWS = ((1, "critical"), (7, "warning"), (30, "warning"))


def _due_window(delta: timedelta) -> int | None:
    seconds = delta.total_seconds()
    if seconds <= 0:
        return None
    days = seconds / 86400
    if days <= 1:
        return 1
    if days <= 7:
        return 7
    if days <= 30:
        return 30
    return None


async def _alert_once(
    session: AsyncSession, *, tenant_id: uuid.UUID, code: str, message: str, severity: str
) -> None:
    found = await session.scalar(
        select(Alert.id).where(Alert.tenant_id == tenant_id, Alert.code == code, Alert.message == message).limit(1)
    )
    if found is None:
        session.add(Alert(tenant_id=tenant_id, severity=severity, code=code, message=message))


async def apply_qualification_clock(session: AsyncSession, tenant: Tenant, now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    rows = (
        await session.scalars(
            select(Qualification).where(
                Qualification.tenant_id == tenant.id,
                Qualification.status.in_(("approved", "expired")),
                Qualification.expires_at.is_not(None),
            )
        )
    ).all()
    for row in rows:
        expires_at = row.expires_at
        if expires_at is None:
            continue
        if expires_at <= now:
            row.status = "expired"
            await _alert_once(
                session,
                tenant_id=tenant.id,
                code="qualification_expired",
                message=str(row.id),
                severity="critical",
            )
            if row.doc_type == "business_license" and tenant.status == "active":
                tenant.status = "suspended"
                tenant.updated_at = now
            continue
        window = _due_window(expires_at - now)
        if window is None:
            continue
        severity = next(level for days, level in _WINDOWS if days == window)
        await _alert_once(
            session,
            tenant_id=tenant.id,
            code=f"qualification_due_{window}",
            message=str(row.id),
            severity=severity,
        )
