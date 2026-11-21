"""DND, consent, and script caches from PRD §10.3. A Redis miss still reads Postgres."""

from __future__ import annotations

import uuid

from redis.exceptions import RedisError
from sqlalchemy import select

from app.compliance.cache import (
    forget_dnd,
    forget_template,
    jittered,
    load_template_script,
    remember_consent,
    remember_dnd,
    revoke_consent_cache,
)
from app.compliance.gate_pre import pre_dial_gate
from app.db import set_tenant
from app.models import ConsentRecord, DndEntry, ScenarioTemplate
from app.models.extra import ScriptNode
from tests.test_compliance_gate import NOW, _consent, _flags, _views, _world


class _DownRedis:
    async def get(self, *_args: object, **_kwargs: object) -> str:
        raise RedisError("down")

    async def sismember(self, *_args: object, **_kwargs: object) -> int:
        raise RedisError("down")


def test_ttl_jitter_stays_within_ten_percent() -> None:
    samples = [jittered(100) for _ in range(40)]
    assert min(samples) >= 90
    assert max(samples) <= 110


async def _decide(session_factory, redis, ids):
    tenant, trunk, campaign = _views(ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        return await pre_dial_gate(
            session,
            tenant=tenant,
            trunk=trunk,
            campaign=campaign,
            phone_hash=str(ids["phone_hash"]),
            now=NOW,
            flags=_flags(),
            redis=redis,
        )


async def test_dnd_set_blocks_until_the_entry_is_removed(session_factory, redis_client) -> None:
    ids = await _world(session_factory)
    await _consent(session_factory, ids)
    tenant_id = ids["tenant_id"]
    phone_hash = str(ids["phone_hash"])
    try:
        async with session_factory() as session:
            await set_tenant(session, tenant_id)
            session.add(
                DndEntry(
                    tenant_id=tenant_id,
                    phone_hash=phone_hash,
                    phone_last4="1111",
                    source="operator_manual",
                    created_at=NOW,
                )
            )
            await session.commit()
        blocked = await _decide(session_factory, redis_client, ids)
        assert blocked.block_reason == "tenant_dnd"
        async with session_factory() as session:
            await set_tenant(session, tenant_id)
            row = await session.scalar(select(DndEntry).where(DndEntry.phone_hash == phone_hash))
            assert row is not None
            await session.delete(row)
            await session.commit()
        still = await _decide(session_factory, redis_client, ids)
        assert still.block_reason == "tenant_dnd"
        await forget_dnd(redis_client, tenant_id=tenant_id, phone_hash=phone_hash)
        cleared = await _decide(session_factory, redis_client, ids)
        assert cleared.allowed is True
        await remember_dnd(redis_client, tenant_id=tenant_id, phone_hash=phone_hash)
        again = await _decide(session_factory, redis_client, ids)
        assert again.block_reason == "tenant_dnd"
    finally:
        await forget_dnd(redis_client, tenant_id=tenant_id, phone_hash=phone_hash)
        await redis_client.delete("dnd:set:platform", "dnd:bloom:platform", "dnd:ready:platform", "dnd:lock:platform")
        scope = f"tenant:{tenant_id}"
        await redis_client.delete(f"dnd:set:{scope}", f"dnd:bloom:{scope}", f"dnd:ready:{scope}", f"dnd:lock:{scope}")


async def test_consent_cache_and_revoke_marker(session_factory, redis_client) -> None:
    ids = await _world(session_factory)
    tenant_id = ids["tenant_id"]
    phone_hash = str(ids["phone_hash"])
    try:
        missing = await _decide(session_factory, redis_client, ids)
        assert missing.code == "COMPLIANCE_NO_CONSENT"
        ttl = await redis_client.ttl(f"consent:{tenant_id}:{phone_hash}:notification")
        assert 27 <= ttl <= 33
        await _consent(session_factory, ids)
        cached_miss = await _decide(session_factory, redis_client, ids)
        assert cached_miss.code == "COMPLIANCE_NO_CONSENT"
        await remember_consent(
            redis_client,
            tenant_id=tenant_id,
            phone_hash=phone_hash,
            scenario="notification",
            consented_at=NOW,
            expires_at=None,
        )
        allowed = await _decide(session_factory, redis_client, ids)
        assert allowed.allowed is True
        async with session_factory() as session:
            await set_tenant(session, tenant_id)
            row = await session.scalar(select(ConsentRecord).where(ConsentRecord.phone_hash == phone_hash))
            assert row is not None
            await session.delete(row)
            await session.commit()
        held = await _decide(session_factory, redis_client, ids)
        assert held.allowed is True
        await revoke_consent_cache(redis_client, tenant_id=tenant_id, phone_hash=phone_hash, scenario="notification")
        revoked = await _decide(session_factory, redis_client, ids)
        assert revoked.code == "COMPLIANCE_NO_CONSENT"
    finally:
        await redis_client.delete(
            f"consent:{tenant_id}:{phone_hash}:notification",
            f"consent:revoked:{tenant_id}:{phone_hash}:notification",
            "dnd:set:platform",
            "dnd:bloom:platform",
            "dnd:ready:platform",
            "dnd:lock:platform",
        )


async def test_template_cache_clears_when_nodes_are_saved(session_factory, redis_client) -> None:
    ids = await _world(session_factory)
    template_id = uuid.uuid4()
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        template = await session.scalar(select(ScenarioTemplate).where(ScenarioTemplate.tenant_id == ids["tenant_id"]))
        assert template is not None
        template_id = template.id
        session.add(
            ScriptNode(
                template_id=template_id,
                tenant_id=ids["tenant_id"],
                node_key="n_open",
                node_type="mandatory",
                content={"text": "第一版"},
            )
        )
        await session.commit()
    try:
        async with session_factory() as session:
            await set_tenant(session, ids["tenant_id"])
            first = await load_template_script(session, redis_client, template_id)
        async with session_factory() as session:
            await set_tenant(session, ids["tenant_id"])
            node = await session.scalar(select(ScriptNode).where(ScriptNode.template_id == template_id))
            assert node is not None
            node.content = {"text": "第二版"}
            await session.commit()
        async with session_factory() as session:
            await set_tenant(session, ids["tenant_id"])
            cached = await load_template_script(session, redis_client, template_id)
        await forget_template(redis_client, template_id)
        async with session_factory() as session:
            await set_tenant(session, ids["tenant_id"])
            fresh = await load_template_script(session, redis_client, template_id)
        assert first[0]["content"] == {"text": "第一版"}
        assert cached[0]["content"] == {"text": "第一版"}
        assert fresh[0]["content"] == {"text": "第二版"}
        stored = await redis_client.ttl(f"template:{template_id}")
        assert 540 <= stored <= 660
    finally:
        await forget_template(redis_client, template_id)


async def test_a_redis_outage_still_reads_dnd_from_postgres(session_factory) -> None:
    ids = await _world(session_factory)
    await _consent(session_factory, ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        session.add(
            DndEntry(
                tenant_id=ids["tenant_id"],
                phone_hash=str(ids["phone_hash"]),
                phone_last4="1111",
                source="operator_manual",
                created_at=NOW,
            )
        )
        await session.commit()
    decision = await _decide(session_factory, _DownRedis(), ids)
    assert decision.block_reason == "tenant_dnd"
