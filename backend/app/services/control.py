"""Control-plane use cases shared by the HTTP routes."""

from __future__ import annotations

import csv
import hashlib
import io
import secrets
import uuid
from datetime import datetime, timedelta, timezone
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


async def import_contacts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_list: ContactList,
    content: bytes,
    settings: Settings,
) -> ImportJob:
    digest = hashlib.md5(content).hexdigest()
    previous = await session.scalar(
        select(ImportJob).where(
            ImportJob.list_id == contact_list.id,
            ImportJob.file_md5 == digest,
            ImportJob.status == "done",
        )
    )
    if previous is not None:
        return previous
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
    job = ImportJob(tenant_id=tenant_id, list_id=contact_list.id, file_md5=digest, status="running", errors=[])
    session.add(job)
    await session.flush()
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
    contact_list.file_md5 = digest
    return job


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
    existing = int(await session.scalar(select(func.count()).select_from(Task).where(Task.campaign_id == campaign.id)) or 0)
    if campaign.status == "running" and existing:
        return {"campaign_id": str(campaign.id), "task_count": existing, "idempotent": True, "started": 0}
    tenant = await session.scalar(select(Tenant).where(Tenant.id == campaign.tenant_id))
    trunk = await session.scalar(select(Trunk).where(Trunk.id == campaign.trunk_id))
    if tenant is None or trunk is None:
        raise ApiError(404, "NOT_FOUND", "campaign dependencies missing")
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
        now=now or utcnow(),
        heard=heard,
    )
    total = int(await session.scalar(select(func.count()).select_from(Task).where(Task.campaign_id == campaign.id)) or 0)
    await audit(session, tenant_id=campaign.tenant_id, user_id=user_id, action="campaign.start", target_type="campaign", target_id=str(campaign.id), detail={"created": created, "started": started}, trace_id=trace_id)
    return {"campaign_id": str(campaign.id), "task_count": total, "idempotent": False, "started": started}


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


async def overview(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, object]:
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None or tenant.is_demo:
        return {"calls": 0, "completed": 0, "blocked_compliance": 0, "demo_excluded": True}
    calls = int(await session.scalar(select(func.count()).select_from(Call).where(Call.tenant_id == tenant_id)) or 0)
    completed = int(
        await session.scalar(select(func.count()).select_from(Call).where(Call.tenant_id == tenant_id, Call.status == "completed"))
        or 0
    )
    blocked = int(
        await session.scalar(
            select(func.count()).select_from(Call).where(Call.tenant_id == tenant_id, Call.status == "blocked_compliance")
        )
        or 0
    )
    return {"calls": calls, "completed": completed, "blocked_compliance": blocked, "demo_excluded": False}


async def billing_summary(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, object]:
    items = (await session.scalars(select(BillingItem).where(BillingItem.tenant_id == tenant_id))).all()
    quoted = [item.amount for item in items if item.amount is not None]
    amount = format(sum(quoted), "f") if quoted and len(quoted) == len(items) else None
    return {
        "item_count": len(items),
        "amount": amount,
        "currency": "CNY",
        "rate_status": "quoted" if amount is not None and items else "unquoted",
        "formula": "单通成本 = 线路分钟价×平均时长 + ASR分钟价×分钟数 + TTS分钟价×合成分钟 + LLM token价×单通token + 基础设施月费/月通话数 + 号码月租/月通话数",
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
