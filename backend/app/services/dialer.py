"""Campaign dispatch. Compliance runs again immediately before each dial."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.gate_pre import CampaignView, Flags, TenantView, TrunkView, pre_dial_gate
from app.compliance.guard_incall import on_caller_text
from app.compliance.opening import opening_is_complete
from app.config import Settings
from app.models import Call, CallEvent, Campaign, Contact, Task, Tenant, Trunk
from app.models.extra import BillingItem, BillingRate, CallTurn, QcResult, Recording, Transcript
from app.services.tokens import TokenBucket
from app.voice.orchestrator import Frame, run_session

EXPLICIT_AIGC = "以下为人工智能生成合成语音"
_RETRYABLE = ("no_answer", "busy")


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
        held = await bucket.acquire(f"live:{tenant.id}", limit=campaign.max_concurrency, ttl_sec=120)
        if not held:
            break
        try:
            await _place(session, campaign, tenant, contact, task, settings, moment, heard)
        except Exception:
            task.status = "failed"
            raise
        started += 1
    for _ in range(started):
        await bucket.release(f"live:{tenant.id}")
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


async def _place(
    session: AsyncSession,
    campaign: Campaign,
    tenant: Tenant,
    contact: Contact,
    task: Task,
    settings: Settings,
    now: datetime,
    heard: str,
) -> None:
    frames = [] if heard == "无人接听" else [Frame("caller", f"PCM:{heard}".encode("utf-8"))]
    outcome = await run_session(frames, company=tenant.name, purpose=campaign.name)
    if heard == "无人接听" or outcome.voicemail:
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
        caller_number=None,
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
    producer = settings.aigc_producer_code or "UNREGISTERED"
    meta: dict[str, object] = {
        "AIGC": {"Label": "1", "ContentProducer": producer, "ProduceID": str(call_uuid)}
    }
    session.add(
        Recording(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            object_key=f"recordings/{tenant.id}/{call_uuid}.wav",
            aigc_label_meta=meta,
            explicit_label=EXPLICIT_AIGC,
            created_by=campaign.created_by,
            retain_until=now + timedelta(days=180),
        )
    )
    session.add(
        Transcript(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            body={"explicit_label": EXPLICIT_AIGC, "turns": [{"speaker": "assistant", "text": outcome.reply_text}]},
            summary=None,
            created_by=campaign.created_by,
        )
    )
    session.add(
        QcResult(
            tenant_id=campaign.tenant_id,
            call_id=call.id,
            result="pass" if opening_is_complete(outcome.reply_text) else "fail",
            detail={"ai_notice_present": opening_is_complete(outcome.reply_text), "summary": None},
        )
    )
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
