"""Campaign dispatch. Compliance runs again immediately before each dial."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.gate_pre import CampaignView, Flags, TenantView, TrunkView, pre_dial_gate
from app.compliance.qualification import apply_qualification_clock
from app.compliance.guard_incall import on_caller_text
from app.compliance.qc_post import block_followups, mark_recording_missing, review_call, wants_reschedule
from app.config import Settings
from app.models import Call, CallEvent, CallerNumber, Campaign, Contact, Task, Tenant, Trunk, User
from app.models.extra import (
    Alert,
    BillingItem,
    BillingRate,
    CallTransfer,
    CallTurn,
    NumberHealthStat,
    QcResult,
    Recording,
    ScriptNode,
    Transcript,
)
from app.services.tokens import TokenBucket
from app.voice.circuit import VendorCircuit, vendor_circuit
from app.voice.orchestrator import Frame, run_session
from app.voice.stereo import EXPLICIT_LABEL, label_meta, write_recording

EXPLICIT_AIGC = EXPLICIT_LABEL
_RETRYABLE = ("no_answer", "busy")
_REJECTED_HEARD = frozenset({"拒接", "用户拒接"})
_REJECT_SAMPLE = 5
_REJECT_RATE = 0.85


def _store_circuit_alerts(session: AsyncSession, tenant_id: uuid.UUID, circuit: VendorCircuit) -> None:
    for code, reason in circuit.drain_alerts():
        session.add(
            Alert(
                tenant_id=tenant_id,
                severity="warning",
                code=code,
                message=f"语音供应商熔断 {reason}",
            )
        )


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now.astimezone(timezone.utc)


async def ensure_initial_tasks(session: AsyncSession, campaign: Campaign) -> int:
    contacts = (
        await session.scalars(
            select(Contact).where(
                Contact.list_id == campaign.list_id,
                Contact.tenant_id == campaign.tenant_id,
                Contact.deleted_at.is_(None),
            )
        )
    ).all()
    created = 0
    for contact in contacts:
        exists = await session.scalar(
            select(Task.id).where(Task.campaign_id == campaign.id, Task.contact_id == contact.id, Task.attempt_no == 1)
        )
        if exists is not None:
            continue
        session.add(
            Task(
                tenant_id=campaign.tenant_id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                attempt_no=1,
                status="pending",
                next_run_at=_aware(datetime.now(timezone.utc)),
            )
        )
        created += 1
    await session.flush()
    return created


async def dial_pending(
    session: AsyncSession,
    *,
    campaign: Campaign,
    tenant: Tenant,
    trunk: Trunk,
    settings: Settings,
    redis: redis_async.Redis,
    now: datetime,
    heard: str = "好的",
) -> int:
    """Dial pending tasks up to the live concurrency limit. Returns calls created."""
    if campaign.status != "running":
        return 0
    moment = _aware(now)
    await apply_qualification_clock(session, tenant, moment)
    bucket = TokenBucket(redis, prefix="")
    flags = Flags(settings.calling_enabled, settings.feature_telesales, settings.feature_collection)
    tasks = (
        await session.scalars(
            select(Task)
            .where(Task.campaign_id == campaign.id, Task.status == "pending")
            .order_by(Task.created_at, Task.attempt_no)
        )
    ).all()
    started = 0
    for task in tasks:
        if started >= campaign.max_concurrency:
            break
        contact = await session.scalar(select(Contact).where(Contact.id == task.contact_id))
        if contact is None or contact.deleted_at is not None:
            task.status = "skipped"
            continue
        decision = await pre_dial_gate(
            session,
            tenant=TenantView(
                tenant.id,
                tenant.status,
                list(tenant.allowed_scenarios),
                tenant.timezone,
                tenant.daily_call_quota,
            ),
            trunk=TrunkView(trunk.mode, trunk.status, list(trunk.allowed_scenarios)),
            campaign=CampaignView(
                campaign.scenario_code,
                campaign.call_window,
                campaign.timezone,
                campaign.freq_per_phone_daily,
                campaign.freq_per_phone_7d,
                campaign.freq_per_phone_30d,
            ),
            phone_hash=contact.phone_hash,
            now=moment,
            flags=flags,
        )
        if not decision.allowed:
            await _blocked(session, campaign, contact, task, decision.code or "", decision.block_reason or "", moment)
            continue
        caller = await _active_caller(session, campaign)
        if campaign.caller_number_id is not None and caller is None:
            await _blocked(session, campaign, contact, task, "COMPLIANCE_SCENARIO_NOT_ALLOWED", "caller_blocked", moment)
            continue
        held = await bucket.acquire(f"live:{tenant.id}", limit=campaign.max_concurrency, ttl_sec=120)
        if not held:
            break
        try:
            await _place(session, campaign, tenant, contact, task, settings, moment, heard, caller)
        except Exception:
            task.status = "failed"
            raise
        started += 1
    for _ in range(started):
        await bucket.release(f"live:{tenant.id}")
    await _pause_if_rejected(session, campaign, moment)
    return started


async def schedule_retries(session: AsyncSession, campaign: Campaign, now: datetime) -> int:
    if campaign.status != "running":
        return 0
    raw_max = campaign.retry_policy.get("max_retry", 0)
    max_retry = raw_max if isinstance(raw_max, int) else int(str(raw_max))
    calls = (
        await session.scalars(select(Call).where(Call.campaign_id == campaign.id, Call.status.in_(_RETRYABLE)))
    ).all()
    created = 0
    for call in calls:
        attempts = (
            await session.scalars(select(Task).where(Task.campaign_id == campaign.id, Task.contact_id == call.contact_id))
        ).all()
        if any(item.status == "pending" for item in attempts):
            continue
        if len(attempts) >= 1 + max_retry:
            continue
        session.add(
            Task(
                tenant_id=campaign.tenant_id,
                campaign_id=campaign.id,
                contact_id=call.contact_id,
                attempt_no=len(attempts) + 1,
                status="pending",
                next_run_at=_aware(now),
            )
        )
        created += 1
    await session.flush()
    return created


async def _handoff(
    session: AsyncSession,
    campaign: Campaign,
    contact: Contact,
    task: Task,
    call: Call,
    now: datetime,
) -> None:
    agent = await session.scalar(
        select(User)
        .where(User.tenant_id == campaign.tenant_id, User.role == "agent", User.status == "active")
        .limit(1)
    )
    if agent is not None:
        session.add(CallTransfer(tenant_id=campaign.tenant_id, call_id=call.id, agent_id=agent.id, status="pending"))
        return
    call.status = "transfer_failed"
    session.add(
        CallEvent(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            event_type="transfer",
            detail={"status": "failed", "no_agent_action": "callback_30min"},
        )
    )
    await _callback(session, campaign, contact, task, call, now, timedelta(minutes=30))


async def _callback(
    session: AsyncSession,
    campaign: Campaign,
    contact: Contact,
    task: Task,
    call: Call,
    now: datetime,
    delay: timedelta,
) -> None:
    attempt_no = task.attempt_no + 1
    exists = await session.scalar(
        select(Task.id).where(
            Task.campaign_id == campaign.id, Task.contact_id == contact.id, Task.attempt_no == attempt_no
        )
    )
    if exists is None:
        session.add(
            Task(
                tenant_id=campaign.tenant_id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                attempt_no=attempt_no,
                status="pending",
                next_run_at=now + delay,
            )
        )
    session.add(
        CallEvent(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            event_type="callback",
            detail={"next_run_at": (now + delay).isoformat()},
        )
    )


async def _touch_health(session: AsyncSession, caller: CallerNumber, *, answered: bool, now: datetime) -> None:
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    stat = await session.scalar(
        select(NumberHealthStat).where(NumberHealthStat.caller_number_id == caller.id, NumberHealthStat.stat_day == day)
    )
    rate = Decimal("1.0000") if answered else Decimal("0.0000")
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
        return
    stat.answer_rate = rate
    stat.complaint_count = caller.complaint_count_30d


async def _blocked(
    session: AsyncSession,
    campaign: Campaign,
    contact: Contact,
    task: Task,
    code: str,
    reason: str,
    now: datetime,
) -> None:
    task.status = "blocked"
    call = Call(
        tenant_id=campaign.tenant_id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        call_uuid=uuid.uuid4(),
        status="blocked_compliance",
        block_reason=reason,
        trunk_id=campaign.trunk_id,
        template_version=campaign.template_version,
        start_at=now,
        end_at=now,
        duration_sec=0,
        compliance_hit=[{"code": code, "reason": reason}],
        created_by=campaign.created_by,
    )
    session.add(call)
    await session.flush()
    session.add(
        CallEvent(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            event_type="gate_block",
            detail={"code": code, "block_reason": reason},
        )
    )


async def _active_caller(session: AsyncSession, campaign: Campaign) -> CallerNumber | None:
    if campaign.caller_number_id is not None:
        row = cast(CallerNumber | None, await session.get(CallerNumber, campaign.caller_number_id))
        if row is None or row.tenant_id != campaign.tenant_id or row.status != "active":
            return None
        return row
    found: CallerNumber | None = await session.scalar(
        select(CallerNumber)
        .where(
            CallerNumber.tenant_id == campaign.tenant_id,
            CallerNumber.trunk_id == campaign.trunk_id,
            CallerNumber.status == "active",
        )
        .limit(1)
    )
    return found


async def _pause_if_rejected(session: AsyncSession, campaign: Campaign, now: datetime) -> None:
    since = now - timedelta(minutes=5)
    statuses = (
        await session.scalars(
            select(Call.status).where(Call.campaign_id == campaign.id, Call.start_at.is_not(None), Call.start_at >= since)
        )
    ).all()
    if len(statuses) < _REJECT_SAMPLE:
        return
    rejected = sum(1 for status in statuses if status == "rejected")
    if rejected / len(statuses) > _REJECT_RATE:
        campaign.status = "paused"


async def _place(
    session: AsyncSession,
    campaign: Campaign,
    tenant: Tenant,
    contact: Contact,
    task: Task,
    settings: Settings,
    now: datetime,
    heard: str,
    caller: CallerNumber | None,
) -> None:
    frames = [] if heard == "无人接听" else [Frame("caller", f"PCM:{heard}".encode("utf-8"))]
    script_rows = (
        await session.scalars(select(ScriptNode).where(ScriptNode.template_id == campaign.template_id).order_by(ScriptNode.node_key))
    ).all()
    script: list[dict[str, object]] = []
    for row in script_rows:
        script.append({"node_key": row.node_key, "node_type": row.node_type, "content": row.content})
    circuit = vendor_circuit("asr")
    outcome = await run_session(frames, company=tenant.name, purpose=campaign.name, script=script, circuit=circuit)
    _store_circuit_alerts(session, campaign.tenant_id, circuit)
    if heard in _REJECTED_HEARD:
        status = "rejected"
    elif heard == "无人接听" or outcome.voicemail:
        status = "no_answer"
    elif outcome.action == "transfer":
        status = "transferring"
    else:
        status = "completed"
    call_uuid = uuid.uuid4()
    call = Call(
        tenant_id=campaign.tenant_id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        call_uuid=call_uuid,
        status=status,
        caller_number=None if caller is None else caller.number,
        trunk_id=campaign.trunk_id,
        template_version=campaign.template_version,
        start_at=now,
        answer_at=now if status == "completed" else None,
        end_at=now,
        duration_sec=0 if status != "completed" else 30,
        intent_grade=outcome.intent_grade,
        compliance_hit=[{"rule": code} for code in outcome.redline_hits],
        created_by=campaign.created_by,
    )
    session.add(call)
    await session.flush()
    if status == "transferring":
        await _handoff(session, campaign, contact, task, call, now)
    elif status == "completed" and wants_reschedule(heard):
        await _callback(session, campaign, contact, task, call, now, timedelta(days=1))
    if caller is not None:
        await _touch_health(session, caller, answered=status == "completed", now=now)
    if outcome.needs_dnd:
        await on_caller_text(
            session,
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            phone_hash=contact.phone_hash,
            phone_last4=contact.phone_last4,
            text=heard,
            company=tenant.name,
            purpose=campaign.name,
            opening_played=True,
        )
    task.status = "done"
    session.add(
        CallTurn(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            turn_idx=0,
            speaker="assistant",
            text_content=outcome.reply_text,
        )
    )
    if outcome.heard:
        session.add(
            CallTurn(
                tenant_id=campaign.tenant_id,
                call_id=call.id,
                turn_idx=1,
                speaker="caller",
                text_content=outcome.heard,
            )
        )
    meta = label_meta(call_uuid, settings.aigc_producer_code)
    try:
        object_key = write_recording(tenant.id, call_uuid, outcome.turns, meta)
    except OSError:
        object_key = None
    if object_key is not None:
        session.add(
            Recording(
                tenant_id=campaign.tenant_id,
                call_id=call.id,
                object_key=object_key,
                aigc_label_meta=meta,
                explicit_label=EXPLICIT_AIGC,
                created_by=campaign.created_by,
                retain_until=now + timedelta(days=180),
            )
        )
    turns: list[dict[str, str]] = [{"speaker": "assistant", "text": outcome.reply_text}]
    if outcome.heard:
        turns.append({"speaker": "caller", "text": outcome.heard})
    review = review_call(
        assistant=outcome.reply_text,
        caller=outcome.heard,
        grade=outcome.intent_grade,
        redline_hits=outcome.redline_hits,
        transcript_present=True,
    )
    if object_key is None:
        review = mark_recording_missing(review)
        session.add(
            Alert(
                tenant_id=campaign.tenant_id,
                severity="critical",
                code="recording_missing",
                message=f"通话 {call_uuid} 已生成但录音未落盘",
            )
        )
        session.add(
            CallEvent(
                tenant_id=campaign.tenant_id,
                call_id=call.id,
                event_type="recording_missing",
                detail={"call_uuid": str(call_uuid)},
            )
        )
    session.add(
        Transcript(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            body={"explicit_label": EXPLICIT_AIGC, "turns": turns},
            summary=review.summary,
            created_by=campaign.created_by,
        )
    )
    session.add(
        QcResult(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            result=review.result,
            detail=review.detail,
        )
    )
    if review.block_script:
        await block_followups(session, campaign)
    session.add(
        CallEvent(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            event_type="ai_notice",
            detail={"played": True},
        )
    )
    rate = await session.scalar(
        select(BillingRate)
        .where((BillingRate.tenant_id == tenant.id) | (BillingRate.tenant_id.is_(None)))
        .order_by(BillingRate.tenant_id.is_(None))
        .limit(1)
    )
    session.add(
        BillingItem(
            tenant_id=tenant.id,
            call_id=call.id,
            call_uuid=call_uuid,
            amount=None if rate is None or rate.unit_price is None else rate.unit_price,
            currency="CNY",
            rate_note="unquoted" if rate is None or rate.unit_price is None else rate.note,
        )
    )
