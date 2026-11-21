"""Hot-path cache for DND, consent, and the published script.

DND lives in a Redis set plus a bloom filter. A bloom miss is definitive only after
the set has been loaded. A new entry is written into the set immediately, and a
revoke marker outranks a cached consent. Redis errors fall through to Postgres.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentRecord, DndEntry, PlatformDnd
from app.models.extra import ScriptNode

_BLOOM_BITS = 1 << 17
_CONSENT_TTL = 120
_MISS_TTL = 30
_TEMPLATE_TTL = 600
_REVOKE_TTL = 180
_CHUNK = 500


def jittered(base_seconds: float) -> int:
    """TTL with a ±10% offset so a crowd of keys does not expire together."""
    span = base_seconds * 0.1
    return max(1, int(round(base_seconds + random.uniform(-span, span))))


def _bits(phone_hash: str) -> tuple[int, int]:
    digest = hashlib.sha256(phone_hash.encode("utf-8")).digest()
    first = int.from_bytes(digest[:4], "big") % _BLOOM_BITS
    second = int.from_bytes(digest[4:8], "big") % _BLOOM_BITS
    return first, second


def _scope_tenant(tenant_id: uuid.UUID) -> str:
    return f"tenant:{tenant_id}"


def _set_key(scope: str) -> str:
    return f"dnd:set:{scope}"


def _bloom_key(scope: str) -> str:
    return f"dnd:bloom:{scope}"


def _ready_key(scope: str) -> str:
    return f"dnd:ready:{scope}"


def _lock_key(scope: str) -> str:
    return f"dnd:lock:{scope}"


def _miss_key(scope: str, phone_hash: str) -> str:
    return f"dnd:miss:{scope}:{phone_hash}"


def _consent_key(tenant_id: uuid.UUID, phone_hash: str, scenario: str) -> str:
    return f"consent:{tenant_id}:{phone_hash}:{scenario}"


def _revoke_key(tenant_id: uuid.UUID, phone_hash: str, scenario: str) -> str:
    return f"consent:revoked:{tenant_id}:{phone_hash}:{scenario}"


async def _bloom_maybe(client: Any, scope: str, phone_hash: str) -> bool:
    first, second = _bits(phone_hash)
    key = _bloom_key(scope)
    return bool(await client.getbit(key, first)) and bool(await client.getbit(key, second))


async def _bloom_add(client: Any, scope: str, phone_hash: str) -> None:
    first, second = _bits(phone_hash)
    key = _bloom_key(scope)
    await client.setbit(key, first, 1)
    await client.setbit(key, second, 1)


async def _answer_if_ready(client: Any, scope: str, phone_hash: str) -> bool | None:
    if await client.get(_ready_key(scope)) != "1":
        return None
    if not await _bloom_maybe(client, scope, phone_hash):
        return False
    return bool(await client.sismember(_set_key(scope), phone_hash))


async def _remember(client: Any, scope: str, phone_hash: str) -> None:
    await client.sadd(_set_key(scope), phone_hash)
    await _bloom_add(client, scope, phone_hash)
    await client.delete(_miss_key(scope, phone_hash))


async def _hydrate(
    client: Any,
    scope: str,
    phone_hash: str,
    load_all: Callable[[], Awaitable[list[str]]],
) -> bool | None:
    owned = await client.set(_lock_key(scope), "1", nx=True, ex=2)
    if not owned:
        ready = await _answer_if_ready(client, scope, phone_hash)
        if ready is not None:
            return ready
        if await client.sismember(_set_key(scope), phone_hash):
            return True
        if await client.get(_miss_key(scope, phone_hash)):
            return False
        return None
    try:
        ready = await _answer_if_ready(client, scope, phone_hash)
        if ready is not None:
            return ready
        hashes = await load_all()
        for start in range(0, len(hashes), _CHUNK):
            chunk = hashes[start : start + _CHUNK]
            if chunk:
                await client.sadd(_set_key(scope), *chunk)
                for item in chunk:
                    await _bloom_add(client, scope, item)
        await client.set(_ready_key(scope), "1")
        if phone_hash in set(hashes):
            return True
        return bool(await client.sismember(_set_key(scope), phone_hash))
    finally:
        await client.delete(_lock_key(scope))


async def _point_dnd(session: AsyncSession, *, tenant_id: uuid.UUID | None, phone_hash: str) -> bool:
    if tenant_id is None:
        found = await session.scalar(select(PlatformDnd.id).where(PlatformDnd.phone_hash == phone_hash).limit(1))
    else:
        found = await session.scalar(
            select(DndEntry.id).where(DndEntry.tenant_id == tenant_id, DndEntry.phone_hash == phone_hash).limit(1)
        )
    return found is not None


async def _scope_hit(
    session: AsyncSession,
    client: Any,
    *,
    scope: str,
    tenant_id: uuid.UUID | None,
    phone_hash: str,
    load_all: Callable[[], Awaitable[list[str]]],
) -> bool:
    ready = await _answer_if_ready(client, scope, phone_hash)
    if ready is not None:
        return ready
    if await client.sismember(_set_key(scope), phone_hash):
        return True
    hydrated = await _hydrate(client, scope, phone_hash, load_all)
    if hydrated is not None:
        return hydrated
    hit = await _point_dnd(session, tenant_id=tenant_id, phone_hash=phone_hash)
    if hit:
        await _remember(client, scope, phone_hash)
    else:
        await client.set(_miss_key(scope, phone_hash), "0", ex=jittered(_MISS_TTL))
    return hit


async def dnd_reason(
    session: AsyncSession,
    client: Any,
    *,
    tenant_id: uuid.UUID,
    phone_hash: str,
) -> str | None:
    """Return platform_dnd, tenant_dnd, or None. Redis failure reads Postgres."""
    try:
        platform = await _scope_hit(
            session,
            client,
            scope="platform",
            tenant_id=None,
            phone_hash=phone_hash,
            load_all=lambda: _load_platform(session),
        )
        if platform:
            return "platform_dnd"
        tenant_hit = await _scope_hit(
            session,
            client,
            scope=_scope_tenant(tenant_id),
            tenant_id=tenant_id,
            phone_hash=phone_hash,
            load_all=lambda: _load_tenant(session, tenant_id),
        )
    except RedisError:
        if await _point_dnd(session, tenant_id=None, phone_hash=phone_hash):
            return "platform_dnd"
        if await _point_dnd(session, tenant_id=tenant_id, phone_hash=phone_hash):
            return "tenant_dnd"
        return None
    if tenant_hit:
        return "tenant_dnd"
    return None


async def _load_platform(session: AsyncSession) -> list[str]:
    rows = (await session.scalars(select(PlatformDnd.phone_hash))).all()
    return [row for row in rows if isinstance(row, str)]


async def _load_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    rows = (await session.scalars(select(DndEntry.phone_hash).where(DndEntry.tenant_id == tenant_id))).all()
    return [row for row in rows if isinstance(row, str)]


async def remember_dnd(client: Any, *, tenant_id: uuid.UUID, phone_hash: str) -> None:
    try:
        await _remember(client, _scope_tenant(tenant_id), phone_hash)
    except RedisError:
        return


async def forget_dnd(client: Any, *, tenant_id: uuid.UUID, phone_hash: str) -> None:
    try:
        scope = _scope_tenant(tenant_id)
        await client.srem(_set_key(scope), phone_hash)
        await client.delete(_miss_key(scope, phone_hash))
    except RedisError:
        return


def _parse_moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else None


def _consent_payload_ok(payload: dict[str, object], now: datetime) -> bool:
    if payload.get("ok") is not True:
        return False
    consented_at = _parse_moment(payload.get("consented_at"))
    if consented_at is None or consented_at > now:
        return False
    expires_at = _parse_moment(payload.get("expires_at"))
    return expires_at is None or expires_at > now


def _dump_consent(row: ConsentRecord | None) -> dict[str, object]:
    if row is None or row.revoked:
        return {"ok": False}
    return {
        "ok": True,
        "consented_at": row.consented_at.isoformat(),
        "expires_at": None if row.expires_at is None else row.expires_at.isoformat(),
    }


async def _read_consent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone_hash: str,
    scenario: str,
) -> ConsentRecord | None:
    return cast(
        ConsentRecord | None,
        await session.scalar(
            select(ConsentRecord).where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.phone_hash == phone_hash,
                ConsentRecord.scenario_code == scenario,
                ConsentRecord.revoked.is_(False),
            )
        ),
    )


async def consent_allows(
    session: AsyncSession,
    client: Any,
    *,
    tenant_id: uuid.UUID,
    phone_hash: str,
    scenario: str,
    now: datetime,
) -> bool:
    """Cached consent. A revoke marker blocks even when a positive entry is still stored."""
    key = _consent_key(tenant_id, phone_hash, scenario)
    marker = _revoke_key(tenant_id, phone_hash, scenario)
    try:
        if await client.get(marker):
            return False
        cached = await client.get(key)
        if isinstance(cached, str):
            payload = json.loads(cached)
            if isinstance(payload, dict):
                return _consent_payload_ok(payload, now)
        row = await _read_consent(session, tenant_id=tenant_id, phone_hash=phone_hash, scenario=scenario)
        if await client.get(marker):
            return False
        payload = _dump_consent(row)
        ttl = jittered(_CONSENT_TTL if payload.get("ok") is True else _MISS_TTL)
        await client.set(key, json.dumps(payload), ex=ttl)
        return _consent_payload_ok(payload, now)
    except (RedisError, json.JSONDecodeError):
        row = await _read_consent(session, tenant_id=tenant_id, phone_hash=phone_hash, scenario=scenario)
        return _consent_payload_ok(_dump_consent(row), now)


async def remember_consent(
    client: Any,
    *,
    tenant_id: uuid.UUID,
    phone_hash: str,
    scenario: str,
    consented_at: datetime,
    expires_at: datetime | None,
) -> None:
    payload = {
        "ok": True,
        "consented_at": consented_at.isoformat(),
        "expires_at": None if expires_at is None else expires_at.isoformat(),
    }
    try:
        await client.delete(_revoke_key(tenant_id, phone_hash, scenario))
        await client.set(
            _consent_key(tenant_id, phone_hash, scenario),
            json.dumps(payload),
            ex=jittered(_CONSENT_TTL),
        )
    except RedisError:
        return


async def revoke_consent_cache(
    client: Any,
    *,
    tenant_id: uuid.UUID,
    phone_hash: str,
    scenario: str,
) -> None:
    try:
        await client.set(_revoke_key(tenant_id, phone_hash, scenario), "1", ex=jittered(_REVOKE_TTL))
        await client.delete(_consent_key(tenant_id, phone_hash, scenario))
    except RedisError:
        return


async def _script_rows(session: AsyncSession, template_id: uuid.UUID) -> list[dict[str, object]]:
    rows = (
        await session.scalars(select(ScriptNode).where(ScriptNode.template_id == template_id).order_by(ScriptNode.node_key))
    ).all()
    return [{"node_key": row.node_key, "node_type": row.node_type, "content": row.content} for row in rows]


async def _script_count(session: AsyncSession, template_id: uuid.UUID) -> int:
    value = await session.scalar(select(func.count()).select_from(ScriptNode).where(ScriptNode.template_id == template_id))
    return int(value or 0)


async def load_template_script(
    session: AsyncSession,
    client: Any,
    template_id: uuid.UUID,
) -> list[dict[str, object]]:
    """Published script. The node count is checked so a newly saved line is not served stale."""
    key = f"template:{template_id}"
    count = await _script_count(session, template_id)
    try:
        cached = await _cached_script(client, key)
        if cached is not None and cached[0] == count:
            return cached[1]
        owned = await client.set(f"template:lock:{template_id}", "1", nx=True, ex=2)
        if not owned:
            cached = await _cached_script(client, key)
            if cached is not None and cached[0] == count:
                return cached[1]
            return await _script_rows(session, template_id)
        try:
            cached = await _cached_script(client, key)
            if cached is not None and cached[0] == count:
                return cached[1]
            rows = await _script_rows(session, template_id)
            payload = {"count": count, "nodes": rows}
            await client.set(key, json.dumps(payload, ensure_ascii=False), ex=jittered(_TEMPLATE_TTL))
            return rows
        finally:
            await client.delete(f"template:lock:{template_id}")
    except (RedisError, json.JSONDecodeError):
        return await _script_rows(session, template_id)


async def _cached_script(client: Any, key: str) -> tuple[int, list[dict[str, object]]] | None:
    cached = await client.get(key)
    if not isinstance(cached, str):
        return None
    loaded = json.loads(cached)
    if not isinstance(loaded, dict) or not isinstance(loaded.get("count"), int) or not isinstance(loaded.get("nodes"), list):
        return None
    nodes = [item for item in loaded["nodes"] if isinstance(item, dict)]
    return loaded["count"], nodes


async def forget_template(client: Any, template_id: uuid.UUID) -> None:
    try:
        await client.delete(f"template:{template_id}")
    except RedisError:
        return
