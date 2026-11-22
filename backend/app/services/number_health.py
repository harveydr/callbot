"""Caller health. A low answer rate warns that the number may be marked. It does not block the line."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, CallEvent, CallerNumber
from app.models.extra import Alert, NumberHealthStat

_DIALED = ("completed", "no_answer", "busy", "rejected", "failed", "transferring", "transfer_failed")
_ANSWERED = ("completed", "transferring", "transfer_failed")
_FLOOR = Decimal("0.1500")
_MIN_SAMPLE = 5
_ZONE = ZoneInfo("Asia/Shanghai")


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now.astimezone(timezone.utc)


def _day_bounds(now: datetime) -> tuple[datetime, datetime, date]:
    local = _aware(now).astimezone(_ZONE)
    start = datetime(local.year, local.month, local.day, tzinfo=_ZONE)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc), local.date()


def _rate(statuses: list[str]) -> Decimal | None:
    if not statuses:
        return None
    answered = sum(1 for status in statuses if status in _ANSWERED)
    return (Decimal(answered) / Decimal(len(statuses))).quantize(Decimal("0.0001"))


async def _statuses(
    session: AsyncSession,
    caller: CallerNumber,
    *,
    start: datetime,
    end: datetime,
) -> list[str]:
    rows = (
        await session.scalars(
            select(Call.status).where(
                Call.tenant_id == caller.tenant_id,
                Call.caller_number == caller.number,
                Call.start_at.is_not(None),
                Call.start_at >= start,
                Call.start_at < end,
                Call.status.in_(_DIALED),
            )
        )
    ).all()
    return list(rows)


async def refresh_caller_health(session: AsyncSession, caller: CallerNumber, now: datetime) -> Decimal | None:
    """Store today's answer rate and warn once per half hour when the recent rate is under 0.15."""
    moment = _aware(now)
    day_start, day_end, day = _day_bounds(moment)
    rate = _rate(await _statuses(session, caller, start=day_start, end=day_end))
    stat = await session.scalar(
        select(NumberHealthStat).where(
            NumberHealthStat.caller_number_id == caller.id,
            NumberHealthStat.stat_day == day,
        )
    )
    if stat is None:
        session.add(
            NumberHealthStat(
                tenant_id=caller.tenant_id,
                caller_number_id=caller.id,
                stat_day=day,
                answer_rate=rate,
                complaint_count=caller.complaint_count_30d,
            )
        )
    else:
        stat.answer_rate = rate
        stat.complaint_count = caller.complaint_count_30d
    await _flag_low_answer(session, caller, moment)
    await session.flush()
    return rate


async def _flag_low_answer(session: AsyncSession, caller: CallerNumber, now: datetime) -> bool:
    window_start = now - timedelta(minutes=30)
    statuses = await _statuses(session, caller, start=window_start, end=now + timedelta(microseconds=1))
    if len(statuses) < _MIN_SAMPLE:
        return False
    rate = _rate(statuses)
    if rate is None or rate >= _FLOOR:
        return False
    local = now.astimezone(_ZONE)
    slot = 0 if local.minute < 30 else 30
    label = f"{local:%Y-%m-%d} {local.hour:02d}:{slot:02d}"
    message = f"{label} 主叫 {caller.number} 接通率 {rate}，低于 0.15，号码可能被标记"
    existing = await session.scalar(
        select(Alert.id).where(
            Alert.tenant_id == caller.tenant_id,
            Alert.code == "call_answer_rate",
            Alert.message == message,
        )
    )
    if existing is not None:
        return False
    session.add(
        Alert(
            tenant_id=caller.tenant_id,
            severity="warning",
            code="call_answer_rate",
            message=message,
            created_at=now,
        )
    )
    latest = await session.scalar(
        select(Call)
        .where(
            Call.tenant_id == caller.tenant_id,
            Call.caller_number == caller.number,
            Call.start_at.is_not(None),
            Call.start_at >= window_start,
            Call.start_at <= now,
        )
        .order_by(Call.start_at.desc())
        .limit(1)
    )
    if latest is not None:
        session.add(
            CallEvent(
                tenant_id=caller.tenant_id,
                call_id=latest.id,
                event_type="call_answer_rate",
                detail={"caller_number": caller.number, "answer_rate": format(rate, "f"), "hint": "号码可能被标记"},
                created_at=now,
            )
        )
    return True
