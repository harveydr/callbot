"""Retry recording uploads and drop local copies that the shared store already has."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.services.recording_buffer import note_recording_buffer
from app.tasks import durable_task
from app.voice.object_store import configured_store, flush_buffer, oss_mode


async def _run() -> str:
    mode = oss_mode()
    if mode == "off":
        return "local-only"
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            if mode == "incomplete":
                flagged = await note_recording_buffer(session, incomplete=True, overflow=0)
                await session.commit()
                return f"incomplete:{flagged}"
            store = configured_store()
            if store is None:
                return "local-only"
            result = flush_buffer(store, now=datetime.now(timezone.utc))
            flagged = await note_recording_buffer(session, incomplete=False, overflow=len(result.overflow))
            await session.commit()
            return f"uploaded:{len(result.uploaded)} dropped:{len(result.dropped_local)} overflow:{flagged}"
    finally:
        await engine.dispose()


@durable_task(name="flush_recording_buffer")
def flush_recording_buffer_task() -> str:
    return asyncio.run(_run())
