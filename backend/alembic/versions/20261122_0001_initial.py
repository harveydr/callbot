"""Initial control-plane schema and row level security.

Revision ID: 20261122_0001
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

from app.models.base import Base
from app.models import tables as _tables  # noqa: F401

revision = "20261122_0001"
down_revision = None
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "users",
    "qualifications",
    "trunks",
    "caller_numbers",
    "consent_records",
    "contact_lists",
    "contacts",
    "campaigns",
    "tasks",
    "calls",
    "call_events",
    "dnd_entries",
    "audit_logs",
)

def _isolation(table: str) -> tuple[str, str, str]:
    match = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    bypass = "current_setting('app.rls_bypass', true) = 'on'"
    policy = f"""
        CREATE POLICY tenant_isolation ON {table}
          USING ({bypass} OR tenant_id = {match})
          WITH CHECK ({bypass} OR tenant_id = {match})
        """
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        policy,
    )


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    for table in _TENANT_TABLES:
        for statement in _isolation(table):
            op.execute(statement)
    op.execute("ALTER TABLE scenario_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE scenario_templates FORCE ROW LEVEL SECURITY")
    match = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    bypass = "current_setting('app.rls_bypass', true) = 'on'"
    own = f"{bypass} OR tenant_id = {match}"
    op.execute(
        "CREATE POLICY template_read ON scenario_templates FOR SELECT "
        f"USING ({bypass} OR tenant_id IS NULL OR tenant_id = {match})"
    )
    op.execute(f"CREATE POLICY template_insert ON scenario_templates FOR INSERT WITH CHECK ({own})")
    op.execute(
        "CREATE POLICY template_update ON scenario_templates FOR UPDATE "
        f"USING ({own}) WITH CHECK ({own})"
    )
    op.execute(f"CREATE POLICY template_delete ON scenario_templates FOR DELETE USING ({own})")
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    tenant_match = "id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute(
        "CREATE POLICY tenant_self ON tenants "
        f"USING ({bypass} OR {tenant_match}) WITH CHECK ({bypass} OR {tenant_match})"
    )
    op.execute("ALTER TABLE platform_dnd ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform_dnd FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY platform_dnd_read ON platform_dnd FOR SELECT USING (true)")
    op.execute(
        "CREATE POLICY platform_dnd_write ON platform_dnd FOR ALL "
        f"USING ({bypass}) WITH CHECK ({bypass})"
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'echodesk_app') THEN
            CREATE ROLE echodesk_app LOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO echodesk_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO echodesk_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO echodesk_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_self ON tenants")
    op.execute("DROP POLICY IF EXISTS platform_dnd_write ON platform_dnd")
    op.execute("DROP POLICY IF EXISTS platform_dnd_read ON platform_dnd")
    op.execute("DROP POLICY IF EXISTS template_delete ON scenario_templates")
    op.execute("DROP POLICY IF EXISTS template_update ON scenario_templates")
    op.execute("DROP POLICY IF EXISTS template_insert ON scenario_templates")
    op.execute("DROP POLICY IF EXISTS template_read ON scenario_templates")
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
