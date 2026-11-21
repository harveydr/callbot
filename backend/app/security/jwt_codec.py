"""HS256 access and refresh tokens. Refresh reuse is detected in Redis."""

from __future__ import annotations

import time
import uuid
from typing import cast

import jwt

ACCESS_TTL = 30 * 60
REFRESH_TTL = 7 * 24 * 60 * 60


def _encode(payload: dict[str, object], secret: str) -> str:
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        return token.decode("ascii")
    return token


def issue(secret: str, *, user_id: uuid.UUID, role: str, tenant_id: uuid.UUID | None, must_change: bool) -> dict[str, object]:
    now = int(time.time())
    access_jti = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())
    tenant = str(tenant_id) if tenant_id is not None else None
    access = _encode(
        {
            "sub": str(user_id),
            "role": role,
            "tenant_id": tenant,
            "typ": "access",
            "jti": access_jti,
            "mcp": must_change,
            "iat": now,
            "exp": now + ACCESS_TTL,
        },
        secret,
    )
    refresh = _encode(
        {
            "sub": str(user_id),
            "role": role,
            "tenant_id": tenant,
            "typ": "refresh",
            "jti": refresh_jti,
            "mcp": must_change,
            "iat": now,
            "exp": now + REFRESH_TTL,
        },
        secret,
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": ACCESS_TTL,
        "refresh_jti": refresh_jti,
        "refresh_ttl": REFRESH_TTL,
        "access_jti": access_jti,
        "access_exp": now + ACCESS_TTL,
    }


def decode(token: str, secret: str, *, expected_typ: str) -> dict[str, object]:
    try:
        raw = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise jwt.InvalidTokenError("invalid token") from exc
    claims = {str(key): value for key, value in cast(dict[object, object], raw).items()}
    if claims.get("typ") != expected_typ:
        raise jwt.InvalidTokenError("wrong token type")
    return claims
