"""HTTP surface from the API chapter. Request bodies never accept tenant_id."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import Principal, current_principal, db_session, require_roles, require_tenant
from app.errors import ApiError
from app.models import Call, Qualification, Tenant, User
from app.models.extra import Alert, CircuitState, UserRole
from app.security.passwords import hash_password
from app.services.erasure import close_tenant
from app.services.control import (
    bump,
    change_password,
    create_tenant,
    login,
    paging,
    public_id,
    refresh_session,
    remember,
    replay,
    utcnow,
)

auth_router = APIRouter()
platform_router = APIRouter()
tenant_router = APIRouter()


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=64)
    captcha_token: str | None = None


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=8, max_length=64)
    new_password: str = Field(min_length=8, max_length=64)


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=64)
    role: str
    password: str = Field(min_length=8, max_length=64)


class TenantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=2, max_length=32)


class TenantPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    name: str | None = None
    status: str | None = None
    daily_call_quota: int | None = Field(default=None, gt=0)
    billing_budget: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)


class QualificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_type: str
    file_key: str
    doc_no: str | None = None
    expires_at: datetime | None = None


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    remark: str = ""


class TrunkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    mode: str = "byoc"
    provider: str
    sip_host: str
    sip_port: int = 5060
    sip_transport: str = "udp"
    auth_user: str | None = None
    auth_secret: str | None = None
    max_concurrency: int = Field(default=2, gt=0)
    allowed_scenarios: list[str]


class TrunkPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    status: str | None = None
    max_concurrency: int | None = Field(default=None, gt=0)


class NumberBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trunk_id: uuid.UUID
    number: str
    region: str


class ListBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    scenario_code: str


class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str
    scenario_code: str
    source: str
    proof_key: str | None = None
    consented_at: datetime


class DeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[uuid.UUID]
    confirm: bool
    password: str


class CampaignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    scenario_code: str
    template_id: uuid.UUID
    list_id: uuid.UUID
    trunk_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    call_window: str = "09:00-19:00"
    max_concurrency: int = 2
    freq_per_phone_daily: int = 1
    freq_per_phone_7d: int = 3
    freq_per_phone_30d: int = 6
    retry_policy: dict[str, object] | None = None


class CampaignPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    name: str | None = None
    call_window: str | None = None
    max_concurrency: int | None = None
    status: str | None = None


class DndBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str
    source: str = "operator_manual"


class ComplaintBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str
    detail: str
    call_id: uuid.UUID | None = None


class ComplaintPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    status: str
    disposition: str | None = None


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str
    confirm: bool


class QcReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: str
    remark: str = ""


class NodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[dict[str, object]]


class CircuitReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool
    reason: str
    reviewer_username: str
    reviewer_password: str


def envelope(trace_id: str, data: object, *, code: object = 0, message: str = "ok") -> dict[str, object]:
    return {"code": code, "message": message, "data": data, "trace_id": trace_id}


async def finish(
    session: AsyncSession,
    principal: Principal,
    key: str | None,
    method: str,
    path: str,
    producer: Callable[[], Awaitable[tuple[int, dict[str, object]]]],
) -> tuple[int, dict[str, object]]:
    if not key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required")
    cached = await replay(session, principal.user_id, key)
    if cached is not None:
        body = cached["body"]
        if not isinstance(body, dict):
            raise ApiError(500, "INTERNAL", "stored response is invalid")
        return int(str(cached["status"])), body
    status, body = await producer()
    await remember(
        session,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        key=key,
        method=method,
        path=path,
        status=status,
        body=body,
    )
    return status, body


@auth_router.post("/auth/login")
async def login_route(body: LoginBody, request: Request, session: AsyncSession = Depends(db_session)) -> dict[str, object]:
    data = await login(session, request.app.state.redis, request.app.state.settings, body.username, body.password)
    return envelope(request.state.trace_id, data)


@auth_router.post("/auth/refresh")
async def refresh_route(body: RefreshBody, request: Request, session: AsyncSession = Depends(db_session)) -> dict[str, object]:
    data = await refresh_session(session, request.app.state.redis, request.app.state.settings, body.refresh_token)
    return envelope(request.state.trace_id, data)


@auth_router.post("/auth/logout")
async def logout_route(request: Request, principal: Principal = Depends(current_principal)) -> dict[str, object]:
    ttl = max(principal.exp - int(time.time()), 1)
    await request.app.state.redis.set(f"deny:{principal.jti}", "1", ex=ttl)
    return envelope(request.state.trace_id, {"revoked": True})


@auth_router.post("/auth/change-password")
async def change_route(
    body: PasswordBody,
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    await change_password(session, principal.user_id, body.current_password, body.new_password)
    return envelope(request.state.trace_id, {"must_change_password": False})


@auth_router.get("/users/me")
async def me_route(
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    user = await session.scalar(select(User).where(User.id == principal.user_id))
    if user is None:
        raise ApiError(401, "UNAUTHORIZED", "user no longer exists")
    return envelope(
        request.state.trace_id,
        {
            "id": str(user.id),
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "tenant_id": public_id(user.tenant_id),
            "must_change_password": user.must_change_password,
        },
    )


@tenant_router.post("/members")
async def invite_route(
    body: InviteBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)

    async def produce() -> tuple[int, dict[str, object]]:
        if body.role not in {"tenant_admin", "operator", "agent", "auditor"}:
            raise ApiError(422, "VALIDATION", "role is not invitable")
        if await session.scalar(select(User.id).where(User.username == body.username)):
            raise ApiError(409, "CONFLICT", "username exists")
        user = User(
            tenant_id=tenant_id,
            username=body.username,
            password_hash=hash_password(body.password),
            display_name=body.display_name,
            role=body.role,
            must_change_password=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, tenant_id=tenant_id, role=body.role))
        return 200, {"id": str(user.id), "username": user.username, "role": user.role, "must_change_password": True}

    status, data = await finish(session, principal, idempotency_key, "POST", "/api/t/members", produce)
    return envelope(request.state.trace_id, data) if status == 200 else data


@tenant_router.get("/members")
async def members_route(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (
        await session.scalars(select(User).where(User.tenant_id == tenant_id).order_by(User.created_at.desc()).limit(limit).offset(offset))
    ).all()
    return envelope(
        request.state.trace_id,
        {"items": [{"id": str(row.id), "username": row.username, "role": row.role, "status": row.status} for row in rows], "page": page, "page_size": page_size},
    )


@platform_router.post("/tenants")
async def create_tenant_route(
    body: TenantBody,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    async def produce() -> tuple[int, dict[str, object]]:
        data = await create_tenant(session, name=body.name, code=body.code, trace_id=request.state.trace_id, user_id=principal.user_id)
        return 200, data

    _status, data = await finish(session, principal, idempotency_key, "POST", "/api/p/tenants", produce)
    return envelope(request.state.trace_id, data)


@platform_router.get("/tenants")
async def list_tenants_route(
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    del principal
    limit, offset = paging(page, page_size)
    rows = (await session.scalars(select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset))).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "name": row.name, "code": row.code, "status": row.status, "is_demo": row.is_demo} for row in rows], "page": page, "page_size": page_size})


@platform_router.get("/tenants/{tenant_id}")
async def get_tenant_route(
    tenant_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    del principal
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise ApiError(404, "NOT_FOUND", "tenant not found")
    return envelope(
        request.state.trace_id,
        {
            "id": str(tenant.id),
            "name": tenant.name,
            "status": tenant.status,
            "version": tenant.version,
            "allowed_scenarios": tenant.allowed_scenarios,
            "billing_budget": None if tenant.billing_budget is None else format(tenant.billing_budget, "f"),
        },
    )


@platform_router.patch("/tenants/{tenant_id}")
async def patch_tenant_route(
    tenant_id: uuid.UUID,
    body: TenantPatch,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise ApiError(404, "NOT_FOUND", "tenant not found")
    bump(tenant, body.version)
    if body.name is not None:
        tenant.name = body.name
    if body.daily_call_quota is not None:
        tenant.daily_call_quota = body.daily_call_quota
    if "billing_budget" in body.model_fields_set:
        tenant.billing_budget = body.billing_budget
    if "status" in body.model_fields_set and body.status is not None:
        if body.status != "closed":
            raise ApiError(422, "VALIDATION", "status is invalid")
        await close_tenant(session, tenant, user_id=principal.user_id, trace_id=request.state.trace_id)
    tenant.updated_at = utcnow()
    return envelope(
        request.state.trace_id,
        {
            "id": str(tenant.id),
            "version": tenant.version,
            "status": tenant.status,
            "billing_budget": None if tenant.billing_budget is None else format(tenant.billing_budget, "f"),
        },
    )


@platform_router.post("/tenants/{tenant_id}/qualifications")
async def add_qualification_route(
    tenant_id: uuid.UUID,
    body: QualificationBody,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    del principal
    allowed = {
        "business_license",
        "telco_value_added_license",
        "callcenter_license",
        "number_certificate",
        "consent_policy",
        "script_filing",
        "guardian_consent",
        "other",
    }
    if body.doc_type not in allowed:
        raise ApiError(422, "VALIDATION", "doc_type is invalid")
    if body.expires_at is not None and body.expires_at.tzinfo is None:
        raise ApiError(422, "VALIDATION", "expires_at must be timezone-aware")
    row = Qualification(
        tenant_id=tenant_id,
        doc_type=body.doc_type,
        doc_no=body.doc_no,
        file_key=body.file_key,
        status="pending",
        expires_at=body.expires_at,
    )
    session.add(row)
    await session.flush()
    return envelope(request.state.trace_id, {"id": str(row.id), "status": row.status})


@platform_router.post("/qualifications/{qualification_id}/review")
async def review_qualification_route(
    qualification_id: uuid.UUID,
    body: ReviewBody,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    if body.status not in {"approved", "rejected"}:
        raise ApiError(422, "VALIDATION", "status is invalid")
    row = await session.scalar(select(Qualification).where(Qualification.id == qualification_id))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "qualification not found")
    row.status = body.status
    row.remark = body.remark
    row.reviewed_by = principal.user_id
    row.reviewed_at = utcnow()
    return envelope(request.state.trace_id, {"id": str(row.id), "status": row.status})


async def _set_tenant_status(session: AsyncSession, tenant_id: uuid.UUID, status: str) -> Tenant:
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise ApiError(404, "NOT_FOUND", "tenant not found")
    if status == "active":
        license_row = await session.scalar(
            select(Qualification.id).where(
                Qualification.tenant_id == tenant_id,
                Qualification.doc_type == "business_license",
                Qualification.status == "approved",
            )
        )
        if license_row is None:
            raise ApiError(409, "COMPLIANCE_QUALIFICATION_MISSING", "approved business license is required")
    tenant.status = status
    tenant.updated_at = utcnow()
    tenant.version += 1
    return tenant


@platform_router.post("/tenants/{tenant_id}/suspend")
async def suspend_route(
    tenant_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    del principal
    tenant = await _set_tenant_status(session, tenant_id, "suspended")
    return envelope(request.state.trace_id, {"id": str(tenant.id), "status": tenant.status})


@platform_router.post("/tenants/{tenant_id}/resume")
async def resume_route(
    tenant_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    del principal
    current = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if current is not None and current.status == "closed":
        raise ApiError(409, "CONFLICT", "closed tenant cannot resume")
    tenant = await _set_tenant_status(session, tenant_id, "active")
    return envelope(request.state.trace_id, {"id": str(tenant.id), "status": tenant.status})


@platform_router.get("/metrics")
async def metrics_route(
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    del principal
    tenants = int(await session.scalar(select(func.count()).select_from(Tenant)) or 0)
    calls = int(await session.scalar(select(func.count()).select_from(Call)) or 0)
    return envelope(request.state.trace_id, {"tenants": tenants, "calls": calls, "calling_enabled": request.app.state.settings.calling_enabled})


@platform_router.get("/circuit-status")
async def circuit_route(
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    del principal
    rows = (await session.scalars(select(CircuitState))).all()
    return envelope(
        request.state.trace_id,
        {
            "calling_enabled": request.app.state.settings.calling_enabled,
            "items": [{"tenant_id": str(row.tenant_id), "opened": row.opened, "reason": row.reason} for row in rows],
        },
    )


@platform_router.post("/circuit/{tenant_id}/reset")
async def circuit_reset_route(
    tenant_id: uuid.UUID,
    body: CircuitReset,
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    if not body.confirm or not body.reason.strip():
        raise ApiError(400, "BAD_REQUEST", "dual control confirmation is required")
    reviewer = await session.scalar(select(User).where(User.username == body.reviewer_username, User.role == "platform_admin"))
    from app.security.passwords import verify_password

    if reviewer is None or reviewer.id == principal.user_id or not verify_password(reviewer.password_hash, body.reviewer_password):
        raise ApiError(403, "FORBIDDEN", "reviewer check failed")
    state = await session.scalar(select(CircuitState).where(CircuitState.tenant_id == tenant_id))
    if state is None:
        state = CircuitState(tenant_id=tenant_id, opened=False, reason=body.reason, updated_at=utcnow())
        session.add(state)
    else:
        state.opened = False
        state.reason = body.reason
        state.updated_at = utcnow()
    return envelope(request.state.trace_id, {"tenant_id": str(tenant_id), "opened": False})


@platform_router.get("/alerts")
async def alerts_route(
    request: Request,
    principal: Principal = Depends(require_roles("platform_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    del principal
    rows = (await session.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(100))).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "severity": row.severity, "code": row.code, "message": row.message} for row in rows]})


def _redis(request: Request) -> redis_async.Redis:
    client: redis_async.Redis = request.app.state.redis
    return client
