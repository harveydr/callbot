"""Close a tenant, then erase list data only after the retention window."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Call, Campaign, ConsentRecord, Contact, Tenant
from app.models.extra import Recording
from app.services.control import audit

_GRACE = timedelta(days=30)
_KEY = re.compile(r"^recordings/([0-9a-fA-F-]{36})/([0-9a-fA-F-]{36})\.wav$")
_OPEN = ("running", "paused", "approved")


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _held(created_at: datetime, retain_until: datetime | None, now: datetime) -> bool:
    floor = _aware(created_at) + _GRACE
    until = floor if retain_until is None else max(_aware(retain_until), floor)
    return until > now


def _recording_file(tenant_id: uuid.UUID, object_key: str) -> Path | None:
    matched = _KEY.fullmatch(object_key)
    if matched is None or matched.group(1) != str(tenant_id):
        return None
    root = Path(os.environ.get("RECORDINGS_DIR", "tmp/recordings")).resolve()
    path = (root / str(tenant_id) / f"{matched.group(2)}.wav").resolve()
    if path.parent != (root / str(tenant_id)).resolve():
        return None
    return path


async def close_tenant(session: AsyncSession, tenant: Tenant, *, user_id: uuid.UUID | None, trace_id: str) -> None:
    """Stop open campaigns and record the termination. A second close does not write again."""
    if tenant.status == "closed":
        return
    tenant.status = "closed"
    campaigns = (
        await session.scalars(select(Campaign).where(Campaign.tenant_id == tenant.id, Campaign.status.in_(_OPEN)))
    ).all()
    for campaign in campaigns:
        campaign.status = "aborted"
    await audit(
        session,
        tenant_id=tenant.id,
        user_id=user_id,
        action="tenant.close",
        target_type="tenant",
        target_id=str(tenant.id),
        detail={},
        trace_id=trace_id,
    )


async def purge_closed_tenants(session: AsyncSession, now: datetime) -> list[str]:
    """Erase list data 30 days after close, once related recordings are past retention."""
    moment = _aware(now)
    tenants = (await session.scalars(select(Tenant).where(Tenant.status == "closed"))).all()
    erased: list[str] = []
    for tenant in tenants:
        closed_at = await session.scalar(
            select(func.min(AuditLog.created_at)).where(AuditLog.tenant_id == tenant.id, AuditLog.action == "tenant.close")
        )
        if closed_at is None or _aware(closed_at) + _GRACE > moment:
            continue
        if await _purge_one(session, tenant, moment):
            erased.append(str(tenant.id))
    return erased


async def _purge_one(session: AsyncSession, tenant: Tenant, now: datetime) -> bool:
    calls = (await session.scalars(select(Call).where(Call.tenant_id == tenant.id))).all()
    recordings = (await session.scalars(select(Recording).where(Recording.tenant_id == tenant.id))).all()
    retain_by_call = {row.call_id: row.retain_until for row in recordings}
    held_contacts: set[uuid.UUID] = set()
    held_hashes: set[str] = set()
    for call in calls:
        if _held(call.created_at, retain_by_call.get(call.id), now):
            held_contacts.add(call.contact_id)
    contacts = (await session.scalars(select(Contact).where(Contact.tenant_id == tenant.id))).all()
    for contact in contacts:
        if contact.id in held_contacts:
            held_hashes.add(contact.phone_hash)
    wiped_contacts = 0
    for contact in contacts:
        if contact.id in held_contacts or contact.phone_last4 == "----":
            continue
        contact.phone_enc = b"\x00"
        contact.phone_last4 = "----"
        contact.phone_hash = f"purged:{contact.id}"
        contact.display_name = None
        contact.deleted_at = now
        wiped_contacts += 1
    consents = (await session.scalars(select(ConsentRecord).where(ConsentRecord.tenant_id == tenant.id))).all()
    wiped_consents = 0
    for consent in consents:
        if consent.phone_hash in held_hashes or consent.phone_last4 == "----":
            continue
        consent.phone_enc = b"\x00"
        consent.phone_last4 = "----"
        consent.phone_hash = f"purged:{consent.id}"
        wiped_consents += 1
    released: list[str] = []
    for recording in recordings:
        if _aware(recording.retain_until) > now or recording.object_key.startswith("purged/"):
            continue
        path = _recording_file(tenant.id, recording.object_key)
        if path is not None and path.is_file():
            path.unlink()
        released.append(recording.object_key)
        recording.object_key = f"purged/{recording.object_key}"
    if wiped_contacts == 0 and wiped_consents == 0 and not released:
        return False
    await audit(
        session,
        tenant_id=tenant.id,
        user_id=None,
        action="tenant.deletion_report",
        target_type="tenant",
        target_id=str(tenant.id),
        detail={"contacts": wiped_contacts, "consents": wiped_consents, "recordings": released},
        trace_id="",
    )
    return True
