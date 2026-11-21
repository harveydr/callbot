"""Report worker. The export request records the task and this task writes the file."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.services.exports import render_report
from app.tasks import celery_app


async def _run(export_id: str) -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            row = await render_report(session, uuid.UUID(export_id), datetime.now(timezone.utc))
            await session.commit()
            return f"report:{row.status}"
    finally:
        await engine.dispose()


@celery_app.task(name="render_report")
def render_report_task(export_id: str) -> str:
    return asyncio.run(_run(export_id))
