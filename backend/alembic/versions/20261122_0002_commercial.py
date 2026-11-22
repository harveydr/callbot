"""Commercial tables, row level security, and lookup indexes.

Revision ID: 20261122_0002
Revises: 20261122_0001
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from app.models import extra as _extra  # noqa: F401
from app.models.base import Base

revision = "20261122_0002"
down_revision = "20261122_0001"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "user_roles",
    "call_turns",
    "recordings",
    "transcripts",
    "qc_results",
    "call_transfers",
    "billing_items",
    "billing_disputes",
    "complaints",
    "subscriptions",
    "invoices",
    "number_health_stats",
    "contact_optins",
    "idempotency_keys",
    "import_jobs",
    "evidence_packs",
    "circuit_states",
    "report_exports",
)

_SHARED_READ = ("script_nodes", "redline_rules", "billing_rates", "alerts")

_DROP_ORDER = (
    "report_exports",
    "evidence_packs",
    "import_jobs",
    "idempotency_keys",
    "alerts",
    "circuit_states",
    "billing_disputes",
    "billing_items",
    "invoices",
    "subscriptions",
    "qc_results",
    "transcripts",
    "recordings",
    "call_turns",
    "call_transfers",
    "complaints",
    "contact_optins",
    "number_health_stats",
    "script_nodes",
    "redline_rules",
    "user_roles",
    "billing_rates",
    "plans",
)


def _policy(table: str) -> None:
    match = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    bypass = "current_setting('app.rls_bypass', true) = 'on'"
    own = f"{bypass} OR tenant_id = {match}"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} USING ({own}) WITH CHECK ({own})"
    )


def _shared(table: str) -> None:
    match = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    bypass = "current_setting('app.rls_bypass', true) = 'on'"
    own = f"{bypass} OR tenant_id = {match}"
    read = f"{bypass} OR tenant_id IS NULL OR tenant_id = {match}"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY shared_read ON {table} FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY shared_insert ON {table} FOR INSERT WITH CHECK ({own})")
    op.execute(f"CREATE POLICY shared_update ON {table} FOR UPDATE USING ({own}) WITH CHECK ({own})")
    op.execute(f"CREATE POLICY shared_delete ON {table} FOR DELETE USING ({own})")


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    inspector = inspect(bind)
    campaign_cols = {column["name"] for column in inspector.get_columns("campaigns")}
    if "created_by" not in campaign_cols:
        op.execute("ALTER TABLE campaigns ADD COLUMN created_by uuid REFERENCES users(id) ON DELETE SET NULL")
    call_cols = {column["name"] for column in inspector.get_columns("calls")}
    if "created_by" not in call_cols:
        op.execute("ALTER TABLE calls ADD COLUMN created_by uuid")
    for table in _TENANT_TABLES:
        _policy(table)
    for table in _SHARED_READ:
        _shared(table)
    op.execute("ALTER TABLE plans ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE plans FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY plans_read ON plans FOR SELECT USING (true)")
    op.execute(
        "CREATE POLICY plans_write ON plans FOR ALL "
        "USING (current_setting('app.rls_bypass', true) = 'on') "
        "WITH CHECK (current_setting('app.rls_bypass', true) = 'on')"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_calls_tenant_created ON calls (tenant_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_contacts_phone_hash ON contacts (phone_hash)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO echodesk_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO echodesk_app")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contacts_phone_hash")
    op.execute("DROP INDEX IF EXISTS ix_calls_tenant_created")
    op.execute("DROP POLICY IF EXISTS plans_write ON plans")
    op.execute("DROP POLICY IF EXISTS plans_read ON plans")
    for table in _SHARED_READ:
        op.execute(f"DROP POLICY IF EXISTS shared_delete ON {table}")
        op.execute(f"DROP POLICY IF EXISTS shared_update ON {table}")
        op.execute(f"DROP POLICY IF EXISTS shared_insert ON {table}")
        op.execute(f"DROP POLICY IF EXISTS shared_read ON {table}")
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for table in _DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    bind = op.get_bind()
    inspector = inspect(bind)
    if "campaigns" in inspector.get_table_names():
        cols = {column["name"] for column in inspector.get_columns("campaigns")}
        if "created_by" in cols:
            op.execute("ALTER TABLE campaigns DROP COLUMN created_by")
    if "calls" in inspector.get_table_names():
        cols = {column["name"] for column in inspector.get_columns("calls")}
        if "created_by" in cols:
            op.execute("ALTER TABLE calls DROP COLUMN created_by")
