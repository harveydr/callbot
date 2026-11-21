"""Hot indexes exist, and the audit log can accept a row in a later month."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import AuditLog
from app.services.audit_partitions import ensure_audit_partitions
from app.tasks import celery_app

_INDEXES = {
    "ix_calls_tenant_contact_created",
    "ix_calls_campaign_status_created",
    "ix_tasks_pending_next_run",
    "ix_transcripts_summary_fts",
    "ix_billing_items_tenant_created",
    "ix_complaints_tenant_status_created",
}


async def test_indexes_and_a_future_audit_month_accept_a_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert celery_app.conf.beat_schedule["ensure-audit-partitions"]["task"] == "ensure_audit_partitions"
    async with session_factory() as session:
        await set_bypass(session)
        kind = await session.scalar(text("SELECT relkind::text FROM pg_class WHERE relname = 'audit_logs'"))
        children = (
            await session.scalars(
                text(
                    """
                    SELECT child.relname
                    FROM pg_inherits
                    JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                    WHERE pg_inherits.inhparent = 'audit_logs'::regclass
                    """
                )
            )
        ).all()
        indexes = set((await session.scalars(text("SELECT indexname FROM pg_indexes"))).all())
        assert kind == "p"
        assert "audit_logs_default" in children
        assert len(children) >= 4
        assert _INDEXES <= indexes

        future = datetime(2027, 6, 15, tzinfo=timezone.utc)
        created = await ensure_audit_partitions(session, future)
        again = await ensure_audit_partitions(session, future)
        assert created == ["audit_logs_2027_06", "audit_logs_2027_07"]
        assert again == []
        session.add(AuditLog(action="partition.check", detail={"month": "2027-06"}, created_at=future))
        await session.flush()
        found = await session.scalar(select(AuditLog.id).where(AuditLog.action == "partition.check"))
        assert found is not None
