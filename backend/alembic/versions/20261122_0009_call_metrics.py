"""Call metrics from the PRD call record. CDR stays empty until a trunk sends one.

Revision ID: 20261122_0009
Revises: 20261122_0008
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "20261122_0009"
down_revision = "20261122_0008"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("ai_duration_sec", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("human_duration_sec", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("summary", sa.Text(), nullable=True),
    sa.Column("barge_in_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("latency_p50_ms", sa.Integer(), nullable=True),
    sa.Column("latency_p95_ms", sa.Integer(), nullable=True),
    sa.Column("cdr", postgresql.JSONB(), nullable=True),
)


def _present() -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns("calls")}


def upgrade() -> None:
    present = _present()
    for column in _COLUMNS:
        if column.name not in present:
            op.add_column("calls", column)


def downgrade() -> None:
    present = _present()
    for column in reversed(_COLUMNS):
        if column.name in present:
            op.drop_column("calls", column.name)
