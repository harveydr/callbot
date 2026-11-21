"""Contract rates produce a per-call cost. A missing price or meter stays blank."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import Call, CallEvent
from app.models.extra import BillingRate
from app.services.call_cost import per_call_cost
from app.services.control import billing_summary
from tests.test_dispatch import _world

_PRICES = {
    "trunk_minute": Decimal("1"),
    "asr_minute": Decimal("2"),
    "tts_minute": Decimal("3"),
    "llm_token": Decimal("0.01"),
    "infra_month": Decimal("10"),
    "number_month": Decimal("20"),
}


def test_the_formula_spreads_fixed_fees_across_answered_calls() -> None:
    cost = per_call_cost(
        _PRICES,
        avg_duration_sec=Decimal(60),
        avg_asr_sec=Decimal(60),
        avg_tts_sec=Decimal(30),
        avg_llm_tokens=Decimal(100),
        month_calls=3,
    )
    assert cost == Decimal("15.5000")


async def _quoted(session: AsyncSession, tenant_id, now: datetime, *, trunk: Decimal) -> None:
    for code, price in _PRICES.items():
        session.add(
            BillingRate(
                tenant_id=None,
                item_code=code,
                unit_price=Decimal("9") if code == "trunk_minute" else price,
                effective_from=now - timedelta(days=2),
                note="platform",
            )
        )
        session.add(
            BillingRate(
                tenant_id=tenant_id,
                item_code=code,
                unit_price=trunk if code == "trunk_minute" else price,
                effective_from=now - timedelta(days=1),
                note="contract",
            )
        )
    session.add(
        BillingRate(
            tenant_id=tenant_id,
            item_code="trunk_minute",
            unit_price=Decimal("99"),
            effective_from=now + timedelta(days=1),
            note="future",
        )
    )


async def test_a_full_quote_reports_per_call_and_per_intent(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "cost-quoted", phone="13800006771")
        now = datetime.now(timezone.utc)
        await _quoted(session, tenant.id, now, trunk=Decimal("1"))
        grades = ("A", "B", "C")
        calls = []
        for grade in grades:
            call = Call(
                tenant_id=tenant.id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                call_uuid=uuid.uuid4(),
                status="completed",
                trunk_id=trunk.id,
                template_version=1,
                duration_sec=60,
                intent_grade=grade,
                start_at=now,
            )
            calls.append(call)
        session.add_all(calls)
        await session.flush()
        for call in calls:
            session.add(
                CallEvent(
                    tenant_id=tenant.id,
                    call_id=call.id,
                    event_type="usage",
                    detail={"asr_sec": "60", "tts_sec": "30", "llm_tokens": "100"},
                )
            )
        await session.flush()
        summary = await billing_summary(session, tenant.id)
        assert summary["cost_status"] == "quoted"
        assert summary["cost_per_call"] == "15.5000"
        assert summary["cost_per_intent"] == "23.2500"
        assert summary["amount"] is None


async def test_missing_price_or_meter_leaves_the_cost_blank(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        tenant, trunk, campaign, contact, _caller = await _world(session, "cost-blank", phone="13800006772")
        now = datetime.now(timezone.utc)
        call = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=uuid.uuid4(),
            status="completed",
            trunk_id=trunk.id,
            template_version=1,
            duration_sec=60,
            intent_grade="A",
            start_at=now,
        )
        session.add(call)
        await session.flush()
        session.add(
            CallEvent(
                tenant_id=tenant.id,
                call_id=call.id,
                event_type="usage",
                detail={"asr_sec": "60", "llm_tokens": "100"},
            )
        )
        for code, price in _PRICES.items():
            if code == "tts_minute":
                continue
            session.add(
                BillingRate(
                    tenant_id=tenant.id,
                    item_code=code,
                    unit_price=price,
                    effective_from=now - timedelta(days=1),
                    note="contract",
                )
            )
        await session.flush()
        missing_price = await billing_summary(session, tenant.id)
        assert missing_price["cost_status"] == "unquoted"
        assert missing_price["cost_per_call"] is None
        session.add(
            BillingRate(
                tenant_id=tenant.id,
                item_code="tts_minute",
                unit_price=Decimal("3"),
                effective_from=now - timedelta(days=1),
                note="contract",
            )
        )
        await session.flush()
        missing_meter = await billing_summary(session, tenant.id)
        assert missing_meter["cost_status"] == "usage_unmetered"
        assert missing_meter["cost_per_intent"] is None
