"""Monthly spend cap. Quoted amounts count. Unquoted amounts stay out of the sum."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, Tenant
from app.models.extra import Alert, BillingItem


@dataclass(frozen=True)
class MonthSpend:
    label: str
    quoted: Decimal
    unquoted: int
    budget: Decimal | None

    @property
    def warn(self) -> bool:
        if self.budget is None or self.quoted <= 0:
            return False
        return self.quoted * 5 >= self.budget * 4

    @property
    def reached(self) -> bool:
        return self.budget is not None and self.quoted >= self.budget


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now.astimezone(timezone.utc)


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")), "f")


def month_window(now: datetime, timezone_name: str) -> tuple[datetime, datetime, str]:
    """Half-open local month, returned in UTC, plus the YYYY-MM label."""
    zone = ZoneInfo(timezone_name or "Asia/Shanghai")
    local = _aware(now).astimezone(zone)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), f"{start.year:04d}-{start.month:02d}"


async def month_spend(session: AsyncSession, tenant: Tenant, now: datetime) -> MonthSpend:
    start, end, label = month_window(now, tenant.timezone)
    quoted_raw = await session.scalar(
        select(func.coalesce(func.sum(BillingItem.amount), 0)).where(
            BillingItem.tenant_id == tenant.id,
            BillingItem.amount.is_not(None),
            BillingItem.created_at >= start,
            BillingItem.created_at < end,
        )
    )
    unquoted = int(
        await session.scalar(
            select(func.count())
            .select_from(BillingItem)
            .where(
                BillingItem.tenant_id == tenant.id,
                BillingItem.amount.is_(None),
                BillingItem.created_at >= start,
                BillingItem.created_at < end,
            )
        )
        or 0
    )
    quoted = Decimal(quoted_raw or 0).quantize(Decimal("0.0001"))
    budget = None if tenant.billing_budget is None else Decimal(tenant.billing_budget).quantize(Decimal("0.0001"))
    return MonthSpend(label=label, quoted=quoted, unquoted=unquoted, budget=budget)


async def _once(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    severity: str,
    message: str,
    now: datetime,
) -> None:
    existing = await session.scalar(
        select(Alert.id).where(Alert.tenant_id == tenant_id, Alert.code == code, Alert.message == message)
    )
    if existing is not None:
        return
    session.add(
        Alert(
            tenant_id=tenant_id,
            severity=severity,
            code=code,
            message=message,
            created_at=now,
        )
    )


async def apply_billing_budget(session: AsyncSession, tenant: Tenant, now: datetime) -> bool:
    """Pause new work once quoted spend reaches the cap. Live calls stay as they are."""
    if tenant.billing_budget is None:
        return False
    moment = _aware(now)
    spend = await month_spend(session, tenant, moment)
    budget = spend.budget
    if budget is None:
        return False
    shown = _money(budget)
    if spend.unquoted:
        await _once(
            session,
            tenant_id=tenant.id,
            code="billing_budget_unquoted",
            severity="warning",
            message=f"{spend.label} 存在未报价明细，月度上限未计入这些金额",
            now=moment,
        )
    if spend.warn:
        await _once(
            session,
            tenant_id=tenant.id,
            code="billing_budget",
            severity="warning",
            message=f"{spend.label} 费用达到月度上限 {shown} 的 80%",
            now=moment,
        )
    if not spend.reached:
        await session.flush()
        return False
    await _once(
        session,
        tenant_id=tenant.id,
        code="billing_budget_exceeded",
        severity="critical",
        message=f"{spend.label} 费用达到月度上限 {shown}，已暂停新任务",
        now=moment,
    )
    running = (
        await session.scalars(
            select(Campaign).where(Campaign.tenant_id == tenant.id, Campaign.status == "running")
        )
    ).all()
    for campaign in running:
        campaign.status = "paused"
    await session.flush()
    return True
