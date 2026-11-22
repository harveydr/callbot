"""Request dependencies: database session, caller, and row-level scope."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass, set_tenant
from app.errors import ApiError
from app.security.jwt_codec import decode
from app.services.control import audit

_PASSWORD_FREE = {"/api/auth/change-password", "/api/auth/logout", "/api/users/me"}
_AUDITOR_WRITES = {("POST", "/api/t/audit/evidence-pack"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/change-password"), ("POST", "/api/auth/refresh")}


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str
    must_change_password: bool
    jti: str
    exp: int


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.sessions
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_principal(request: Request, session: AsyncSession = Depends(db_session)) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "UNAUTHORIZED", "missing bearer token")
    try:
        claims = decode(header.removeprefix("Bearer ").strip(), request.app.state.settings.jwt_secret, expected_typ="access")
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, "UNAUTHORIZED", "invalid token") from exc
    jti = str(claims.get("jti") or "")
    if await request.app.state.redis.get(f"deny:{jti}"):
        raise ApiError(401, "UNAUTHORIZED", "token revoked")
    tenant_raw = claims.get("tenant_id")
    tenant_id = uuid.UUID(str(tenant_raw)) if tenant_raw else None
    principal = Principal(
        user_id=uuid.UUID(str(claims.get("sub"))),
        tenant_id=tenant_id,
        role=str(claims.get("role")),
        must_change_password=bool(claims.get("mcp")),
        jti=jti,
        exp=int(str(claims.get("exp"))),
    )
    path = request.url.path
    if principal.must_change_password and path not in _PASSWORD_FREE:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "change the initial password first", {"redirect": "/force-password"})
    if principal.role == "auditor" and request.method not in {"GET", "HEAD", "OPTIONS"}:
        if (request.method, path) not in _AUDITOR_WRITES:
            raise ApiError(403, "FORBIDDEN", "auditor is read only")
    if principal.role == "platform_admin":
        override = request.headers.get("x-tenant-override")
        if path.startswith("/api/t/"):
            if not override:
                raise ApiError(400, "BAD_REQUEST", "X-Tenant-Override is required")
            scoped = uuid.UUID(override)
            await set_tenant(session, scoped)
            await audit(
                session,
                tenant_id=scoped,
                user_id=principal.user_id,
                action="tenant.override",
                target_type="tenant",
                target_id=override,
                detail={"path": path},
                trace_id=getattr(request.state, "trace_id", ""),
            )
        else:
            await set_bypass(session)
    elif principal.tenant_id is None:
        raise ApiError(403, "FORBIDDEN", "tenant scope is missing")
    else:
        await set_tenant(session, principal.tenant_id)
    return principal


def require_roles(*roles: str) -> Callable[..., object]:
    async def checker(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in roles:
            raise ApiError(403, "FORBIDDEN", "role is not allowed")
        return principal

    return checker


def require_tenant(principal: Principal) -> uuid.UUID:
    if principal.tenant_id is None:
        raise ApiError(403, "FORBIDDEN", "tenant scope is missing")
    return principal.tenant_id
