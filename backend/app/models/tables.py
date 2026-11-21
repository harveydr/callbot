"""Tables used by the control plane. DDL lives in the Alembic revision."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("status IN ('pending','active','suspended','closed')", name="status"),
        CheckConstraint("compliance_mode IN ('strict','custom')", name="compliance_mode"),
        CheckConstraint("max_concurrency BETWEEN 1 AND 1000", name="max_concurrency"),
        CheckConstraint("daily_call_quota > 0", name="daily_call_quota"),
        CheckConstraint("billing_budget IS NULL OR billing_budget > 0", name="billing_budget_positive"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    plan_code: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'trial'"))
    allowed_scenarios: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default=text("2"))
    daily_call_quota: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("200"))
    billing_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    compliance_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'strict'"))
    rls_bypass: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Asia/Shanghai'"))
    compliance_config: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    data_use_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('platform_admin','tenant_admin','operator','agent','auditor')",
            name="role",
        ),
        CheckConstraint("status IN ('active','locked','disabled')", name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Qualification(Base):
    __tablename__ = "qualifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_type", "file_key"),
        CheckConstraint(
            "doc_type IN ('business_license','telco_value_added_license','callcenter_license',"
            "'number_certificate','consent_policy','script_filing','guardian_consent','other')",
            name="doc_type",
        ),
        CheckConstraint("status IN ('pending','approved','rejected','expired')", name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    doc_no: Mapped[str | None] = mapped_column(Text)
    file_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remark: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class Trunk(Base):
    __tablename__ = "trunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint("mode IN ('byoc','managed')", name="mode"),
        CheckConstraint("sip_transport IN ('udp','tcp','tls')", name="sip_transport"),
        CheckConstraint("status IN ('inactive','active','blocked')", name="status"),
        CheckConstraint("max_concurrency > 0", name="max_concurrency"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'byoc'"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    sip_host: Mapped[str] = mapped_column(Text, nullable=False)
    sip_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5060"))
    sip_transport: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'udp'"))
    auth_user: Mapped[str | None] = mapped_column(Text)
    auth_credential_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default=text("5"))
    allowed_scenarios: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'inactive'"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tenant: Mapped[Tenant] = relationship(lazy="raise")


class CallerNumber(Base):
    __tablename__ = "caller_numbers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        CheckConstraint("status IN ('active','cooling','blocked')", name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    trunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trunks.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    complaint_count_30d: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _ts()


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_hash", "scenario_code"),
        CheckConstraint(
            "source IN ('form','app','contract','sms_confirm','ivr_confirm','import_with_proof','offline_sign')",
            name="source",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    phone_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    phone_last4: Mapped[str] = mapped_column(Text, nullable=False)
    phone_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_code: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    proof_key: Mapped[str | None] = mapped_column(Text)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class ScenarioTemplate(Base):
    __tablename__ = "scenario_templates"
    __table_args__ = (
        CheckConstraint("status IN ('draft','pending_review','approved','rejected')", name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    nodes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    tenant: Mapped[Tenant | None] = relationship(lazy="raise")


class ContactList(Base):
    __tablename__ = "contact_lists"
    __table_args__ = (CheckConstraint("import_status IN ('idle','running','done','failed')", name="import_status"),)

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_code: Mapped[str] = mapped_column(Text, nullable=False)
    import_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'idle'"))
    file_md5: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tenant: Mapped[Tenant] = relationship(lazy="raise")


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("list_id", "phone_hash"),
        CheckConstraint("dial_class IN ('dialable','no_consent','dnd_hit','invalid')", name="dial_class"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    list_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contact_lists.id", ondelete="CASCADE"))
    phone_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    phone_last4: Mapped[str] = mapped_column(Text, nullable=False)
    phone_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    dial_class: Mapped[str] = mapped_column(Text, nullable=False)
    minor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    tenant: Mapped[Tenant] = relationship(lazy="raise")
    contact_list: Mapped[ContactList] = relationship(lazy="raise")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','pending_review','approved','running','paused','done','aborted')",
            name="status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_code: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_templates.id", ondelete="RESTRICT")
    )
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    list_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contact_lists.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    call_window: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'09:00-19:00'"))
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Asia/Shanghai'"))
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default=text("2"))
    freq_per_phone_daily: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    freq_per_phone_7d: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    freq_per_phone_30d: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("6"))
    retry_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("""'{"max_retry"\\:2,"interval_min"\\:[60,1440]}'::jsonb"""),
    )
    trunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trunks.id", ondelete="RESTRICT"))
    caller_number_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("caller_numbers.id", ondelete="SET NULL")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tenant: Mapped[Tenant] = relationship(lazy="raise")
    template: Mapped[ScenarioTemplate] = relationship(lazy="raise")
    contact_list: Mapped[ContactList] = relationship(lazy="raise")
    trunk: Mapped[Trunk] = relationship(lazy="raise")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id", "attempt_no"),)

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"))
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="RESTRICT"))
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','dialing','ringing','answered','talking','interrupted',"
            "'transferring','transfer_failed','completed','no_answer','busy','rejected',"
            "'failed','blocked_compliance','cancelled')",
            name="status",
        ),
        CheckConstraint("intent_grade IS NULL OR intent_grade IN ('A','B','C','D','NA')", name="intent_grade"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"))
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="RESTRICT"))
    call_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    block_reason: Mapped[str | None] = mapped_column(Text)
    caller_number: Mapped[str | None] = mapped_column(Text)
    trunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("trunks.id", ondelete="SET NULL"))
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    ai_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    human_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    barge_in_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    latency_p50_ms: Mapped[int | None] = mapped_column(Integer)
    latency_p95_ms: Mapped[int | None] = mapped_column(Integer)
    cdr: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    intent_grade: Mapped[str | None] = mapped_column(Text)
    compliance_hit: Mapped[list[object]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class CallEvent(Base):
    __tablename__ = "call_events"

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _ts()


class DndEntry(Base):
    __tablename__ = "dnd_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_hash"),
        CheckConstraint(
            "source IN ('user_said_no','complaint','carrier_report','operator_manual','global_12321')",
            name="source",
        ),
        CheckConstraint("scope IN ('tenant','platform')", name="scope"),
    )

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))
    phone_hash: Mapped[str] = mapped_column(Text, nullable=False)
    phone_last4: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'tenant'"))
    permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _ts()


class PlatformDnd(Base):
    __tablename__ = "platform_dnd"

    id: Mapped[uuid.UUID] = _uuid()
    phone_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    phone_last4: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    trace_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
