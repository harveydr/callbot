"""Daily trunk-bill reconcile. Runs at 02:00 Asia/Shanghai and does not invent prices."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import load_settings
from app.db import make_engine, make_session_factory, set_bypass
from app.tasks import durable_task
from app.tasks.billing import probe_missing_cdr, reconcile_previous_day, unavailable_cdr


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


async def _run_probe() -> str:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            await set_bypass(session)
            probed = await probe_missing_cdr(session, datetime.now(timezone.utc), unavailable_cdr)
            await session.commit()
            return f"probes:{probed}"
    finally:
        await engine.dispose()


@durable_task(name="billing_reconcile")
def billing_reconcile_task() -> str:
    return asyncio.run(_run_reconcile())


@durable_task(name="probe_missing_cdr")
def probe_missing_cdr_task() -> str:
    return asyncio.run(_run_probe())
