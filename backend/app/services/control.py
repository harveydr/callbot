"""Control-plane use cases shared by the HTTP routes."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import secrets
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TypeVar

import redis.asyncio as redis_async
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import set_bypass, set_tenant
from app.errors import ApiError
from app.models import (
    AuditLog,
    Call,
    CallerNumber,
    Campaign,
    ConsentRecord,
    Contact,
    ContactList,
    DndEntry,
    PlatformDnd,
    ScenarioTemplate,
    Task,
    Tenant,
    Trunk,
    User,
)
from app.models.extra import (
    Alert,
    BillingItem,
    CallTurn,
    CircuitState,
    Complaint,
    EvidencePack,
    IdempotencyKey,
    ImportJob,
    QcResult,
    ReportExport,
    ScriptNode,
    Transcript,
    UserRole,
)
from app.security.crypto import PhoneInvalidError, encrypt_phone, mask_phone, normalize_phone, phone_hash
from app.security.jwt_codec import REFRESH_TTL, decode, issue
from app.security.passwords import hash_password, verify_password
from app.services.billing_budget import apply_billing_budget, month_spend
from app.services.dialer import dial_pending, ensure_initial_tasks, schedule_retries

_WINDOW = __import__("re").compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
_VIRTUAL = ("170", "171", "165", "167", "162")
_FORBIDDEN = {"skip_compliance", "skip_opening", "disable_recording", "spoof_caller", "random_caller", "virtual_number"}
_SCENARIOS = {"notification", "callback_survey", "telesales", "collection"}
_DUMMY: str | None = None
ModelT = TypeVar("ModelT", Call, Campaign, Trunk, ContactList, Complaint)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password("dummy-password-not-used")
    return _DUMMY


def public_id(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def assert_window(value: str) -> None:
    if _WINDOW.fullmatch(value) is None:
        raise ApiError(422, "VALIDATION", "call_window is invalid")
    start, end = value.split("-")
    if start < "09:00" or end > "19:00" or start >= end:
        raise ApiError(422, "COMPLIANCE_WINDOW_CLOSED", "call window can only be tightened inside 09:00-19:00")


def assert_freq(daily: int, week: int, month: int) -> None:
    if not 1 <= daily <= 1 or not 1 <= week <= 3 or not 1 <= month <= 6:
        raise ApiError(422, "VALIDATION", "frequency caps can only be tightened")


def assert_caller(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    if digits.startswith("86") and len(digits) > 11:
        digits = digits[2:]
    if len(digits) < 5 or len(digits) > 15:
        raise ApiError(422, "VALIDATION", "caller number must be a registered number")
    if digits.startswith(_VIRTUAL):
        raise ApiError(422, "VALIDATION", "virtual numbers cannot place calls")
    return digits


def reject_forbidden(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key) in _FORBIDDEN:
                raise ApiError(400, "BAD_REQUEST", "that option cannot be configured")
            reject_forbidden(value)
    elif isinstance(payload, list):
        for item in payload:
            reject_forbidden(item)


async def audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    action: str,
    target_type: str | None,
    target_id: str | None,
    detail: dict[str, object],
    trace_id: str,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            trace_id=trace_id,
        )
    )


async def owned(session: AsyncSession, model: type[ModelT], object_id: uuid.UUID, tenant_id: uuid.UUID) -> ModelT:
    row = await session.scalar(select(model).where(model.id == object_id))
    if row is not None:
        return row
    await set_bypass(session)
    other = await session.scalar(select(model).where(model.id == object_id))
    if other is not None:
        other_tenant = other.tenant_id
        session.expunge(other)
        await set_tenant(session, tenant_id)
        if other_tenant != tenant_id:
            raise ApiError(403, "FORBIDDEN", "cross-tenant access")
    await set_tenant(session, tenant_id)
    raise ApiError(404, "NOT_FOUND", "resource not found")


def paging(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page_size not in {20, 50, 100}:
        raise ApiError(400, "BAD_REQUEST", "page_size must be 20, 50, or 100")
    return page_size, (page - 1) * page_size


def bump(row: Campaign | Trunk | Tenant, expected: int) -> None:
    if row.version != expected:
        raise ApiError(409, "VERSION_CONFLICT", "已被他人修改，请刷新")
    row.version += 1


async def replay(session: AsyncSession, user_id: uuid.UUID, key: str) -> dict[str, object] | None:
    if not key:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required")
    found = await session.scalar(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.idem_key == key)
    )
    if found is None:
        return None
    return {"status": found.status_code, "body": found.body}


async def remember(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    key: str,
    method: str,
    path: str,
    status: int,
    body: dict[str, object],
) -> None:
    session.add(
        IdempotencyKey(
            user_id=user_id,
            tenant_id=tenant_id,
            idem_key=key,
            method=method,
            path=path,
            status_code=status,
            body=body,
        )
    )


async def login(session: AsyncSession, redis: redis_async.Redis, settings: Settings, username: str, password: str) -> dict[str, object]:
    await set_bypass(session)
    user = await session.scalar(select(User).where(User.username == username))
    now = utcnow()
    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise ApiError(423, "ACCOUNT_LOCKED", "账号已锁定", {"unlock_at": user.locked_until.isoformat()})
    verified = verify_password(user.password_hash, password) if user is not None else verify_password(_dummy_hash(), password)
    if user is None or not verified or user.status == "disabled":
        if user is not None and user.status != "disabled":
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.locked_until = now + timedelta(minutes=15)
                user.status = "locked"
            await session.commit()
            locked_until = user.locked_until
            if user.failed_login_count >= 5 and locked_until is not None:
                raise ApiError(423, "ACCOUNT_LOCKED", "账号已锁定", {"unlock_at": locked_until.isoformat()})
        raise ApiError(401, "BAD_CREDENTIALS", "账号或密码错误")
    user.failed_login_count = 0
    user.locked_until = None
    if user.status == "locked":
        user.status = "active"
    tokens = issue(
        settings.jwt_secret,
        user_id=user.id,
        role=user.role,
        tenant_id=user.tenant_id,
        must_change=user.must_change_password,
    )
    await redis.set(f"refresh:{tokens['refresh_jti']}", str(user.id), ex=REFRESH_TTL)
    await audit(session, tenant_id=user.tenant_id, user_id=user.id, action="login", target_type="user", target_id=str(user.id), detail={}, trace_id="")
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_in": tokens["expires_in"],
        "must_change_password": user.must_change_password,
        "role": user.role,
        "tenant_id": public_id(user.tenant_id),
    }


async def refresh_session(session: AsyncSession, redis: redis_async.Redis, settings: Settings, refresh_token: str) -> dict[str, object]:
    try:
        claims = decode(refresh_token, settings.jwt_secret, expected_typ="refresh")
    except Exception as exc:
        raise ApiError(401, "UNAUTHORIZED", "invalid refresh token") from exc
    jti = str(claims.get("jti"))
    stored = await redis.get(f"refresh:{jti}")
    if stored is None:
        raise ApiError(401, "UNAUTHORIZED", "refresh token reuse")
    await redis.delete(f"refresh:{jti}")
    await set_bypass(session)
    user = await session.scalar(select(User).where(User.id == uuid.UUID(str(claims.get("sub")))))
    if user is None or user.status == "disabled":
        raise ApiError(401, "UNAUTHORIZED", "invalid refresh token")
    tokens = issue(settings.jwt_secret, user_id=user.id, role=user.role, tenant_id=user.tenant_id, must_change=user.must_change_password)
    await redis.set(f"refresh:{tokens['refresh_jti']}", str(user.id), ex=REFRESH_TTL)
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_in": tokens["expires_in"],
        "must_change_password": user.must_change_password,
        "role": user.role,
        "tenant_id": public_id(user.tenant_id),
    }


async def change_password(session: AsyncSession, user_id: uuid.UUID, current: str, new: str) -> None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or not verify_password(user.password_hash, current):
        raise ApiError(401, "BAD_CREDENTIALS", "账号或密码错误")
    if new == current:
        raise ApiError(422, "VALIDATION", "choose a different password")
    user.password_hash = hash_password(new)
    user.must_change_password = False


async def create_tenant(session: AsyncSession, *, name: str, code: str, trace_id: str, user_id: uuid.UUID) -> dict[str, object]:
    existing = await session.scalar(select(Tenant).where(Tenant.code == code))
    if existing is not None:
        raise ApiError(409, "CONFLICT", "tenant code exists", {"id": str(existing.id)})
    tenant = Tenant(
        name=name,
        code=code,
        status="pending",
        allowed_scenarios=["notification", "callback_survey"],
        timezone="Asia/Shanghai",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(tenant)
    await session.flush()
    await audit(session, tenant_id=tenant.id, user_id=user_id, action="tenant.create", target_type="tenant", target_id=str(tenant.id), detail={"code": code}, trace_id=trace_id)
    return {"id": str(tenant.id), "status": tenant.status, "code": tenant.code}


def _import_file(job_id: uuid.UUID) -> Path:
    root = Path(os.environ.get("IMPORT_DIR", "tmp/imports"))
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{job_id}.csv"


async def accept_import(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_list: ContactList,
    content: bytes,
) -> ImportJob:
    """Store the upload and return. Parsing happens in run_import_job."""
    digest = hashlib.md5(content).hexdigest()
    current = await session.scalar(
        select(ImportJob).where(
            ImportJob.list_id == contact_list.id,
            ImportJob.file_md5 == digest,
            ImportJob.status == "running",
        )
    )
    if current is not None:
        return current
    previous = await session.scalar(
        select(ImportJob).where(
            ImportJob.list_id == contact_list.id,
            ImportJob.file_md5 == digest,
            ImportJob.status == "done",
        )
    )
    if previous is not None:
        return previous
    if contact_list.import_status == "running":
        raise ApiError(409, "CONFLICT", "import already running")
    job = ImportJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        list_id=contact_list.id,
        file_md5=digest,
        status="running",
        errors=[],
        payload=content,
    )
    session.add(job)
    contact_list.import_status = "running"
    await session.flush()
    try:
        _import_file(job.id).write_bytes(content)
    except OSError:
        pass
    return job


async def run_import_job(session: AsyncSession, job_id: uuid.UUID, settings: Settings) -> ImportJob:
    job = await session.scalar(select(ImportJob).where(ImportJob.id == job_id))
    if job is None:
        raise ApiError(404, "NOT_FOUND", "import not found")
    if job.status == "done":
        return job
    contact_list = await session.scalar(select(ContactList).where(ContactList.id == job.list_id))
    if contact_list is None:
        raise ApiError(404, "NOT_FOUND", "list not found")
    content = job.payload
    if content is not None and not isinstance(content, bytes):
        content = bytes(content)
    spool = _import_file(job.id)
    if content is None and spool.exists():
        content = spool.read_bytes()
    if not content:
        job.status = "failed"
        contact_list.import_status = "failed"
        await session.flush()
        return job
    try:
        await _parse_import(session, job=job, contact_list=contact_list, content=content, settings=settings)
    except Exception:
        job.status = "failed"
        contact_list.import_status = "failed"
        await session.flush()
        raise
    job.payload = None
    if spool.exists():
        spool.unlink()
    return job


async def import_contacts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_list: ContactList,
    content: bytes,
    settings: Settings,
) -> ImportJob:
    job = await accept_import(session, tenant_id=tenant_id, contact_list=contact_list, content=content)
    if job.status == "done":
        return job
    return await run_import_job(session, job.id, settings)


async def _parse_import(
    session: AsyncSession,
    *,
    job: ImportJob,
    contact_list: ContactList,
    content: bytes,
    settings: Settings,
) -> None:
    tenant_id = job.tenant_id
    consent_rows = (
        await session.scalars(
            select(ConsentRecord.phone_hash).where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.revoked.is_(False),
                ConsentRecord.scenario_code == contact_list.scenario_code,
            )
        )
    ).all()
    dnd_rows = (await session.scalars(select(DndEntry.phone_hash).where(DndEntry.tenant_id == tenant_id))).all()
    platform_rows = (await session.scalars(select(PlatformDnd.phone_hash))).all()
    consented = set(consent_rows)
    blocked = set(dnd_rows) | set(platform_rows)
    known = {
        row.phone_hash: row
        for row in (await session.scalars(select(Contact).where(Contact.list_id == contact_list.id))).all()
    }
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    errors: list[object] = []
    counts = {"total": 0, "dialable": 0, "no_consent": 0, "dnd_hit": 0, "invalid_format": 0}
    for index, row in enumerate(reader, start=2):
        counts["total"] += 1
        raw_phone = (row.get("phone") or "").strip()
        try:
            normalized = normalize_phone(raw_phone)
        except PhoneInvalidError:
            counts["invalid_format"] += 1
            if len(errors) < 50:
                errors.append({"row": index, "reason": "phone_invalid"})
            continue
        digest_phone = phone_hash(normalized, settings.phone_pepper)
        if digest_phone in blocked:
            dial_class = "dnd_hit"
        elif digest_phone not in consented:
            dial_class = "no_consent"
        else:
            dial_class = "dialable"
        counts[dial_class] += 1
        existing = known.get(digest_phone)
        if existing is None:
            existing = Contact(
                tenant_id=tenant_id,
                list_id=contact_list.id,
                phone_enc=encrypt_phone(normalized, settings.field_enc_key),
                phone_last4=normalized[-4:],
                phone_hash=digest_phone,
                display_name=(row.get("name") or "").strip() or None,
                dial_class=dial_class,
                reason=None if dial_class == "dialable" else dial_class,
            )
            session.add(existing)
            known[digest_phone] = existing
        else:
            existing.dial_class = dial_class
            existing.deleted_at = None
            existing.reason = None if dial_class == "dialable" else dial_class
        if counts["total"] % 5000 == 0:
            await session.flush()
    await session.flush()
    job.status = "done"
    job.total = counts["total"]
    job.dialable = counts["dialable"]
    job.no_consent = counts["no_consent"]
    job.dnd_hit = counts["dnd_hit"]
    job.invalid_format = counts["invalid_format"]
    job.errors = errors
    contact_list.import_status = "done"
    contact_list.file_md5 = job.file_md5


def job_payload(job: ImportJob) -> dict[str, object]:
    return {
        "task_id": str(job.id),
        "status": job.status,
        "total": job.total,
        "dialable": job.dialable,
        "no_consent": job.no_consent,
        "dnd_hit": job.dnd_hit,
        "invalid_format": job.invalid_format,
        "errors": job.errors,
    }


async def start_campaign(
    session: AsyncSession,
    *,
    campaign: Campaign,
    settings: Settings,
    redis: redis_async.Redis,
    user_id: uuid.UUID,
    trace_id: str,
    heard: str = "好的",
    now: datetime | None = None,
) -> dict[str, object]:
    if campaign.status not in {"approved", "running"}:
        raise ApiError(409, "CONFLICT", "campaign is not approved")
    tenant = await session.scalar(select(Tenant).where(Tenant.id == campaign.tenant_id))
    trunk = await session.scalar(select(Trunk).where(Trunk.id == campaign.trunk_id))
    if tenant is None or trunk is None:
        raise ApiError(404, "NOT_FOUND", "campaign dependencies missing")
    moment = now or utcnow()
    if await apply_billing_budget(session, tenant, moment):
        raise ApiError(409, "CONFLICT", "billing budget reached")
    existing = int(await session.scalar(select(func.count()).select_from(Task).where(Task.campaign_id == campaign.id)) or 0)
    if campaign.status == "running" and existing:
        return {"campaign_id": str(campaign.id), "task_count": existing, "idempotent": True, "started": 0}
    campaign.status = "running"
    campaign.created_by = campaign.created_by or user_id
    created = await ensure_initial_tasks(session, campaign)
    started = await dial_pending(
        session,
        campaign=campaign,
        tenant=tenant,
        trunk=trunk,
        settings=settings,
        redis=redis,
        now=moment,
        heard=heard,
    )
    total = int(await session.scalar(select(func.count()).select_from(Task).where(Task.campaign_id == campaign.id)) or 0)
    await audit(session, tenant_id=campaign.tenant_id, user_id=user_id, action="campaign.start", target_type="campaign", target_id=str(campaign.id), detail={"created": created, "started": started}, trace_id=trace_id)
    return {"campaign_id": str(campaign.id), "task_count": total, "idempotent": False, "started": started}


async def _block_caller_if_complaint_rate(
    session: AsyncSession, *, tenant_id: uuid.UUID, call_id: uuid.UUID | None
) -> None:
    if call_id is None:
        return
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != tenant_id or not call.caller_number:
        return
    number = await session.scalar(
        select(CallerNumber).where(CallerNumber.tenant_id == tenant_id, CallerNumber.number == call.caller_number)
    )
    if number is None:
        return
    number.complaint_count_30d += 1
    since = datetime.now(timezone.utc) - timedelta(days=30)
    dialed = int(
        await session.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.tenant_id == tenant_id,
                Call.caller_number == number.number,
                Call.start_at.is_not(None),
                Call.start_at >= since,
            )
        )
        or 0
    )
    if dialed > 0 and Decimal(number.complaint_count_30d) / Decimal(dialed) >= Decimal("0.0100"):
        number.status = "blocked"


async def open_complaint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone: str,
    detail: str,
    settings: Settings,
    user_id: uuid.UUID,
    trace_id: str,
    call_id: uuid.UUID | None,
) -> dict[str, object]:
    try:
        normalized = normalize_phone(phone)
    except PhoneInvalidError as exc:
        raise ApiError(422, "VALIDATION", "phone_invalid") from exc
    hashed = phone_hash(normalized, settings.phone_pepper)
    complaint = Complaint(
        tenant_id=tenant_id,
        call_id=call_id,
        phone_hash=hashed,
        phone_last4=normalized[-4:],
        detail=detail,
        status="open",
    )
    session.add(complaint)
    exists = await session.scalar(select(DndEntry).where(DndEntry.tenant_id == tenant_id, DndEntry.phone_hash == hashed))
    if exists is None:
        session.add(
            DndEntry(
                tenant_id=tenant_id,
                phone_hash=hashed,
                phone_last4=normalized[-4:],
                source="complaint",
                scope="tenant",
                permanent=True,
            )
        )
    await _block_caller_if_complaint_rate(session, tenant_id=tenant_id, call_id=call_id)
    await session.flush()
    await audit(session, tenant_id=tenant_id, user_id=user_id, action="complaint.create", target_type="complaint", target_id=str(complaint.id), detail={"phone": mask_phone(normalized)}, trace_id=trace_id)
    return {"id": str(complaint.id), "phone_last4": complaint.phone_last4, "status": complaint.status, "dnd": True}


async def build_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    trace_id: str,
) -> EvidencePack:
    calls = (await session.scalars(select(Call).where(Call.tenant_id == tenant_id).limit(200))).all()
    consents = (await session.scalars(select(ConsentRecord).where(ConsentRecord.tenant_id == tenant_id).limit(200))).all()
    payload: dict[str, object] = {
        "calls": [
            {
                "id": str(item.id),
                "status": item.status,
                "block_reason": item.block_reason,
                "template_version": item.template_version,
                "call_uuid": str(item.call_uuid),
            }
            for item in calls
        ],
        "consents": [
            {"id": str(item.id), "phone_last4": item.phone_last4, "scenario_code": item.scenario_code, "revoked": item.revoked}
            for item in consents
        ],
    }
    pack = EvidencePack(
        tenant_id=tenant_id,
        token=secrets.token_urlsafe(32),
        payload=payload,
        expires_at=utcnow() + timedelta(minutes=30),
        created_by=user_id,
    )
    session.add(pack)
    await audit(session, tenant_id=tenant_id, user_id=user_id, action="evidence.pack", target_type="evidence", target_id=pack.token, detail={}, trace_id=trace_id)
    await session.flush()
    return pack


async def require_password(session: AsyncSession, user_id: uuid.UUID, password: str) -> User:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or not verify_password(user.password_hash, password):
        raise ApiError(401, "BAD_CREDENTIALS", "confirmation password rejected")
    return user


def scenarios_ok(requested: list[str], allowed: list[str]) -> None:
    if not requested or any(item not in _SCENARIOS or item not in allowed for item in requested):
        raise ApiError(422, "COMPLIANCE_SCENARIO_NOT_ALLOWED", "scenario is not allowed for this tenant")


async def load_turns(session: AsyncSession, call_id: uuid.UUID) -> list[dict[str, object]]:
    rows = (await session.scalars(select(CallTurn).where(CallTurn.call_id == call_id).order_by(CallTurn.turn_idx))).all()
    return [{"turn_idx": row.turn_idx, "speaker": row.speaker, "text": row.text_content} for row in rows]


_DIALED = ("completed", "no_answer", "busy", "rejected", "failed", "transferring", "transfer_failed")
_ANSWERED = ("completed", "transferring", "transfer_failed")
_DND_REASONS = ("tenant_dnd", "platform_dnd")


def _rate(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return f"{numerator / denominator:.4f}"


def _blank_overview(*, demo_excluded: bool) -> dict[str, object]:
    return {
        "calls": 0,
        "completed": 0,
        "blocked_compliance": 0,
        "demo_excluded": demo_excluded,
        "answer_rate": None,
        "avg_duration_sec": None,
        "intent_a": 0,
        "intent_b": 0,
        "intent_c": 0,
        "intent_d": 0,
        "intent_na": 0,
        "transfer_rate": None,
        "reject_rate": None,
        "ai_complete_rate": None,
        "top_compliance_rule": "",
        "no_consent_blocks": 0,
        "dnd_blocks": 0,
        "unquoted_items": 0,
    }


async def overview(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, object]:
    """Tenant report. Rates stay empty when the denominator is zero. Demo data is excluded."""
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None or tenant.is_demo:
        return _blank_overview(demo_excluded=True)
    rows = (await session.scalars(select(Call).where(Call.tenant_id == tenant_id))).all()
    dialed = [row for row in rows if row.status in _DIALED]
    answered = [row for row in rows if row.status in _ANSWERED]
    blocked = [row for row in rows if row.status == "blocked_compliance"]
    completed = [row for row in rows if row.status == "completed"]
    transferred = [row for row in rows if row.status in {"transferring", "transfer_failed"}]
    reasons: dict[str, int] = {}
    for row in blocked:
        key = row.block_reason or ""
        reasons[key] = reasons.get(key, 0) + 1
    top = ""
    best = 0
    for reason, count in sorted(reasons.items()):
        if count > best and reason:
            best = count
            top = reason
    grades = {"A": 0, "B": 0, "C": 0, "D": 0, "NA": 0}
    for row in answered:
        if row.intent_grade in grades:
            grades[row.intent_grade] += 1
    duration = sum(row.duration_sec for row in answered)
    unquoted = int(
        await session.scalar(
            select(func.count()).select_from(BillingItem).where(BillingItem.tenant_id == tenant_id, BillingItem.amount.is_(None))
        )
        or 0
    )
    report = _blank_overview(demo_excluded=False)
    report.update(
        {
            "calls": len(rows),
            "completed": len(completed),
            "blocked_compliance": len(blocked),
            "answer_rate": _rate(len(answered), len(dialed)),
            "avg_duration_sec": None if not answered else round(duration / len(answered)),
            "intent_a": grades["A"],
            "intent_b": grades["B"],
            "intent_c": grades["C"],
            "intent_d": grades["D"],
            "intent_na": grades["NA"],
            "transfer_rate": _rate(len(transferred), len(answered)),
            "reject_rate": _rate(sum(1 for row in dialed if row.status == "rejected"), len(dialed)),
            "ai_complete_rate": _rate(len(completed), len(answered)),
            "top_compliance_rule": top,
            "no_consent_blocks": reasons.get("no_valid_consent", 0),
            "dnd_blocks": sum(reasons.get(reason, 0) for reason in _DND_REASONS),
            "unquoted_items": unquoted,
        }
    )
    return report


async def billing_summary(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, object]:
    items = (await session.scalars(select(BillingItem).where(BillingItem.tenant_id == tenant_id))).all()
    quoted = [item.amount for item in items if item.amount is not None]
    amount = format(sum(quoted), "f") if quoted and len(quoted) == len(items) else None
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    spend = await month_spend(session, tenant, utcnow()) if tenant is not None else None
    return {
        "item_count": len(items),
        "amount": amount,
        "currency": "CNY",
        "rate_status": "quoted" if amount is not None and items else "unquoted",
        "formula": "单通成本 = 线路分钟价×平均时长 + ASR分钟价×分钟数 + TTS分钟价×合成分钟 + LLM token价×单通token + 基础设施月费/月通话数 + 号码月租/月通话数",
        "billing_budget": None if spend is None or spend.budget is None else format(spend.budget, "f"),
        "month": None if spend is None else spend.label,
        "month_quoted": None if spend is None else format(spend.quoted, "f"),
        "month_unquoted": 0 if spend is None else spend.unquoted,
        "new_tasks_paused": False if spend is None else spend.reached,
    }


__all__ = [
    "Alert",
    "CircuitState",
    "QcResult",
    "ReportExport",
    "ScenarioTemplate",
    "ScriptNode",
    "Transcript",
    "UserRole",
    "schedule_retries",
]
