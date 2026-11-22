"""A complaint is answered within 24 hours and closed with a result within five business days."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.compliance.complaint_sla import flag_overdue_complaints, resolve_due, respond_due
from app.config import load_settings
from app.db import set_bypass
from app.errors import ApiError
from app.models import Tenant, User
from app.models.extra import Alert, Complaint
from app.security.passwords import hash_password
from app.services.control import open_complaint, progress_complaint
from app.tasks import celery_app

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)
FRIDAY = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)
MONDAY = datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc)


def test_deadlines_use_twenty_four_hours_and_shanghai_weekdays() -> None:
    assert respond_due(FRIDAY) == FRIDAY + timedelta(hours=24)
    assert resolve_due(FRIDAY) == datetime(2026, 11, 27, 2, 0, tzinfo=timezone.utc)
    assert resolve_due(MONDAY) == datetime(2026, 11, 30, 2, 0, tzinfo=timezone.utc)
    assert resolve_due(NOW) == datetime(2026, 11, 27, 2, 0, tzinfo=timezone.utc)


def test_the_sla_sweep_is_hourly() -> None:
    entry = celery_app.conf.beat_schedule["flag-complaint-sla"]
    assert entry["task"] == "flag_complaint_sla"
    assert entry["schedule"].minute == {15}


async def _tenant(session: AsyncSession, code: str, status: str = "active") -> Tenant:
    tenant = Tenant(
        name=code,
        code=code,
        status=status,
        allowed_scenarios=["notification"],
        timezone="Asia/Shanghai",
        is_demo=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def test_opening_a_complaint_stores_both_deadlines(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = load_settings()
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "sla-open")
        user = User(
            tenant_id=tenant.id,
            username="sla-open-admin",
            password_hash=hash_password("password12"),
            display_name="admin",
            role="tenant_admin",
            must_change_password=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(user)
        await session.flush()
        payload = await open_complaint(
            session,
            tenant_id=tenant.id,
            phone="13800007001",
            detail="反复来电",
            settings=settings,
            user_id=user.id,
            trace_id="trace",
            call_id=None,
        )
        row = await session.scalar(select(Complaint).where(Complaint.tenant_id == tenant.id))
        assert row is not None
        assert row.respond_due_at - row.created_at == timedelta(hours=24)
        assert row.resolve_due_at == resolve_due(row.created_at)
        assert payload["dnd"] is True
        assert payload["respond_due_at"] == row.respond_due_at.isoformat()
        try:
            progress_complaint(row, status="closed", disposition="  ", now=NOW)
        except ApiError as exc:
            assert exc.message == "disposition is required"
        else:
            raise AssertionError("closing without a result must be refused")
        closed = progress_complaint(row, status="closed", disposition="已停呼并致歉", now=NOW)
        assert row.responded_at == NOW
        assert row.resolved_at == NOW
        assert row.disposition == "已停呼并致歉"
        assert closed["status"] == "closed"
        assert closed["version"] == 1


async def test_overdue_complaints_are_flagged_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant = await _tenant(session, "sla-late")
        closed_tenant = await _tenant(session, "sla-closed", status="closed")
        late = Complaint(
            tenant_id=tenant.id,
            phone_hash="h-late",
            phone_last4="7002",
            detail="未响应",
            status="open",
            created_at=NOW - timedelta(days=10),
            respond_due_at=NOW - timedelta(hours=1),
            resolve_due_at=NOW - timedelta(days=1),
        )
        ignored = Complaint(
            tenant_id=closed_tenant.id,
            phone_hash="h-closed",
            phone_last4="7003",
            detail="已关闭租户",
            status="open",
            created_at=NOW - timedelta(days=10),
            respond_due_at=NOW - timedelta(hours=1),
            resolve_due_at=NOW - timedelta(days=1),
        )
        session.add_all([late, ignored])
        await session.flush()
        first = await flag_overdue_complaints(session, NOW)
        second = await flag_overdue_complaints(session, NOW)
        codes = set(
            (
                await session.scalars(select(Alert.code).where(Alert.tenant_id == tenant.id))
            ).all()
        )
        foreign = await session.scalar(select(Alert.id).where(Alert.tenant_id == closed_tenant.id))
        assert first == 2
        assert second == 0
        assert codes == {"complaint_response_overdue", "complaint_resolve_overdue"}
        assert foreign is None
