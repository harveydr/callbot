"""Pre-dial gate. A failed check blocks the call and does not bill it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.windows import (
    effective_freq_30d,
    effective_freq_7d,
    effective_freq_daily,
    local_period_start,
    window_block_reason,
)
from app.models import Call, ConsentRecord, Contact, DndEntry, PlatformDnd, Qualification

_DIALED = (
    "queued",
    "dialing",
    "ringing",
    "answered",
    "talking",
    "interrupted",
    "transferring",
    "transfer_failed",
    "completed",
    "no_answer",
    "busy",
    "rejected",
    "failed",
)
_TELESALES_LICENSES = ("telco_value_added_license", "callcenter_license")


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    code: str | None
    block_reason: str | None


def allow() -> GateDecision:
    return GateDecision(True, None, None)


def block(code: str, reason: str) -> GateDecision:
    return GateDecision(False, code, reason)


@dataclass(frozen=True)
class TenantView:
    id: uuid.UUID
    status: str
    allowed_scenarios: list[str]
    timezone: str
    daily_call_quota: int


@dataclass(frozen=True)
class TrunkView:
    mode: str
    status: str
    allowed_scenarios: list[str]


@dataclass(frozen=True)
class CampaignView:
    scenario_code: str
    call_window: str
    timezone: str
    freq_per_phone_daily: int
    freq_per_phone_7d: int
    freq_per_phone_30d: int


@dataclass(frozen=True)
class Flags:
    calling_enabled: bool
    feature_telesales: bool
    feature_collection: bool


async def _count_since(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone_hash: str,
    since: datetime,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Call)
        .join(Contact, Call.contact_id == Contact.id)
        .where(
            Call.tenant_id == tenant_id,
            Contact.phone_hash == phone_hash,
            Call.status.in_(_DIALED),
            Call.created_at >= since,
        )
    )
    value = await session.scalar(stmt)
    return int(value or 0)


async def pre_dial_gate(
    session: AsyncSession,
    *,
    tenant: TenantView,
    trunk: TrunkView,
    campaign: CampaignView,
    phone_hash: str,
    now: datetime,
    flags: Flags,
) -> GateDecision:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    if not flags.calling_enabled:
        return block("COMPLIANCE_CIRCUIT_OPEN", "calling_disabled")
    if tenant.status != "active":
        return block("COMPLIANCE_QUALIFICATION_MISSING", "tenant_not_active")
    if campaign.scenario_code not in tenant.allowed_scenarios:
        return block("COMPLIANCE_SCENARIO_NOT_ALLOWED", "scenario_not_allowed")
    if campaign.scenario_code == "telesales" and not flags.feature_telesales:
        return block("COMPLIANCE_SCENARIO_NOT_ALLOWED", "telesales_disabled")
    if campaign.scenario_code == "collection" and not flags.feature_collection:
        return block("COMPLIANCE_SCENARIO_NOT_ALLOWED", "collection_disabled")
    if campaign.scenario_code == "telesales" and trunk.mode != "byoc":
        return block("COMPLIANCE_SCENARIO_NOT_ALLOWED", "managed_trunk_telesales")
    if campaign.scenario_code == "telesales":
        license_stmt = (
            select(func.count())
            .select_from(Qualification)
            .where(
                Qualification.tenant_id == tenant.id,
                Qualification.status == "approved",
                Qualification.doc_type.in_(_TELESALES_LICENSES),
            )
        )
        licenses = int(await session.scalar(license_stmt) or 0)
        if licenses < 1:
            return block("COMPLIANCE_QUALIFICATION_MISSING", "telesales_license_missing")
    if trunk.status != "active" or campaign.scenario_code not in trunk.allowed_scenarios:
        return block("COMPLIANCE_SCENARIO_NOT_ALLOWED", "trunk_scenario_mismatch")

    platform_hit = await session.scalar(select(PlatformDnd.id).where(PlatformDnd.phone_hash == phone_hash).limit(1))
    if platform_hit is not None:
        return block("COMPLIANCE_DND_HIT", "platform_dnd")
    tenant_hit = await session.scalar(
        select(DndEntry.id)
        .where(DndEntry.tenant_id == tenant.id, DndEntry.phone_hash == phone_hash)
        .limit(1)
    )
    if tenant_hit is not None:
        return block("COMPLIANCE_DND_HIT", "tenant_dnd")

    consent = await session.scalar(
        select(ConsentRecord).where(
            ConsentRecord.tenant_id == tenant.id,
            ConsentRecord.phone_hash == phone_hash,
            ConsentRecord.scenario_code == campaign.scenario_code,
            ConsentRecord.revoked.is_(False),
        )
    )
    if consent is None or consent.consented_at > now or (consent.expires_at is not None and consent.expires_at <= now):
        return block("COMPLIANCE_NO_CONSENT", "no_valid_consent")

    tz_name = campaign.timezone or tenant.timezone
    window_reason = window_block_reason(now, timezone_name=tz_name, call_window=campaign.call_window)
    if window_reason is not None:
        return block("COMPLIANCE_WINDOW_CLOSED", window_reason)

    daily_limit = effective_freq_daily(campaign.freq_per_phone_daily)
    week_limit = effective_freq_7d(campaign.freq_per_phone_7d)
    month_limit = effective_freq_30d(campaign.freq_per_phone_30d)
    daily_since = local_period_start(now, timezone_name=tz_name, days=1)
    week_since = local_period_start(now, timezone_name=tz_name, days=7)
    month_since = local_period_start(now, timezone_name=tz_name, days=30)
    if await _count_since(session, tenant_id=tenant.id, phone_hash=phone_hash, since=daily_since) >= daily_limit:
        return block("QUOTA_EXCEEDED", "freq_daily")
    if await _count_since(session, tenant_id=tenant.id, phone_hash=phone_hash, since=week_since) >= week_limit:
        return block("QUOTA_EXCEEDED", "freq_7d")
    if await _count_since(session, tenant_id=tenant.id, phone_hash=phone_hash, since=month_since) >= month_limit:
        return block("QUOTA_EXCEEDED", "freq_30d")

    quota_since = local_period_start(now, timezone_name=tz_name, days=1)
    tenant_calls = await session.scalar(
        select(func.count())
        .select_from(Call)
        .where(
            Call.tenant_id == tenant.id,
            Call.status.in_(_DIALED),
            Call.created_at >= quota_since,
        )
    )
    if int(tenant_calls or 0) >= tenant.daily_call_quota:
        return block("QUOTA_EXCEEDED", "daily_call_quota")
    return allow()
