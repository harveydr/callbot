"""Complaint deadlines. A response is due in 24 hours, a result in five business days."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant
from app.models.extra import Alert, Complaint

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def respond_due(created: datetime) -> datetime:
    moment = created if created.tzinfo is not None else created.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc) + timedelta(hours=24)


def resolve_due(created: datetime) -> datetime:
    """Same clock time, five Shanghai weekdays later."""
    moment = created if created.tzinfo is not None else created.replace(tzinfo=timezone.utc)
    cursor = moment.astimezone(_SHANGHAI)
    added = 0
    while added < 5:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            added += 1
    return cursor.astimezone(timezone.utc)


async def flag_overdue_complaints(session: AsyncSession, now: datetime) -> int:
    """Warn once when a response is late, and once when the result is late."""
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    rows = (
        await session.scalars(
            select(Complaint)
            .join(Tenant, Tenant.id == Complaint.tenant_id)
            .where(Tenant.status != "closed", Complaint.status != "closed")
        )
    ).all()
    written = 0
    for row in rows:
        if row.status == "open" and row.responded_at is None and row.respond_due_at <= moment:
            written += await _once(
                session,
                row,
                code="complaint_response_overdue",
                severity="warning",
                message=f"投诉 {row.id} 超过 24 小时未响应",
            )
        if row.resolved_at is None and row.resolve_due_at <= moment:
            written += await _once(
                session,
                row,
                code="complaint_resolve_overdue",
                severity="critical",
                message=f"投诉 {row.id} 超过 5 个工作日未反馈处置结果",
            )
    return written


async def _once(session: AsyncSession, row: Complaint, *, code: str, severity: str, message: str) -> int:
    existing = await session.scalar(
        select(Alert.id).where(Alert.tenant_id == row.tenant_id, Alert.code == code, Alert.message == message)
    )
    if existing is not None:
        return 0
    session.add(Alert(tenant_id=row.tenant_id, severity=severity, code=code, message=message))
    return 1


def complaint_clock(row: Complaint) -> dict[str, object]:
    return {
        "id": str(row.id),
        "status": row.status,
        "version": row.version,
        "respond_due_at": row.respond_due_at.isoformat(),
        "resolve_due_at": row.resolve_due_at.isoformat(),
        "responded_at": None if row.responded_at is None else row.responded_at.isoformat(),
        "resolved_at": None if row.resolved_at is None else row.resolved_at.isoformat(),
        "disposition": row.disposition,
    }
