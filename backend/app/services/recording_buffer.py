"""Alerts for a recording store that is incomplete or whose local buffer is over 4GB."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extra import Alert

_INCOMPLETE = "对象存储配置不完整，录音仍只在本机"


async def note_recording_buffer(session: AsyncSession, *, incomplete: bool, overflow: int) -> int:
    created = 0
    if incomplete:
        created += await _once(session, "recording_store_incomplete", _INCOMPLETE, "critical")
    if overflow > 0:
        message = f"录音本地缓冲超过 4GB，仍有 {overflow} 个文件未上传"
        created += await _once(session, "recording_buffer_full", message, "critical")
    return created


async def _once(session: AsyncSession, code: str, message: str, severity: str) -> int:
    found = await session.scalar(
        select(Alert.id).where(Alert.tenant_id.is_(None), Alert.code == code, Alert.message == message).limit(1)
    )
    if found is not None:
        return 0
    session.add(Alert(tenant_id=None, severity=severity, code=code, message=message))
    await session.flush()
    return 1
