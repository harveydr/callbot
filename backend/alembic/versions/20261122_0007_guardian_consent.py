"""Mark contacts that reach a minor, and accept a guardian consent document.

Revision ID: 20261122_0007
Revises: 20261122_0006
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261122_0007"
down_revision = "20261122_0006"
branch_labels = None
depends_on = None

_NEW = (
    "doc_type IN ('business_license','telco_value_added_license','callcenter_license',"
    "'number_certificate','consent_policy','script_filing','guardian_consent','other')"
)
_OLD = (
    "doc_type IN ('business_license','telco_value_added_license','callcenter_license',"
    "'number_certificate','consent_policy','script_filing','other')"
)


def _has_minor() -> bool:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("contacts")}
    return "minor" in columns


def _replace_doc_type(expression: str) -> None:
    op.drop_constraint("doc_type", "qualifications", type_="check")
    op.create_check_constraint("doc_type", "qualifications", expression)


def upgrade() -> None:
    if not _has_minor():
        op.add_column(
            "contacts",
            sa.Column("minor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    _replace_doc_type(_NEW)


def downgrade() -> None:
    _replace_doc_type(_OLD)
    if _has_minor():
        op.drop_column("contacts", "minor")
