"""Seed the platform admin, default templates, and one clearly marked demo tenant."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import make_engine, make_session_factory, set_bypass  # noqa: E402
from app.models import ScenarioTemplate, Tenant, User  # noqa: E402
from app.models.extra import Plan, RedlineRule, ScriptNode, UserRole  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402
from sqlalchemy import select  # noqa: E402

NOW = datetime.now(timezone.utc)


async def main() -> None:
    password = os.environ.get("SEED_ADMIN_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("SEED_ADMIN_PASSWORD must be set to at least 8 characters")
    engine = make_engine(os.environ["DATABASE_URL"])
    factory = make_session_factory(engine)
    async with factory() as session:
        await set_bypass(session)
        admin = await session.scalar(select(User).where(User.username == "platform.admin"))
        if admin is None:
            admin = User(
                tenant_id=None,
                username="platform.admin",
                password_hash=hash_password(password),
                display_name="平台超管",
                role="platform_admin",
                must_change_password=True,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(admin)
            await session.flush()
            session.add(UserRole(user_id=admin.id, tenant_id=None, role="platform_admin"))
        for code, pattern in (
            ("promise_return", "保证收益|稳赚|百分百|保本"),
            ("fake_authority", "我是(银行|公安|警察|法院|政府)"),
            ("loan_inducement", "无抵押秒批|保证下款|内部渠道放款"),
            ("abuse", "傻逼|滚开|神经病"),
        ):
            exists = await session.scalar(select(RedlineRule).where(RedlineRule.code == code, RedlineRule.tenant_id.is_(None)))
            if exists is None:
                session.add(RedlineRule(tenant_id=None, code=code, pattern=pattern, enabled=True))
        if await session.scalar(select(Plan).where(Plan.code == "trial")) is None:
            session.add(Plan(code="trial", name="试用"))
        for scenario, title in (("notification", "通知"), ("callback_survey", "回访")):
            template = await session.scalar(
                select(ScenarioTemplate).where(
                    ScenarioTemplate.tenant_id.is_(None),
                    ScenarioTemplate.scenario_code == scenario,
                )
            )
            if template is None:
                template = ScenarioTemplate(
                    tenant_id=None,
                    name=title,
                    scenario_code=scenario,
                    status="approved",
                    nodes={"opening": "template"},
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(template)
                await session.flush()
                session.add(
                    ScriptNode(
                        template_id=template.id,
                        tenant_id=None,
                        node_key="opening",
                        node_type="mandatory",
                        content={"text": "开场由系统模板渲染，不能关闭身份声明和录音告知。"},
                    )
                )
        demo = await session.scalar(select(Tenant).where(Tenant.code == "demo"))
        if demo is None:
            session.add(
                Tenant(
                    id=uuid.uuid4(),
                    name="演示租户",
                    code="demo",
                    status="active",
                    allowed_scenarios=["notification", "callback_survey"],
                    timezone="Asia/Shanghai",
                    is_demo=True,
                    data_use_consent=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.commit()
    await engine.dispose()
    print("seed complete; platform.admin must change the password at first login; demo tenant is_demo=true")


if __name__ == "__main__":
    asyncio.run(main())
