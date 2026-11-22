"""Store a rendered report until its download link expires.

Revision ID: 20261122_0005
Revises: 20261122_0004
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261122_0005"
down_revision = "20261122_0004"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("report_exports")}
    return "body" in columns


def upgrade() -> None:
    if not _has_column():
        op.add_column("report_exports", sa.Column("body", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("report_exports", "body")
