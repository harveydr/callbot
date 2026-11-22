"""Hourly complaint SLA sweep. A late ticket is recorded once."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.compliance.complaint_sla import flag_overdue_complaints
from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.tasks import durable_task


async def _run() -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            flagged = await flag_overdue_complaints(session, datetime.now(timezone.utc))
            await session.commit()
            return f"flagged:{flagged}"
    finally:
        await engine.dispose()


@durable_task(name="flag_complaint_sla")
def flag_complaint_sla_task() -> str:
    return asyncio.run(_run())
