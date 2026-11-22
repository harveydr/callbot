"""Create the next audit-log month before inserts reach the default partition."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.services.audit_partitions import ensure_audit_partitions
from app.tasks import durable_task


async def _run() -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            created = await ensure_audit_partitions(session, datetime.now(timezone.utc))
            await session.commit()
            return "partitions:" + ",".join(created)
    finally:
        await engine.dispose()


@durable_task(name="ensure_audit_partitions")
def ensure_audit_partitions_task() -> str:
    return asyncio.run(_run())
