"""In-call guard. Demand-stop writes DND in the same transaction as the hangup."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.opening import opening_is_complete, render_opening
from app.compliance.redline import guard_assistant_text
from app.models import CallEvent, DndEntry

_STOP = ("别再打", "不要再打", "别打了", "不需要")
_COMPLAINT = ("投诉",)
_HUMAN = ("转人工", "找人工", "找你们同事")


@dataclass(frozen=True)
class InCallDecision:
    action: str
    spoken: str
    dnd_written: bool
    compliance_hit: list[str]


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


async def on_caller_text(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    phone_hash: str,
    phone_last4: str,
    text: str,
    company: str,
    purpose: str,
    opening_played: bool,
) -> InCallDecision:
    spoken_opening = ""
    hits: list[str] = []
    if not opening_played:
        spoken_opening = render_opening(company=company, purpose=purpose)
        hits.append("forced_ai_notice")
        session.add(
            CallEvent(
                tenant_id=tenant_id,
                call_id=call_id,
                event_type="ai_notice_forced",
                detail={"opening_complete": opening_is_complete(spoken_opening)},
            )
        )

    if _contains_any(text, _STOP) or _contains_any(text, _COMPLAINT):
        session.add(
            DndEntry(
                tenant_id=tenant_id,
                phone_hash=phone_hash,
                phone_last4=phone_last4,
                source="complaint" if _contains_any(text, _COMPLAINT) else "user_said_no",
                scope="tenant",
                permanent=True,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            CallEvent(
                tenant_id=tenant_id,
                call_id=call_id,
                event_type="dnd_written",
                detail={"text_class": "complaint" if _contains_any(text, _COMPLAINT) else "stop"},
            )
        )
        action = "transfer" if _contains_any(text, _COMPLAINT) else "hangup"
        confirm = "好的，已为您登记不再来电，本次通话就到这里。"
        spoken = f"{spoken_opening}{confirm}" if spoken_opening else confirm
        return InCallDecision(action, spoken, True, hits + ["dnd"])

    if _contains_any(text, _HUMAN):
        return InCallDecision("transfer", spoken_opening or "我为您转接人工同事。", False, hits + ["ask_human"])

    if spoken_opening:
        return InCallDecision("speak", spoken_opening, False, hits)
    return InCallDecision("continue", "", False, hits)


def guard_model_output(text: str, *, prior_hits: int) -> tuple[str, list[str], str]:
    return guard_assistant_text(text, prior_hits=prior_hits)
