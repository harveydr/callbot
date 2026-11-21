"""EchoDesk API. OpenAPI is disabled unless APP_ENV=dev."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from collections.abc import AsyncIterator
from typing import Any, cast
from contextlib import asynccontextmanager

import jwt
import redis.asyncio as redis_async
from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import tenant_routes as _tenant_routes  # noqa: F401
from app.api.surface import auth_router, envelope, platform_router, tenant_router
from app.config import Settings, load_settings
from app.security.jwt_codec import decode
from app.security.rate_limit import LOGIN_RPS, TENANT_WRITE_RPS, USER_RPS, login_key, reserve, user_key, write_key
from app.services.control import utcnow
from app.services.ops_alerts import record_http
from app.db import make_engine, make_session_factory
from app.errors import ApiError

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Request-ID": "",
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = make_engine(settings.database_url)
    app.state.engine = engine
    app.state.sessions = make_session_factory(engine)
    app.state.redis = cast(Any, redis_async.from_url)(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()
    await engine.dispose()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded:
        return forwarded
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


async def _rate_limit(request: Request, trace_id: str) -> JSONResponse | None:
    if not request.url.path.startswith("/api/"):
        return None
    client = getattr(request.app.state, "redis", None)
    if client is None:
        return None
    settings: Settings = request.app.state.settings
    if request.method == "POST" and request.url.path == "/api/auth/login":
        wait = await reserve(client, login_key(_client_ip(request)), LOGIN_RPS)
        if wait:
            return _limited(trace_id, wait)
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        claims = decode(header.split(" ", 1)[1].strip(), settings.jwt_secret, expected_typ="access")
    except jwt.InvalidTokenError:
        return None
    subject = str(claims.get("sub") or "")
    if subject:
        wait = await reserve(client, user_key(subject), USER_RPS)
        if wait:
            return _limited(trace_id, wait)
    tenant_id = claims.get("tenant_id")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and isinstance(tenant_id, str) and tenant_id:
        wait = await reserve(client, write_key(tenant_id), TENANT_WRITE_RPS)
        if wait:
            return _limited(trace_id, wait)
    return None


def _limited(trace_id: str, wait: int) -> JSONResponse:
    response = JSONResponse(
        status_code=429,
        content=envelope(trace_id, {}, code="RATE_LIMITED", message="too many requests"),
    )
    response.headers["Retry-After"] = str(wait)
    response.headers["X-Request-ID"] = trace_id
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    docs = "/docs" if resolved.app_env == "dev" else None
    app = FastAPI(title="EchoDesk", docs_url=docs, redoc_url=None, openapi_url="/openapi.json" if docs else None, lifespan=lifespan)
    app.state.settings = resolved
    app.state.now = utcnow
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Tenant-Override"],
    )

    @app.middleware("http")
    async def context(request: Request, call_next):  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        limited = await _rate_limit(request, trace_id)
        if limited is not None:
            return limited
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            raw = await request.body()
            content_type = request.headers.get("content-type", "")
            if raw and "application/json" in content_type:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict) and "tenant_id" in parsed:
                    response = JSONResponse(
                        status_code=400,
                        content=envelope(trace_id, {}, code="BAD_REQUEST", message="tenant_id is not accepted"),
                    )
                    response.headers["X-Request-ID"] = trace_id
                    return response
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            elapsed_ms = (time.perf_counter() - started) * 1000
            client = getattr(request.app.state, "redis", None)
            if client is not None:
                await record_http(client, elapsed_ms, datetime.now(timezone.utc))
        response.headers["X-Request-ID"] = trace_id
        for key, value in _SECURITY_HEADERS.items():
            if key == "X-Request-ID":
                continue
            response.headers.setdefault(key, value)
        return response

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        trace_id = getattr(_request.state, "trace_id", "")
        return JSONResponse(status_code=exc.status, content=envelope(trace_id, exc.data, code=exc.code, message=exc.message))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        trace_id = getattr(_request.state, "trace_id", "")
        return JSONResponse(status_code=422, content=envelope(trace_id, {}, code="VALIDATION", message="request validation failed"))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(_request.state, "trace_id", "")
        message = "internal error" if resolved.app_env != "dev" else str(exc)
        return JSONResponse(status_code=500, content=envelope(trace_id, {}, code="INTERNAL", message=message))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/api/t/live/ws")
    async def live_socket(websocket: WebSocket) -> None:
        ticket = websocket.query_params.get("ticket", "")
        owner = await app.state.redis.get(f"ws:{ticket}") if ticket else None
        if not owner:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        await websocket.send_json({"type": "ready"})
        try:
            while True:
                message = await websocket.receive_text()
                await websocket.send_json({"type": "ack", "length": len(message)})
        except Exception:
            return

    app.include_router(auth_router, prefix="/api")
    app.include_router(platform_router, prefix="/api/p")
    app.include_router(tenant_router, prefix="/api/t")
    return app


app = create_app()
