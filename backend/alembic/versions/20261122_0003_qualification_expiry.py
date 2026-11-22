"""Qualification expiry timestamp.

Revision ID: 20261122_0003
Revises: 20261122_0002
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261122_0003"
down_revision = "20261122_0002"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("qualifications")}
    return "expires_at" in columns


def upgrade() -> None:
    if not _has_column():
        op.add_column("qualifications", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("qualifications", "expires_at")
