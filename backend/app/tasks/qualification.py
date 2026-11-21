"""Daily qualification sweep. Expired business licenses suspend the tenant without a dial."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.compliance.qualification import sweep_qualifications
from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.tasks import celery_app


async def _run_sweep() -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            suspended = await sweep_qualifications(session, datetime.now(timezone.utc))
            await session.commit()
            return f"suspended:{suspended}"
    finally:
        await engine.dispose()


@celery_app.task(name="sweep_qualifications")
def sweep_qualifications_task() -> str:
    return asyncio.run(_run_sweep())
