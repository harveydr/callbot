"""Complaint response and resolution deadlines.

Revision ID: 20261122_0010
Revises: 20261122_0009
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261122_0010"
down_revision = "20261122_0009"
branch_labels = None
depends_on = None

_OPTIONAL = ("responded_at", "resolved_at", "disposition")
_REQUIRED = ("respond_due_at", "resolve_due_at")


def _present() -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns("complaints")}


def upgrade() -> None:
    present = _present()
    columns = {
        "respond_due_at": sa.Column("respond_due_at", sa.DateTime(timezone=True), nullable=True),
        "resolve_due_at": sa.Column("resolve_due_at", sa.DateTime(timezone=True), nullable=True),
        "responded_at": sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        "resolved_at": sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        "disposition": sa.Column("disposition", sa.Text(), nullable=True),
    }
    for name, column in columns.items():
        if name not in present:
            op.add_column("complaints", column)
    op.execute(
        "UPDATE complaints SET respond_due_at = created_at + interval '24 hours' WHERE respond_due_at IS NULL"
    )
    bind = op.get_bind()
    pending = bind.execute(sa.text("SELECT id, created_at FROM complaints WHERE resolve_due_at IS NULL")).fetchall()
    if pending:
        from app.compliance.complaint_sla import resolve_due

        for row in pending:
            bind.execute(
                sa.text("UPDATE complaints SET resolve_due_at = :due WHERE id = :id"),
                {"due": resolve_due(row.created_at), "id": row.id},
            )
    for name in _REQUIRED:
        op.alter_column("complaints", name, nullable=False)


def downgrade() -> None:
    present = _present()
    for name in (*_REQUIRED, *_OPTIONAL):
        if name in present:
            op.drop_column("complaints", name)
