"""Optional expiry on a platform do-not-call entry.

Revision ID: 20261122_0011
Revises: 20261122_0010
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261122_0011"
down_revision = "20261122_0010"
branch_labels = None
depends_on = None


def _present() -> bool:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("platform_dnd")}
    return "expires_at" in columns


def upgrade() -> None:
    if not _present():
        op.add_column("platform_dnd", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _present():
        op.drop_column("platform_dnd", "expires_at")
