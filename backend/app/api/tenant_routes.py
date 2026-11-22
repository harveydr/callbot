"""Tenant, campaign, compliance, and billing routes."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import Depends, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.surface import (
    CampaignBody,
    CampaignPatch,
    ComplaintBody,
    ComplaintPatch,
    ConfirmBody,
    ConsentBody,
    DeleteBody,
    DndBody,
    ListBody,
    NodeBody,
    NumberBody,
    QcReview,
    TrunkBody,
    TrunkPatch,
    envelope,
    finish,
    tenant_router,
)
from app.compliance.cache import forget_dnd, forget_template, remember_consent, remember_dnd, revoke_consent_cache
from app.db import set_tenant
from app.deps import Principal, current_principal, db_session, require_roles, require_tenant
from app.errors import ApiError
from app.models import Call, CallEvent, CallerNumber, Campaign, ConsentRecord, Contact, ContactList, DndEntry, ScenarioTemplate, Task, Tenant, Trunk
from app.models.extra import BillingItem, Complaint, EvidencePack, ImportJob, QcResult, ReportExport, ScriptNode, Transcript
from app.security.crypto import PhoneInvalidError, encrypt_phone, encrypt_secret, normalize_phone, phone_hash
from app.security.sip import assert_public_sip_host
from app.services.exports import export_is_usable, export_payload, render_report
from app.voice.circuit import shared_voice_mode
from app.voice.stereo import recording_payload
from app.services.control import (
    assert_caller,
    assert_freq,
    assert_window,
    billing_summary,
    build_evidence,
    bump,
    accept_import,
    run_import_job,
    job_payload,
    load_turns,
    open_complaint,
    progress_complaint,
    overview,
    owned,
    paging,
    reject_forbidden,
    require_password,
    scenarios_ok,
    start_campaign,
    utcnow,
)
from app.services.dialer import schedule_retries

_VOICE_LABEL = {"normal": "正常", "script": "话术树模式", "paused_vendor": "语音合成暂停"}


def _voice_mode(request: Request) -> str:
    mode = shared_voice_mode(request.app.state.settings.redis_url)
    return _VOICE_LABEL.get(mode, "正常")


@tenant_router.post("/trunks")
async def create_trunk(
    body: TrunkBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise ApiError(404, "NOT_FOUND", "tenant not found")
    assert_public_sip_host(body.sip_host)
    if body.mode not in {"byoc", "managed"} or body.sip_transport not in {"udp", "tcp", "tls"}:
        raise ApiError(422, "VALIDATION", "trunk mode or transport is invalid")
    if body.max_concurrency > tenant.max_concurrency:
        raise ApiError(422, "VALIDATION", "trunk concurrency exceeds the tenant quota")
    scenarios_ok(body.allowed_scenarios, list(tenant.allowed_scenarios))
    if body.mode == "managed" and "telesales" in body.allowed_scenarios:
        raise ApiError(422, "COMPLIANCE_SCENARIO_NOT_ALLOWED", "managed trunks cannot carry telesales")
    secret = encrypt_secret(body.auth_secret, request.app.state.settings.field_enc_key) if body.auth_secret else None
    trunk = Trunk(
        tenant_id=tenant_id,
        name=body.name,
        mode=body.mode,
        provider=body.provider,
        sip_host=body.sip_host,
        sip_port=body.sip_port,
        sip_transport=body.sip_transport,
        auth_user=body.auth_user,
        auth_credential_enc=secret,
        max_concurrency=body.max_concurrency,
        allowed_scenarios=body.allowed_scenarios,
        status="inactive",
    )
    session.add(trunk)
    await session.flush()
    return envelope(request.state.trace_id, {"id": str(trunk.id), "status": trunk.status, "version": trunk.version})


@tenant_router.get("/trunks")
async def list_trunks(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (
        await session.scalars(
            select(Trunk).where(Trunk.tenant_id == tenant_id).order_by(Trunk.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return envelope(
        request.state.trace_id,
        {"items": [{"id": str(row.id), "name": row.name, "mode": row.mode, "status": row.status, "version": row.version} for row in rows]},
    )


@tenant_router.patch("/trunks/{trunk_id}")
async def patch_trunk(
    trunk_id: uuid.UUID,
    body: TrunkPatch,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    trunk = await owned(session, Trunk, trunk_id, require_tenant(principal))
    bump(trunk, body.version)
    if body.max_concurrency is not None:
        trunk.max_concurrency = body.max_concurrency
    if body.status is not None:
        if body.status not in {"inactive", "active", "blocked"}:
            raise ApiError(422, "VALIDATION", "status is invalid")
        trunk.status = body.status
    return envelope(request.state.trace_id, {"id": str(trunk.id), "version": trunk.version, "status": trunk.status})


@tenant_router.post("/trunks/{trunk_id}/test-call")
async def test_call(
    trunk_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    trunk = await owned(session, Trunk, trunk_id, require_tenant(principal))
    return envelope(
        request.state.trace_id,
        {"trunk_id": str(trunk.id), "reachable": False, "mode": "mock", "reason": "voice_gateway_not_configured"},
    )


@tenant_router.post("/caller-numbers")
async def create_number(
    body: NumberBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    trunk = await owned(session, Trunk, body.trunk_id, tenant_id)
    number = assert_caller(body.number)
    row = CallerNumber(tenant_id=tenant_id, trunk_id=trunk.id, number=number, region=body.region, status="active")
    session.add(row)
    await session.flush()
    return envelope(request.state.trace_id, {"id": str(row.id), "number": number, "status": row.status})


@tenant_router.get("/caller-numbers")
async def list_numbers(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (
        await session.scalars(
            select(CallerNumber).where(CallerNumber.tenant_id == tenant_id).order_by(CallerNumber.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "number": row.number, "region": row.region, "status": row.status} for row in rows]})


@tenant_router.post("/lists")
async def create_list(
    body: ListBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise ApiError(404, "NOT_FOUND", "tenant not found")
    scenarios_ok([body.scenario_code], list(tenant.allowed_scenarios))
    row = ContactList(tenant_id=tenant_id, name=body.name, scenario_code=body.scenario_code, import_status="idle")
    session.add(row)
    await session.flush()
    return envelope(request.state.trace_id, {"id": str(row.id)})


@tenant_router.post("/lists/{list_id}/import")
async def import_list(
    list_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> JSONResponse:
    contact_list = await owned(session, ContactList, list_id, require_tenant(principal))
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise ApiError(400, "BAD_REQUEST", "file is larger than 50MB")
    job = await accept_import(session, tenant_id=contact_list.tenant_id, contact_list=contact_list, content=content)
    if job.status != "done" and request.app.state.settings.celery_enqueue:
        await session.commit()
        await set_tenant(session, contact_list.tenant_id)
        from app.tasks.imports import import_contacts_task

        try:
            import_contacts_task.delay(str(job.id))
        except Exception as exc:
            job.status = "failed"
            contact_list.import_status = "failed"
            raise ApiError(503, "UNAVAILABLE", "import queue is unavailable") from exc
    elif job.status != "done":
        job = await run_import_job(session, job.id, request.app.state.settings)
    return JSONResponse(status_code=202, content=envelope(request.state.trace_id, job_payload(job)))


@tenant_router.get("/imports/{task_id}")
async def import_status(
    task_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    job = await session.scalar(select(ImportJob).where(ImportJob.id == task_id, ImportJob.tenant_id == require_tenant(principal)))
    if job is None:
        raise ApiError(404, "NOT_FOUND", "import not found")
    return envelope(request.state.trace_id, job_payload(job))


@tenant_router.get("/contacts")
async def list_contacts(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (
        await session.scalars(
            select(Contact)
            .where(Contact.tenant_id == tenant_id, Contact.deleted_at.is_(None))
            .order_by(Contact.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return envelope(
        request.state.trace_id,
        {
            "items": [
                {
                    "id": str(row.id),
                    "phone_last4": row.phone_last4,
                    "dial_class": row.dial_class,
                    "display_name": row.display_name,
                    "minor": row.minor,
                }
                for row in rows
            ]
        },
    )


@tenant_router.delete("/contacts")
async def delete_contacts(
    body: DeleteBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    if not body.confirm:
        raise ApiError(400, "BAD_REQUEST", "deletion requires confirmation")
    await require_password(session, principal.user_id, body.password)
    tenant_id = require_tenant(principal)
    rows = (await session.scalars(select(Contact).where(Contact.tenant_id == tenant_id, Contact.id.in_(body.ids)))).all()
    for row in rows:
        row.deleted_at = utcnow()
    return envelope(request.state.trace_id, {"deleted": len(rows)})


@tenant_router.post("/consents")
async def create_consent(
    body: ConsentBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    if body.consented_at.tzinfo is None:
        raise ApiError(422, "VALIDATION", "consented_at must be timezone-aware")
    try:
        normalized = normalize_phone(body.phone)
    except PhoneInvalidError as exc:
        raise ApiError(422, "VALIDATION", "phone_invalid") from exc
    row = ConsentRecord(
        tenant_id=tenant_id,
        phone_enc=encrypt_phone(normalized, request.app.state.settings.field_enc_key),
        phone_last4=normalized[-4:],
        phone_hash=phone_hash(normalized, request.app.state.settings.phone_pepper),
        scenario_code=body.scenario_code,
        source=body.source,
        proof_key=body.proof_key,
        consented_at=body.consented_at,
        revoked=False,
    )
    session.add(row)
    await session.flush()
    await remember_consent(
        request.app.state.redis,
        tenant_id=tenant_id,
        phone_hash=row.phone_hash,
        scenario=row.scenario_code,
        consented_at=row.consented_at,
        expires_at=row.expires_at,
    )
    return envelope(request.state.trace_id, {"id": str(row.id), "phone_last4": row.phone_last4})


@tenant_router.get("/consents")
async def list_consents(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (
        await session.scalars(
            select(ConsentRecord).where(ConsentRecord.tenant_id == tenant_id).order_by(ConsentRecord.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return envelope(
        request.state.trace_id,
        {"items": [{"id": str(row.id), "phone_last4": row.phone_last4, "scenario_code": row.scenario_code, "revoked": row.revoked} for row in rows]},
    )


@tenant_router.post("/consents/{consent_id}/revoke")
async def revoke_consent(
    consent_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    row = await session.scalar(select(ConsentRecord).where(ConsentRecord.id == consent_id, ConsentRecord.tenant_id == require_tenant(principal)))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "consent not found")
    row.revoked = True
    row.revoked_at = utcnow()
    await revoke_consent_cache(
        request.app.state.redis,
        tenant_id=row.tenant_id,
        phone_hash=row.phone_hash,
        scenario=row.scenario_code,
    )
    return envelope(request.state.trace_id, {"id": str(row.id), "revoked": True})


@tenant_router.post("/campaigns")
async def create_campaign(
    body: CampaignBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)

    async def produce() -> tuple[int, dict[str, object]]:
        reject_forbidden(body.model_dump())
        if body.start_at.tzinfo is None or body.end_at.tzinfo is None or body.end_at <= body.start_at:
            raise ApiError(422, "VALIDATION", "start_at and end_at must be aware and ordered")
        assert_window(body.call_window)
        assert_freq(body.freq_per_phone_daily, body.freq_per_phone_7d, body.freq_per_phone_30d)
        tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        template = await session.scalar(select(ScenarioTemplate).where(ScenarioTemplate.id == body.template_id))
        contact_list = await owned(session, ContactList, body.list_id, tenant_id)
        trunk = await owned(session, Trunk, body.trunk_id, tenant_id)
        if tenant is None or template is None:
            raise ApiError(404, "NOT_FOUND", "template or tenant missing")
        if template.tenant_id not in {None, tenant_id} or template.status != "approved":
            raise ApiError(403, "FORBIDDEN", "template is not usable")
        if template.scenario_code != body.scenario_code or contact_list.scenario_code != body.scenario_code:
            raise ApiError(422, "VALIDATION", "scenario does not match the template and list")
        scenarios_ok([body.scenario_code], list(tenant.allowed_scenarios))
        if body.max_concurrency > tenant.max_concurrency:
            raise ApiError(422, "VALIDATION", "concurrency exceeds the tenant quota")
        policy = body.retry_policy or {"max_retry": 2, "interval_min": [60, 1440]}
        reject_forbidden(policy)
        max_retry = policy.get("max_retry", 0)
        if not isinstance(max_retry, int) or not 0 <= max_retry <= 3:
            raise ApiError(422, "VALIDATION", "max_retry must be 0..3")
        campaign = Campaign(
            tenant_id=tenant_id,
            name=body.name,
            scenario_code=body.scenario_code,
            template_id=template.id,
            template_version=template.version,
            list_id=contact_list.id,
            status="draft",
            start_at=body.start_at,
            end_at=body.end_at,
            call_window=body.call_window,
            timezone=tenant.timezone,
            max_concurrency=body.max_concurrency,
            freq_per_phone_daily=body.freq_per_phone_daily,
            freq_per_phone_7d=body.freq_per_phone_7d,
            freq_per_phone_30d=body.freq_per_phone_30d,
            retry_policy=policy,
            trunk_id=trunk.id,
            created_by=principal.user_id,
        )
        session.add(campaign)
        await session.flush()
        return 200, {"id": str(campaign.id), "status": campaign.status, "version": campaign.version, "template_version": campaign.template_version}

    _status, data = await finish(session, principal, idempotency_key, "POST", "/api/t/campaigns", produce)
    return envelope(request.state.trace_id, data)


@tenant_router.get("/campaigns")
async def list_campaigns(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (
        await session.scalars(
            select(Campaign).where(Campaign.tenant_id == tenant_id).order_by(Campaign.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    voice_mode = _voice_mode(request)
    return envelope(
        request.state.trace_id,
        {
            "items": [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "status": row.status,
                    "version": row.version,
                    "voice_mode": voice_mode,
                }
                for row in rows
            ]
        },
    )


@tenant_router.get("/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    campaign = await owned(session, Campaign, campaign_id, require_tenant(principal))
    tasks = int(await session.scalar(select(func.count()).select_from(Task).where(Task.campaign_id == campaign.id)) or 0)
    return envelope(request.state.trace_id, {"id": str(campaign.id), "name": campaign.name, "status": campaign.status, "version": campaign.version, "template_version": campaign.template_version, "tasks": tasks})


@tenant_router.patch("/campaigns/{campaign_id}")
async def patch_campaign(
    campaign_id: uuid.UUID,
    body: CampaignPatch,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    campaign = await owned(session, Campaign, campaign_id, require_tenant(principal))
    bump(campaign, body.version)
    if body.name is not None:
        campaign.name = body.name
    if body.call_window is not None:
        assert_window(body.call_window)
        campaign.call_window = body.call_window
    if body.max_concurrency is not None:
        campaign.max_concurrency = body.max_concurrency
    if body.status is not None:
        if principal.role != "tenant_admin" or campaign.status != "pending_review" or body.status != "approved":
            raise ApiError(403, "FORBIDDEN", "status transition is not allowed")
        campaign.status = "approved"
    return envelope(request.state.trace_id, {"id": str(campaign.id), "version": campaign.version, "status": campaign.status})


@tenant_router.post("/campaigns/{campaign_id}/submit-review")
async def submit_campaign(
    campaign_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    campaign = await owned(session, Campaign, campaign_id, require_tenant(principal))
    if campaign.status != "draft":
        raise ApiError(409, "CONFLICT", "only a draft can be submitted")
    campaign.status = "pending_review"
    campaign.version += 1
    return envelope(request.state.trace_id, {"id": str(campaign.id), "status": campaign.status, "version": campaign.version})


@tenant_router.post("/campaigns/{campaign_id}/start")
async def start_route(
    campaign_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    campaign = await owned(session, Campaign, campaign_id, require_tenant(principal))

    async def produce() -> tuple[int, dict[str, object]]:
        clock = getattr(request.app.state, "now", None)
        settings = request.app.state.settings
        data = await start_campaign(
            session,
            campaign=campaign,
            settings=settings,
            redis=request.app.state.redis,
            user_id=principal.user_id,
            trace_id=request.state.trace_id,
            now=clock() if clock is not None else None,
            place_calls=not settings.celery_enqueue,
        )
        if settings.celery_enqueue and not data["idempotent"]:
            await session.commit()
            await set_tenant(session, campaign.tenant_id)
            from app.tasks.dispatch import dispatch_campaign

            try:
                queued = dispatch_campaign.delay(str(campaign.id), str(campaign.tenant_id))
            except Exception as exc:
                campaign.status = "approved"
                await session.commit()
                await set_tenant(session, campaign.tenant_id)
                raise ApiError(503, "UNAVAILABLE", "dispatch queue is unavailable") from exc
            data["task_id"] = str(queued.id)
        return 202, data

    status, data = await finish(session, principal, idempotency_key, "POST", f"/api/t/campaigns/{campaign_id}/start", produce)
    return JSONResponse(status_code=status, content=envelope(request.state.trace_id, data))


@tenant_router.post("/campaigns/{campaign_id}/pause")
async def pause_route(
    campaign_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    campaign = await owned(session, Campaign, campaign_id, require_tenant(principal))

    async def produce() -> tuple[int, dict[str, object]]:
        if campaign.status != "running":
            raise ApiError(409, "CONFLICT", "only a running campaign can pause")
        campaign.status = "paused"
        campaign.version += 1
        started = await schedule_retries(session, campaign, utcnow())
        return 200, {"id": str(campaign.id), "status": campaign.status, "retries": started}

    _status, data = await finish(session, principal, idempotency_key, "POST", f"/api/t/campaigns/{campaign_id}/pause", produce)
    return envelope(request.state.trace_id, data)


@tenant_router.post("/campaigns/{campaign_id}/abort")
async def abort_route(
    campaign_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    campaign = await owned(session, Campaign, campaign_id, require_tenant(principal))

    async def produce() -> tuple[int, dict[str, object]]:
        if campaign.status not in {"running", "paused", "approved"}:
            raise ApiError(409, "CONFLICT", "campaign cannot be aborted")
        campaign.status = "aborted"
        campaign.version += 1
        return 200, {"id": str(campaign.id), "status": campaign.status}

    _status, data = await finish(session, principal, idempotency_key, "POST", f"/api/t/campaigns/{campaign_id}/abort", produce)
    return envelope(request.state.trace_id, data)


@tenant_router.get("/campaigns/{campaign_id}/stats")
async def campaign_stats(
    campaign_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor", "agent")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    campaign = await owned(session, Campaign, campaign_id, require_tenant(principal))
    rows = (await session.execute(select(Call.status, func.count()).where(Call.campaign_id == campaign.id).group_by(Call.status))).all()
    return envelope(request.state.trace_id, {"campaign_id": str(campaign.id), "by_status": {status: count for status, count in rows}})


@tenant_router.get("/calls")
async def list_calls(
    request: Request,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    if principal.role not in {"tenant_admin", "operator", "auditor", "agent"}:
        raise ApiError(403, "FORBIDDEN", "role is not allowed")
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    stmt = select(Call).where(Call.tenant_id == tenant_id)
    if principal.role == "operator":
        stmt = stmt.where(Call.created_by == principal.user_id)
    rows = (await session.scalars(stmt.order_by(Call.created_at.desc()).limit(limit).offset(offset))).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "status": row.status, "block_reason": row.block_reason, "template_version": row.template_version} for row in rows]})


@tenant_router.get("/calls/{call_id}")
async def get_call(
    call_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor", "agent")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    call = await owned(session, Call, call_id, require_tenant(principal))
    if principal.role == "operator" and call.created_by != principal.user_id:
        raise ApiError(403, "FORBIDDEN", "operator can only read calls they started")
    turns = await load_turns(session, call.id)
    transcript_row = await session.scalar(select(Transcript).where(Transcript.call_id == call.id))
    summary = None if transcript_row is None else transcript_row.summary
    return envelope(
        request.state.trace_id,
        {
            "id": str(call.id),
            "status": call.status,
            "template_version": call.template_version,
            "turns": turns,
            "summary": call.summary if call.summary is not None else summary,
            "duration_sec": call.duration_sec,
            "ai_duration_sec": call.ai_duration_sec,
            "human_duration_sec": call.human_duration_sec,
            "turn_count": call.turn_count,
            "barge_in_count": call.barge_in_count,
            "latency_p50_ms": call.latency_p50_ms,
            "latency_p95_ms": call.latency_p95_ms,
            "cdr": call.cdr,
        },
    )


@tenant_router.get("/calls/{call_id}/transcript")
async def transcript(
    call_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    call = await owned(session, Call, call_id, require_tenant(principal))
    row = await session.scalar(select(Transcript).where(Transcript.call_id == call.id))
    recording = recording_payload(call.tenant_id, call.call_uuid)
    if row is None:
        return envelope(
            request.state.trace_id,
            {"summary": None, "body": None, "note": "AI 小结生成中", "recording": recording},
        )
    return envelope(
        request.state.trace_id,
        {
            "summary": row.summary,
            "body": row.body,
            "note": "AI 小结生成中" if row.summary is None else "ok",
            "recording": recording,
        },
    )


@tenant_router.get("/qc-results")
async def list_qc(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (await session.scalars(select(QcResult).where(QcResult.tenant_id == tenant_id).order_by(QcResult.created_at.desc()).limit(limit).offset(offset))).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "call_id": str(row.call_id), "result": row.result} for row in rows]})


@tenant_router.post("/qc-results/{qc_id}/review")
async def review_qc(
    qc_id: uuid.UUID,
    body: QcReview,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    if body.result not in {"pass", "warn", "fail"}:
        raise ApiError(422, "VALIDATION", "result is invalid")
    if principal.role == "auditor":
        raise ApiError(403, "FORBIDDEN", "auditor is read only")
    row = await session.scalar(select(QcResult).where(QcResult.id == qc_id, QcResult.tenant_id == require_tenant(principal)))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "qc result not found")
    row.result = body.result
    row.reviewed_by = principal.user_id
    row.reviewed_at = utcnow()
    return envelope(request.state.trace_id, {"id": str(row.id), "result": row.result})


@tenant_router.get("/compliance/events")
async def compliance_events(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "auditor", "operator")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (
        await session.scalars(
            select(CallEvent).where(CallEvent.tenant_id == tenant_id).order_by(CallEvent.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "event_type": row.event_type, "detail": row.detail} for row in rows]})


@tenant_router.get("/dnd")
async def list_dnd(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (await session.scalars(select(DndEntry).where(DndEntry.tenant_id == tenant_id).order_by(DndEntry.created_at.desc()).limit(limit).offset(offset))).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "phone_last4": row.phone_last4, "source": row.source} for row in rows]})


@tenant_router.post("/dnd")
async def add_dnd(
    body: DndBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    if body.source != "operator_manual":
        raise ApiError(403, "FORBIDDEN", "only a manual tenant entry can be added here")
    tenant_id = require_tenant(principal)
    try:
        normalized = normalize_phone(body.phone)
    except PhoneInvalidError as exc:
        raise ApiError(422, "VALIDATION", "phone_invalid") from exc
    row = DndEntry(
        tenant_id=tenant_id,
        phone_hash=phone_hash(normalized, request.app.state.settings.phone_pepper),
        phone_last4=normalized[-4:],
        source="operator_manual",
        scope="tenant",
        permanent=True,
    )
    session.add(row)
    await session.flush()
    await remember_dnd(request.app.state.redis, tenant_id=tenant_id, phone_hash=row.phone_hash)
    return envelope(request.state.trace_id, {"id": str(row.id), "phone_last4": row.phone_last4})


@tenant_router.delete("/dnd")
async def delete_dnd(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
    entry_id: uuid.UUID | None = None,
) -> dict[str, object]:
    if entry_id is None:
        raise ApiError(400, "BAD_REQUEST", "entry_id is required")
    row = await session.scalar(select(DndEntry).where(DndEntry.id == entry_id, DndEntry.tenant_id == require_tenant(principal)))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "dnd entry not found")
    if row.source != "operator_manual":
        raise ApiError(403, "FORBIDDEN", "this entry can only be removed by dual-controlled platform review")
    phone_hash = row.phone_hash
    tenant_id = row.tenant_id
    await session.delete(row)
    if tenant_id is not None:
        await forget_dnd(request.app.state.redis, tenant_id=tenant_id, phone_hash=phone_hash)
    return envelope(request.state.trace_id, {"deleted": True})


@tenant_router.post("/complaints")
async def create_complaint(
    body: ComplaintBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "agent")),
    session: AsyncSession = Depends(db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)

    async def produce() -> tuple[int, dict[str, object]]:
        data = await open_complaint(
            session,
            tenant_id=tenant_id,
            phone=body.phone,
            detail=body.detail,
            settings=request.app.state.settings,
            user_id=principal.user_id,
            trace_id=request.state.trace_id,
            call_id=body.call_id,
            redis=request.app.state.redis,
        )
        return 200, data

    _status, data = await finish(session, principal, idempotency_key, "POST", "/api/t/complaints", produce)
    return envelope(request.state.trace_id, data)


@tenant_router.patch("/complaints/{complaint_id}")
async def patch_complaint(
    complaint_id: uuid.UUID,
    body: ComplaintPatch,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "agent")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    row = await owned(session, Complaint, complaint_id, require_tenant(principal))
    if row.version != body.version:
        raise ApiError(409, "VERSION_CONFLICT", "已被他人修改，请刷新")
    data = progress_complaint(
        row,
        status=body.status,
        disposition=body.disposition,
        now=datetime.now(timezone.utc),
    )
    return envelope(request.state.trace_id, data)


@tenant_router.post("/audit/evidence-pack")
async def evidence_route(
    body: ConfirmBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    if not body.confirm:
        raise ApiError(400, "BAD_REQUEST", "evidence export requires confirmation")
    await require_password(session, principal.user_id, body.password)
    pack = await build_evidence(session, tenant_id=require_tenant(principal), user_id=principal.user_id, trace_id=request.state.trace_id)
    return envelope(request.state.trace_id, {"token": pack.token, "expires_at": pack.expires_at.isoformat()})


@tenant_router.get("/audit/evidence-pack/{token}")
async def evidence_download(
    token: str,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    pack = await session.scalar(select(EvidencePack).where(EvidencePack.token == token, EvidencePack.tenant_id == require_tenant(principal)))
    if pack is None:
        raise ApiError(404, "NOT_FOUND", "evidence pack not found")
    now = utcnow()
    if pack.used_at is not None or pack.expires_at <= now:
        raise ApiError(410, "GONE", "evidence link is expired or already used")
    pack.used_at = now
    return envelope(request.state.trace_id, pack.payload)


@tenant_router.get("/billing/summary")
async def summary_route(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    return envelope(request.state.trace_id, await billing_summary(session, require_tenant(principal)))


@tenant_router.get("/billing/items")
async def billing_items(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "auditor")),
    session: AsyncSession = Depends(db_session),
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    limit, offset = paging(page, page_size)
    rows = (await session.scalars(select(BillingItem).where(BillingItem.tenant_id == tenant_id).order_by(BillingItem.created_at.desc()).limit(limit).offset(offset))).all()
    return envelope(
        request.state.trace_id,
        {"items": [{"id": str(row.id), "call_uuid": str(row.call_uuid), "amount": None if row.amount is None else format(row.amount, "f"), "rate_note": row.rate_note} for row in rows]},
    )


@tenant_router.get("/reports/overview")
async def overview_route(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor", "agent")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    data = await overview(session, require_tenant(principal))
    data["degraded"] = not request.app.state.settings.calling_enabled
    data["voice_mode"] = _voice_mode(request)
    return envelope(request.state.trace_id, data)


@tenant_router.get("/reports/export", response_model=None)
async def export_route(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin")),
    session: AsyncSession = Depends(db_session),
    task_id: uuid.UUID | None = None,
    download: int = 0,
    token: str = "",
) -> dict[str, object] | Response:
    tenant_id = require_tenant(principal)
    if task_id is not None:
        row = await session.scalar(select(ReportExport).where(ReportExport.id == task_id, ReportExport.tenant_id == tenant_id))
        if row is None:
            raise ApiError(404, "NOT_FOUND", "export not found")
        if download:
            if row.status != "done" or not row.download_token or row.expires_at is None:
                raise ApiError(409, "CONFLICT", "export is not ready")
            if token != row.download_token:
                raise ApiError(403, "FORBIDDEN", "export token rejected")
            if not export_is_usable(expires_at=row.expires_at, now=utcnow()):
                raise ApiError(410, "GONE", "export link is expired")
            filename = f"report-{row.id}.csv"
            return Response(
                content=row.body or "",
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        return envelope(request.state.trace_id, export_payload(row, utcnow()))
    row = ReportExport(id=uuid.uuid4(), tenant_id=tenant_id, status="running")
    session.add(row)
    await session.flush()
    if request.app.state.settings.celery_enqueue:
        await session.commit()
        await set_tenant(session, tenant_id)
        from app.tasks.reports import render_report_task

        try:
            render_report_task.delay(str(row.id))
        except Exception as exc:
            row.status = "failed"
            raise ApiError(503, "UNAVAILABLE", "report queue is unavailable") from exc
    else:
        row = await render_report(session, row.id, utcnow())
    return envelope(request.state.trace_id, export_payload(row, utcnow()))


@tenant_router.get("/ws/tickets")
async def ws_ticket(
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, object]:
    ticket = secrets.token_urlsafe(24)
    await request.app.state.redis.set(f"ws:{ticket}", str(principal.user_id), ex=60)
    return envelope(request.state.trace_id, {"ticket": ticket, "expires_in": 60})


@tenant_router.get("/templates")
async def list_templates(
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    rows = (
        await session.scalars(
            select(ScenarioTemplate).where((ScenarioTemplate.tenant_id == tenant_id) | (ScenarioTemplate.tenant_id.is_(None)))
        )
    ).all()
    return envelope(request.state.trace_id, {"items": [{"id": str(row.id), "name": row.name, "scenario_code": row.scenario_code, "status": row.status, "version": row.version} for row in rows]})


@tenant_router.get("/templates/{template_id}")
async def get_template(
    template_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator", "auditor")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    row = await session.scalar(select(ScenarioTemplate).where(ScenarioTemplate.id == template_id))
    if row is None or row.tenant_id not in {None, tenant_id}:
        raise ApiError(404, "NOT_FOUND", "template not found")
    nodes = (await session.scalars(select(ScriptNode).where(ScriptNode.template_id == row.id).order_by(ScriptNode.node_key))).all()
    return envelope(
        request.state.trace_id,
        {"id": str(row.id), "name": row.name, "status": row.status, "version": row.version, "nodes": [{"node_key": node.node_key, "node_type": node.node_type, "content": node.content} for node in nodes]},
    )


@tenant_router.put("/templates/{template_id}/nodes")
async def save_nodes(
    template_id: uuid.UUID,
    body: NodeBody,
    request: Request,
    principal: Principal = Depends(require_roles("tenant_admin", "operator")),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    tenant_id = require_tenant(principal)
    row = await session.scalar(select(ScenarioTemplate).where(ScenarioTemplate.id == template_id, ScenarioTemplate.tenant_id == tenant_id))
    if row is None:
        raise ApiError(403, "FORBIDDEN", "platform templates are read only")
    reject_forbidden(body.nodes)
    existing = (await session.scalars(select(ScriptNode).where(ScriptNode.template_id == row.id))).all()
    for node in existing:
        await session.delete(node)
    for item in body.nodes:
        node_key = str(item.get("node_key") or "")
        node_type = str(item.get("node_type") or "")
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        if not node_key or node_type not in {"mandatory", "llm_free", "gate"}:
            raise ApiError(422, "VALIDATION", "node is invalid")
        session.add(ScriptNode(template_id=row.id, tenant_id=tenant_id, node_key=node_key, node_type=node_type, content=content))
    row.version += 1
    row.status = "draft"
    row.updated_at = utcnow()
    await forget_template(request.app.state.redis, row.id)
    return envelope(request.state.trace_id, {"id": str(row.id), "version": row.version, "status": row.status})
