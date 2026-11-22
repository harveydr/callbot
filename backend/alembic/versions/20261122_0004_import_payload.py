"""Store an import upload until the worker finishes parsing it.

Revision ID: 20261122_0004
Revises: 20261122_0003
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261122_0004"
down_revision = "20261122_0003"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("import_jobs")}
    return "payload" in columns


def upgrade() -> None:
    if not _has_column():
        op.add_column("import_jobs", sa.Column("payload", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("import_jobs", "payload")
