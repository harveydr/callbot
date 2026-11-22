"""Per-call and per-intent cost from contract rates. A missing price or meter stays blank."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, CallEvent, Tenant
from app.models.extra import BillingRate
from app.services.billing_budget import month_window

_CODES = ("trunk_minute", "asr_minute", "tts_minute", "llm_token", "infra_month", "number_month")
_ANSWERED = ("completed", "transferring", "transfer_failed")
_QUANT = Decimal("0.0001")
_METERS = ("asr_sec", "tts_sec", "llm_tokens")


def per_call_cost(
    prices: dict[str, Decimal],
    *,
    avg_duration_sec: Decimal,
    avg_asr_sec: Decimal,
    avg_tts_sec: Decimal,
    avg_llm_tokens: Decimal,
    month_calls: int,
) -> Decimal:
    """One answered call: timed rates on the average, fixed fees spread across the month."""
    calls = Decimal(month_calls)
    return (
        prices["trunk_minute"] * avg_duration_sec / Decimal(60)
        + prices["asr_minute"] * avg_asr_sec / Decimal(60)
        + prices["tts_minute"] * avg_tts_sec / Decimal(60)
        + prices["llm_token"] * avg_llm_tokens
        + prices["infra_month"] / calls
        + prices["number_month"] / calls
    ).quantize(_QUANT)


def _blank(status: str) -> dict[str, object]:
    return {"cost_per_call": None, "cost_per_intent": None, "cost_status": status}


def _meter(detail: dict[str, object], key: str) -> Decimal | None:
    raw = detail.get(key)
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, str):
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None
    if isinstance(raw, Decimal):
        return raw
    return None


async def _prices(session: AsyncSession, tenant: Tenant, now: datetime) -> dict[str, Decimal]:
    rows = (
        await session.scalars(
            select(BillingRate)
            .where(
                or_(BillingRate.tenant_id == tenant.id, BillingRate.tenant_id.is_(None)),
                BillingRate.item_code.in_(_CODES),
                BillingRate.effective_from <= now,
                BillingRate.unit_price.is_not(None),
            )
            .order_by(BillingRate.effective_from.desc())
        )
    ).all()
    picked: dict[str, tuple[Decimal, bool]] = {}
    for row in rows:
        if row.unit_price is None or row.item_code not in _CODES:
            continue
        price = Decimal(row.unit_price)
        own = row.tenant_id is not None
        current = picked.get(row.item_code)
        if current is None or (own and not current[1]):
            picked[row.item_code] = (price, own)
    return {code: value[0] for code, value in picked.items()}


async def quote_month(session: AsyncSession, tenant: Tenant, now: datetime) -> dict[str, object]:
    prices = await _prices(session, tenant, now)
    if any(code not in prices for code in _CODES):
        return _blank("unquoted")
    start, end, _label = month_window(now, tenant.timezone)
    stamp = func.coalesce(Call.start_at, Call.created_at)
    calls = (
        await session.scalars(
            select(Call).where(
                Call.tenant_id == tenant.id,
                Call.status.in_(_ANSWERED),
                stamp >= start,
                stamp < end,
            )
        )
    ).all()
    if not calls:
        return _blank("no_calls")
    events = (
        await session.scalars(
            select(CallEvent).where(
                CallEvent.tenant_id == tenant.id,
                CallEvent.call_id.in_([call.id for call in calls]),
                CallEvent.event_type == "usage",
            )
        )
    ).all()
    by_call = {event.call_id: event.detail for event in events}
    meters: list[tuple[Decimal, Decimal, Decimal]] = []
    for call in calls:
        detail = by_call.get(call.id) or {}
        asr_sec = _meter(detail, "asr_sec")
        tts_sec = _meter(detail, "tts_sec")
        tokens = _meter(detail, "llm_tokens")
        if asr_sec is None or tts_sec is None or tokens is None:
            return _blank("usage_unmetered")
        meters.append((asr_sec, tts_sec, tokens))
    count = len(calls)
    divisor = Decimal(count)
    per_call = per_call_cost(
        prices,
        avg_duration_sec=sum((Decimal(call.duration_sec) for call in calls), Decimal(0)) / divisor,
        avg_asr_sec=sum((item[0] for item in meters), Decimal(0)) / divisor,
        avg_tts_sec=sum((item[1] for item in meters), Decimal(0)) / divisor,
        avg_llm_tokens=sum((item[2] for item in meters), Decimal(0)) / divisor,
        month_calls=count,
    )
    interested = sum(1 for call in calls if call.intent_grade in {"A", "B"})
    per_intent = None if interested == 0 else ((per_call * Decimal(count)) / Decimal(interested)).quantize(_QUANT)
    return {
        "cost_per_call": format(per_call, "f"),
        "cost_per_intent": None if per_intent is None else format(per_intent, "f"),
        "cost_status": "quoted",
    }
