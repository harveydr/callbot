"""Post-call review. A summary is copied from the transcript, never invented."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.opening import opening_is_complete
from app.compliance.redline import scan_redline
from app.models import Campaign, Task
from app.models.extra import Alert

_MAX_TRANSCRIPT = 4000
_RESCHEDULE = ("改天", "明天再", "稍后联系", "改期", "下次再打")
_STOP = ("别再打", "不要再打", "不需要", "投诉")


@dataclass(frozen=True)
class QcReview:
    result: str
    summary: str | None
    block_script: bool
    detail: dict[str, object]


def wants_reschedule(text: str) -> bool:
    if any(phrase in text for phrase in _STOP):
        return False
    return any(phrase in text for phrase in _RESCHEDULE)


def _summary(*, assistant: str, caller: str, grade: str, present: bool) -> str | None:
    if not present or len(assistant) + len(caller) > _MAX_TRANSCRIPT:
        return None
    if not assistant.strip() and not caller.strip():
        return None
    notes: list[str] = []
    if "人工智能" in assistant:
        notes.append("已表明人工智能身份")
    if "录音" in assistant:
        notes.append("已告知录音")
    if grade == "B":
        notes.append("对方同意")
    elif grade == "D":
        notes.append("对方拒绝")
    elif grade == "C":
        notes.append("对方询问")
    elif caller.strip():
        notes.append("对方已应答")
    else:
        notes.append("对方未应答")
    text = "，".join(notes)
    return text[:60]


def review_call(
    *,
    assistant: str,
    caller: str,
    grade: str,
    redline_hits: list[str],
    transcript_present: bool,
) -> QcReview:
    scanned = scan_redline(assistant)
    hits = list(dict.fromkeys([*redline_hits, *scanned]))
    ai_notice = opening_is_complete(assistant)
    purpose = "本次来电是" in assistant
    opt_out = "不希望再接到" in assistant or "可以直接告诉我" in assistant
    if hits or not ai_notice:
        result = "fail"
    elif not purpose or not opt_out:
        result = "warn"
    else:
        result = "pass"
    if any(phrase in caller for phrase in ("投诉", "转人工", "找人工")):
        next_action = "complaint_ticket" if "投诉" in caller else "callback"
    elif wants_reschedule(caller):
        next_action = "callback"
    else:
        next_action = "close"
    detail: dict[str, object] = {
        "intent_grade": grade,
        "summary": _summary(assistant=assistant, caller=caller, grade=grade, present=transcript_present),
        "structured": {
            "appoint_time": "次日" if wants_reschedule(caller) else "",
            "amount": None,
            "objection": caller if grade == "D" else "",
        },
        "qc": {
            "redline_hits": [{"rule_id": code, "turn_idx": 0} for code in hits],
            "ai_notice_present": ai_notice,
            "purpose_stated": purpose,
            "opt_out_offered": opt_out,
            "result": result,
        },
        "next_action": next_action,
    }
    summary = detail["summary"]
    return QcReview(result, summary if isinstance(summary, str) else None, result == "fail", detail)


async def block_followups(session: AsyncSession, campaign: Campaign) -> None:
    """A failed script stops later tasks of that campaign."""
    campaign.status = "paused"
    pending = (
        await session.scalars(select(Task).where(Task.campaign_id == campaign.id, Task.status == "pending"))
    ).all()
    for task in pending:
        task.status = "cancelled"
    session.add(
        Alert(
            tenant_id=campaign.tenant_id,
            severity="critical",
            code="qc_redline",
            message="话术触碰红线，已暂停后续呼叫",
        )
    )
