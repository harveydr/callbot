"""Daily trunk-bill reconcile. Runs at 02:00 Asia/Shanghai and does not invent prices."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.tasks import celery_app
from app.tasks.billing import reconcile_previous_day


async def _run_reconcile() -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            opened = await reconcile_previous_day(session, datetime.now(timezone.utc))
            await session.commit()
            return f"disputes:{opened}"
    finally:
        await engine.dispose()


@celery_app.task(name="billing_reconcile")
def billing_reconcile_task() -> str:
    return asyncio.run(_run_reconcile())
