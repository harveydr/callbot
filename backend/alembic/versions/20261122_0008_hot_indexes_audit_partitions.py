"""Hot-table indexes and monthly audit-log partitions.

Revision ID: 20261122_0008
Revises: 20261122_0007
Create Date: 2026-11-22
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from alembic import op
from sqlalchemy import text

revision = "20261122_0008"
down_revision = "20261122_0007"
branch_labels = None
depends_on = None

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_calls_tenant_contact_created ON calls (tenant_id, contact_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_calls_campaign_status_created ON calls (campaign_id, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_pending_next_run ON tasks (status, next_run_at) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS ix_transcripts_summary_fts ON transcripts USING gin (to_tsvector('simple', coalesce(summary, '')))",
    "CREATE INDEX IF NOT EXISTS ix_billing_items_tenant_created ON billing_items (tenant_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_complaints_tenant_status_created ON complaints (tenant_id, status, created_at DESC)",
)
_INDEX_NAMES = (
    "ix_calls_tenant_contact_created",
    "ix_calls_campaign_status_created",
    "ix_tasks_pending_next_run",
    "ix_transcripts_summary_fts",
    "ix_billing_items_tenant_created",
    "ix_complaints_tenant_status_created",
)


def _relkind(name: str) -> str | None:
    bind = op.get_bind()
    return bind.execute(text("SELECT relkind FROM pg_class WHERE relname = :name"), {"name": name}).scalar()


def _month_start(moment: datetime) -> datetime:
    local = moment.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(start: datetime, count: int) -> datetime:
    month = start.month - 1 + count
    year = start.year + month // 12
    month = month % 12 + 1
    return start.replace(year=year, month=month)


def _literal(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")


def _partition(name: str, start: datetime, end: datetime) -> None:
    op.execute(
        f"CREATE TABLE {name} PARTITION OF audit_logs "
        f"FOR VALUES FROM ('{_literal(start)}') TO ('{_literal(end)}')"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO echodesk_app")


def _isolate_audit() -> None:
    match = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    bypass = "current_setting('app.rls_bypass', true) = 'on'"
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON audit_logs "
        f"USING ({bypass} OR tenant_id = {match}) "
        f"WITH CHECK ({bypass} OR tenant_id = {match})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON audit_logs TO echodesk_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO echodesk_app")


def _create_partitioned() -> None:
    op.execute(
        """
        CREATE TABLE audit_logs (
            id bigint GENERATED ALWAYS AS IDENTITY,
            tenant_id uuid,
            user_id uuid,
            action text NOT NULL,
            target_type text,
            target_id text,
            detail jsonb NOT NULL DEFAULT '{}'::jsonb,
            trace_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    current = _month_start(datetime.now(timezone.utc))
    for offset in (-1, 0, 1):
        start = _add_months(current, offset)
        end = _add_months(start, 1)
        _partition(f"audit_logs_{start.year:04d}_{start.month:02d}", start, end)
    op.execute("CREATE TABLE audit_logs_default PARTITION OF audit_logs DEFAULT")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON audit_logs_default TO echodesk_app")
    _isolate_audit()
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ensure_audit_partition(
            partition_name text, start_at timestamptz, end_at timestamptz
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            IF partition_name !~ '^audit_logs_[0-9]{4}_[0-9]{2}$' THEN
                RAISE EXCEPTION 'partition name is invalid';
            END IF;
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF audit_logs FOR VALUES FROM (%L) TO (%L)',
                partition_name, start_at, end_at
            );
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO echodesk_app',
                partition_name
            );
        END;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION ensure_audit_partition(text, timestamptz, timestamptz) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION ensure_audit_partition(text, timestamptz, timestamptz) TO echodesk_app")


def _copy_into_partitioned() -> None:
    op.execute(
        """
        INSERT INTO audit_logs (
            id, tenant_id, user_id, action, target_type, target_id, detail, trace_id, created_at
        )
        OVERRIDING SYSTEM VALUE
        SELECT id, tenant_id, user_id, action, target_type, target_id, detail, trace_id, created_at
        FROM audit_logs_old
        """
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('audit_logs', 'id'),
            COALESCE((SELECT MAX(id) FROM audit_logs), 1),
            (SELECT MAX(id) FROM audit_logs) IS NOT NULL
        )
        """
    )


def upgrade() -> None:
    for statement in _INDEXES:
        op.execute(statement)
    if _relkind("audit_logs") != "p":
        op.execute("ALTER TABLE audit_logs RENAME TO audit_logs_old")
        _create_partitioned()
        _copy_into_partitioned()
        op.execute("DROP TABLE audit_logs_old")


def downgrade() -> None:
    for name in _INDEX_NAMES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("DROP FUNCTION IF EXISTS ensure_audit_partition(text, timestamptz, timestamptz)")
    if _relkind("audit_logs") != "p":
        return
    op.execute(
        """
        CREATE TABLE audit_logs_plain (
            id bigint GENERATED ALWAYS AS IDENTITY,
            tenant_id uuid,
            user_id uuid,
            action text NOT NULL,
            target_type text,
            target_id text,
            detail jsonb NOT NULL DEFAULT '{}'::jsonb,
            trace_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO audit_logs_plain (
            id, tenant_id, user_id, action, target_type, target_id, detail, trace_id, created_at
        )
        OVERRIDING SYSTEM VALUE
        SELECT id, tenant_id, user_id, action, target_type, target_id, detail, trace_id, created_at
        FROM audit_logs
        """
    )
    op.execute("DROP TABLE audit_logs")
    op.execute("ALTER TABLE audit_logs_plain RENAME TO audit_logs")
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('audit_logs', 'id'),
            COALESCE((SELECT MAX(id) FROM audit_logs), 1),
            (SELECT MAX(id) FROM audit_logs) IS NOT NULL
        )
        """
    )
    _isolate_audit()
