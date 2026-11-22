"""Compliance cases from the PRD. These checks cannot be configured off."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.compliance.gate_pre import (
    CampaignView,
    Flags,
    TenantView,
    TrunkView,
    pre_dial_gate,
)
from app.compliance.guard_incall import guard_model_output, on_caller_text
from app.compliance.redline import review_assistant_text, scan_redline
from app.compliance.opening import render_opening
from app.db import set_bypass, set_tenant
from app.models import (
    Call,
    Campaign,
    ConsentRecord,
    Contact,
    ContactList,
    DndEntry,
    PlatformDnd,
    ScenarioTemplate,
    Trunk,
)
from app.security.crypto import encrypt_phone, phone_hash

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)  # 10:00 Asia/Shanghai
PEPPER = b"test-pepper-not-for-production"
KEY = __import__("base64").b64decode("AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA=")
PHONE = "13800001111"


def _flags(**overrides: bool) -> Flags:
    base = {"calling_enabled": True, "feature_telesales": False, "feature_collection": False}
    base.update(overrides)
    return Flags(**base)


async def _world(session_factory, *, mode: str = "byoc", scenario: str = "notification"):
    tenant_id = uuid.uuid4()
    async with session_factory() as session:
        await set_tenant(session, tenant_id)
        session.add(
            __import__("app.models", fromlist=["Tenant"]).Tenant(
                id=tenant_id,
                name="合规租户",
                code=f"cmp-{tenant_id.hex[:8]}",
                status="active",
                allowed_scenarios=["notification", "callback_survey", "telesales"],
                timezone="Asia/Shanghai",
                daily_call_quota=200,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        template_id = uuid.uuid4()
        session.add(
            ScenarioTemplate(
                id=template_id,
                tenant_id=tenant_id,
                name="通知",
                scenario_code=scenario,
                status="approved",
                nodes={"nodes": []},
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        trunk_id = uuid.uuid4()
        session.add(
            Trunk(
                id=trunk_id,
                tenant_id=tenant_id,
                name="主线路",
                mode=mode,
                provider="byo",
                sip_host="sip.example.com",
                allowed_scenarios=["notification", "callback_survey", "telesales"],
                status="active",
                created_at=NOW,
            )
        )
        list_id = uuid.uuid4()
        session.add(
            ContactList(
                id=list_id,
                tenant_id=tenant_id,
                name="名单",
                scenario_code=scenario,
                created_at=NOW,
            )
        )
        normalized_hash = phone_hash(PHONE, PEPPER)
        contact_id = uuid.uuid4()
        session.add(
            Contact(
                id=contact_id,
                tenant_id=tenant_id,
                list_id=list_id,
                phone_enc=encrypt_phone(PHONE, KEY),
                phone_last4=PHONE[-4:],
                phone_hash=normalized_hash,
                dial_class="dialable",
                created_at=NOW,
            )
        )
        campaign_id = uuid.uuid4()
        session.add(
            Campaign(
                id=campaign_id,
                tenant_id=tenant_id,
                name="任务",
                scenario_code=scenario,
                template_id=template_id,
                template_version=1,
                list_id=list_id,
                status="running",
                start_at=NOW - timedelta(hours=1),
                end_at=NOW + timedelta(days=1),
                call_window="09:00-19:00",
                timezone="Asia/Shanghai",
                trunk_id=trunk_id,
                created_at=NOW,
            )
        )
        await session.commit()
    return {
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
        "contact_id": contact_id,
        "trunk_id": trunk_id,
        "phone_hash": normalized_hash,
    }


def _views(ids: dict[str, uuid.UUID | str], *, scenario: str = "notification") -> tuple[TenantView, TrunkView, CampaignView]:
    tenant = TenantView(ids["tenant_id"], "active", ["notification", "callback_survey", "telesales"], "Asia/Shanghai", 200)  # type: ignore[arg-type]
    trunk = TrunkView("byoc", "active", ["notification", "callback_survey", "telesales"])
    campaign = CampaignView(scenario, "09:00-19:00", "Asia/Shanghai", 1, 3, 6)
    return tenant, trunk, campaign


async def _consent(session_factory, ids, *, revoked: bool = False, scenario: str = "notification") -> None:
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        session.add(
            ConsentRecord(
                tenant_id=ids["tenant_id"],
                phone_enc=encrypt_phone(PHONE, KEY),
                phone_last4=PHONE[-4:],
                phone_hash=ids["phone_hash"],
                scenario_code=scenario,
                source="contract",
                consented_at=NOW - timedelta(days=1),
                revoked=revoked,
                revoked_at=NOW if revoked else None,
                created_at=NOW,
            )
        )
        await session.commit()


async def test_opening_cannot_be_omitted() -> None:
    text = render_opening(company="  ", purpose="")
    assert "人工智能" in text
    assert "助理" in text
    assert "录音" in text


async def test_no_consent_is_blocked(session_factory) -> None:
    ids = await _world(session_factory)
    tenant, trunk, campaign = _views(ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        decision = await pre_dial_gate(
            session,
            tenant=tenant,
            trunk=trunk,
            campaign=campaign,
            phone_hash=ids["phone_hash"],
            now=NOW,
            flags=_flags(),
        )
    assert decision.allowed is False
    assert decision.code == "COMPLIANCE_NO_CONSENT"


async def test_revoked_consent_is_blocked(session_factory) -> None:
    ids = await _world(session_factory)
    await _consent(session_factory, ids, revoked=True)
    tenant, trunk, campaign = _views(ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        decision = await pre_dial_gate(
            session,
            tenant=tenant,
            trunk=trunk,
            campaign=campaign,
            phone_hash=ids["phone_hash"],
            now=NOW,
            flags=_flags(),
        )
    assert decision.code == "COMPLIANCE_NO_CONSENT"


async def test_dnd_and_platform_dnd_block(session_factory) -> None:
    ids = await _world(session_factory)
    await _consent(session_factory, ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        session.add(
            DndEntry(
                tenant_id=ids["tenant_id"],
                phone_hash=ids["phone_hash"],
                phone_last4=PHONE[-4:],
                source="operator_manual",
                created_at=NOW,
            )
        )
        await session.commit()
    tenant, trunk, campaign = _views(ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        decision = await pre_dial_gate(
            session,
            tenant=tenant,
            trunk=trunk,
            campaign=campaign,
            phone_hash=ids["phone_hash"],
            now=NOW,
            flags=_flags(),
        )
    assert decision.code == "COMPLIANCE_DND_HIT"
    assert decision.block_reason == "tenant_dnd"

    other_hash = phone_hash("13900002222", PEPPER)
    async with session_factory() as session:
        await set_bypass(session)
        session.add(PlatformDnd(phone_hash=other_hash, phone_last4="2222", source="global_12321", created_at=NOW))
        await session.commit()
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        decision = await pre_dial_gate(
            session,
            tenant=tenant,
            trunk=trunk,
            campaign=campaign,
            phone_hash=other_hash,
            now=NOW,
            flags=_flags(),
        )
    assert decision.block_reason == "platform_dnd"


async def test_outside_window_and_lunch_are_blocked(session_factory) -> None:
    ids = await _world(session_factory)
    await _consent(session_factory, ids)
    tenant, trunk, campaign = _views(ids)
    early = datetime(2026, 11, 22, 0, 30, tzinfo=timezone.utc)  # 08:30 Shanghai
    lunch = datetime(2026, 11, 22, 4, 30, tzinfo=timezone.utc)  # 12:30 Shanghai
    wide = CampaignView("notification", "08:00-21:00", "Asia/Shanghai", 1, 3, 6)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        early_decision = await pre_dial_gate(
            session, tenant=tenant, trunk=trunk, campaign=wide, phone_hash=ids["phone_hash"], now=early, flags=_flags()
        )
        lunch_decision = await pre_dial_gate(
            session, tenant=tenant, trunk=trunk, campaign=campaign, phone_hash=ids["phone_hash"], now=lunch, flags=_flags()
        )
    assert early_decision.block_reason == "outside_call_window"
    assert lunch_decision.block_reason == "forbid_lunch_window"


async def test_daily_and_seven_day_frequency(session_factory) -> None:
    ids = await _world(session_factory)
    await _consent(session_factory, ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        session.add(
            Call(
                tenant_id=ids["tenant_id"],
                campaign_id=ids["campaign_id"],
                contact_id=ids["contact_id"],
                call_uuid=uuid.uuid4(),
                status="completed",
                template_version=1,
                created_at=NOW - timedelta(hours=1),
            )
        )
        await session.commit()
    tenant, trunk, campaign = _views(ids)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        decision = await pre_dial_gate(
            session, tenant=tenant, trunk=trunk, campaign=campaign, phone_hash=ids["phone_hash"], now=NOW, flags=_flags()
        )
    assert decision.block_reason == "freq_daily"

    async with session_factory() as session:
        await set_bypass(session)
        await session.execute(__import__("sqlalchemy").text("DELETE FROM calls"))
        await session.commit()
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        for days_ago in (1, 2, 3):
            session.add(
                Call(
                    tenant_id=ids["tenant_id"],
                    campaign_id=ids["campaign_id"],
                    contact_id=ids["contact_id"],
                    call_uuid=uuid.uuid4(),
                    status="completed",
                    template_version=1,
                    created_at=NOW - timedelta(days=days_ago),
                )
            )
        await session.commit()
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        decision = await pre_dial_gate(
            session, tenant=tenant, trunk=trunk, campaign=campaign, phone_hash=ids["phone_hash"], now=NOW, flags=_flags()
        )
    assert decision.block_reason == "freq_7d"


async def test_managed_trunk_cannot_run_telesales(session_factory) -> None:
    ids = await _world(session_factory, mode="managed", scenario="telesales")
    await _consent(session_factory, ids, scenario="telesales")
    tenant = TenantView(ids["tenant_id"], "active", ["notification", "callback_survey", "telesales"], "Asia/Shanghai", 200)
    trunk = TrunkView("managed", "active", ["telesales"])
    campaign = CampaignView("telesales", "09:00-19:00", "Asia/Shanghai", 1, 3, 6)
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        decision = await pre_dial_gate(
            session,
            tenant=tenant,
            trunk=trunk,
            campaign=campaign,
            phone_hash=ids["phone_hash"],
            now=NOW,
            flags=_flags(feature_telesales=True),
        )
    assert decision.block_reason == "managed_trunk_telesales"


async def test_stop_phrase_writes_dnd_and_hangs_up(session_factory) -> None:
    ids = await _world(session_factory)
    call_id = uuid.uuid4()
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        session.add(
            Call(
                id=call_id,
                tenant_id=ids["tenant_id"],
                campaign_id=ids["campaign_id"],
                contact_id=ids["contact_id"],
                call_uuid=uuid.uuid4(),
                status="talking",
                template_version=1,
                created_at=NOW,
            )
        )
        decision = await on_caller_text(
            session,
            tenant_id=ids["tenant_id"],
            call_id=call_id,
            phone_hash=ids["phone_hash"],
            phone_last4=PHONE[-4:],
            text="别再打了",
            company="回声",
            purpose="通知",
            opening_played=True,
        )
        await session.commit()
    assert decision.action == "hangup"
    assert decision.dnd_written is True
    async with session_factory() as session:
        await set_tenant(session, ids["tenant_id"])
        stored = await session.scalar(select(DndEntry).where(DndEntry.phone_hash == ids["phone_hash"]))
    assert stored is not None
    assert stored.source == "user_said_no"


async def test_redline_rewrites_then_hangs_up() -> None:
    spoken, hits, action = guard_model_output("我们保证收益。", prior_hits=0)
    assert hits == ["promise_return"]
    assert action == "rewrite"
    assert "保证收益" not in spoken
    _spoken, second_hits, second_action = guard_model_output("百分百稳赚。", prior_hits=1)
    assert second_hits == ["promise_return"]
    assert second_action == "hangup"


def test_a_negated_promise_stays_until_the_second_pass() -> None:
    calls: list[str] = []

    def judge(text: str) -> list[str]:
        calls.append(text)
        return ["promise_return"] if "疗效" in text else []

    assert scan_redline("我们不保证收益") == []
    assert scan_redline("我们保证收益") == ["promise_return"]
    local = review_assistant_text("我们保证收益", prior_hits=0, judge=judge)
    allowed = review_assistant_text("我们不保证收益", prior_hits=0, judge=judge)
    caught = review_assistant_text("这个疗效很好", prior_hits=0, judge=judge)
    slow = review_assistant_text("这个疗效很好", prior_hits=0, judge=judge, clock=iter((0.0, 0.04)).__next__)
    on_time = review_assistant_text("这个疗效很好", prior_hits=0, judge=judge, clock=iter((0.0, 0.03)).__next__)
    assert local.action == "rewrite"
    assert calls == ["我们不保证收益", "这个疗效很好", "这个疗效很好", "这个疗效很好"]
    assert "不保证收益" in allowed.text
    assert allowed.timed_out is False
    assert caught.action == "rewrite"
    assert "疗效" not in caught.text
    assert slow.timed_out is True
    assert slow.action == "allow"
    assert "疗效很好" in slow.text
    assert on_time.timed_out is False
    assert on_time.action == "rewrite"
    assert scan_redline("请把身份证号告诉我") == ["pii_disclosure"]
    assert scan_redline("卡号 6222021234567890123") == ["pii_disclosure"]
    assert scan_redline("请拨 13800007001") == []
    assert scan_redline("没有身份证号") == []
    hidden = guard_model_output("请把身份证号 110101199001011234 发我。", prior_hits=0)
    assert hidden[1] == ["pii_disclosure"]
    assert "110101199001011234" not in hidden[0]
    both = guard_model_output("我们保证收益，请报身份证号。", prior_hits=0)
    assert both[1] == ["promise_return", "pii_disclosure"]
    assert both[2] == "hangup"
