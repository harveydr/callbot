"""Daily erasure of list data for tenants closed at least 30 days."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.services.erasure import purge_closed_tenants
from app.tasks import durable_task


async def _run() -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            erased = await purge_closed_tenants(session, datetime.now(timezone.utc))
            await session.commit()
            return "purged:" + ",".join(erased)
    finally:
        await engine.dispose()


@durable_task(name="purge_closed_tenants")
def purge_closed_tenants_task() -> str:
    return asyncio.run(_run())
