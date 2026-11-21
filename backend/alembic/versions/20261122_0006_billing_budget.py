"""Monthly spend cap. Null means the tenant has not set one.

Revision ID: 20261122_0006
Revises: 20261122_0005
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261122_0006"
down_revision = "20261122_0005"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("tenants")}
    return "billing_budget" in columns


def upgrade() -> None:
    if not _has_column():
        op.add_column("tenants", sa.Column("billing_budget", sa.Numeric(12, 4), nullable=True))
        op.create_check_constraint(
            "billing_budget_positive",
            "tenants",
            "billing_budget IS NULL OR billing_budget > 0",
        )


def downgrade() -> None:
    if _has_column():
        op.drop_constraint("billing_budget_positive", "tenants", type_="check")
        op.drop_column("tenants", "billing_budget")
