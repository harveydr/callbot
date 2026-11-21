"""Import worker. The HTTP request stores the file and this task parses it."""

from __future__ import annotations

import asyncio
import uuid

from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.services.control import run_import_job
from app.tasks import celery_app


async def _run(job_id: str) -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            job = await run_import_job(session, uuid.UUID(job_id), settings)
            await session.commit()
            return f"import:{job.status}"
    finally:
        await engine.dispose()


@celery_app.task(name="import_contacts")
def import_contacts_task(job_id: str) -> str:
    return asyncio.run(_run(job_id))
