"""Report download links expire 15 minutes after they are issued."""

from __future__ import annotations

import csv
import io
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import Call, Tenant
from app.models.extra import ReportExport
from app.services.control import overview

_METRIC_KEYS = (
    "calls",
    "completed",
    "blocked_compliance",
    "demo_excluded",
    "answer_rate",
    "avg_duration_sec",
    "intent_a",
    "intent_b",
    "intent_c",
    "intent_d",
    "intent_na",
    "transfer_rate",
    "reject_rate",
    "ai_complete_rate",
    "top_compliance_rule",
    "no_consent_blocks",
    "dnd_blocks",
    "unquoted_items",
)


def export_expires_at(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now + timedelta(minutes=15)


def export_is_usable(*, expires_at: datetime, now: datetime) -> bool:
    if expires_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return expires_at > now


def build_report_csv(metrics: dict[str, object], calls: list[Call]) -> str:
    """Overview plus call rows. Phone numbers are not columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["metric", "value"])
    for key in _METRIC_KEYS:
        value = metrics.get(key)
        writer.writerow([key, "" if value is None else value])
    writer.writerow([])
    writer.writerow(["call_uuid", "status", "duration_sec", "intent_grade", "start_at"])
    for call in calls:
        started = "" if call.start_at is None else call.start_at.isoformat()
        writer.writerow([str(call.call_uuid), call.status, call.duration_sec, call.intent_grade or "", started])
    return buffer.getvalue()


def export_payload(row: ReportExport, now: datetime) -> dict[str, object]:
    usable = (
        row.status == "done"
        and row.expires_at is not None
        and row.download_token is not None
        and export_is_usable(expires_at=row.expires_at, now=now)
    )
    return {
        "task_id": str(row.id),
        "status": row.status,
        "download_token": row.download_token if usable else None,
    }


async def render_report(session: AsyncSession, export_id: uuid.UUID, now: datetime) -> ReportExport:
    """Fill one export. A finished row keeps its token and file."""
    row = await session.scalar(select(ReportExport).where(ReportExport.id == export_id))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "export not found")
    if row.status == "done":
        return row
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    try:
        metrics = await overview(session, row.tenant_id)
        tenant = await session.scalar(select(Tenant).where(Tenant.id == row.tenant_id))
        calls: list[Call] = []
        if tenant is not None and not tenant.is_demo:
            calls = list(
                (
                    await session.scalars(
                        select(Call)
                        .where(Call.tenant_id == row.tenant_id)
                        .order_by(Call.start_at.asc().nulls_last(), Call.created_at)
                    )
                ).all()
            )
        row.body = build_report_csv(metrics, calls)
        row.status = "done"
        row.download_token = secrets.token_urlsafe(24)
        row.expires_at = export_expires_at(now)
    except Exception:
        row.status = "failed"
        await session.flush()
        raise
    await session.flush()
    return row
