"""Monthly partitions for the audit log. Other hot tables keep their foreign keys."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ZONE = ZoneInfo("Asia/Shanghai")


def month_window(moment: datetime) -> tuple[datetime, datetime, str]:
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    local = moment.astimezone(_ZONE).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if local.month == 12:
        end = local.replace(year=local.year + 1, month=1)
    else:
        end = local.replace(month=local.month + 1)
    return local, end, f"audit_logs_{local.year:04d}_{local.month:02d}"


async def ensure_audit_partitions(session: AsyncSession, now: datetime) -> list[str]:
    """Create the Shanghai month that contains now, and the following month."""
    current_start, current_end, _current_name = month_window(now)
    created = [await _ensure(session, current_start)]
    created.append(await _ensure(session, current_end))
    return [name for name in created if name]


async def _ensure(session: AsyncSession, start: datetime) -> str:
    window_start, window_end, name = month_window(start)
    if not name.startswith("audit_logs_"):
        raise ValueError("partition name is invalid")
    exists = await session.scalar(text("SELECT 1 FROM pg_class WHERE relname = :name"), {"name": name})
    if exists:
        return ""
    await session.execute(
        text("SELECT ensure_audit_partition(:name, :start_at, :end_at)"),
        {"name": name, "start_at": window_start, "end_at": window_end},
    )
    return name
