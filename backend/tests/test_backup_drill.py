"""The go-live backup must restore the same rows, policies, and RLS flags."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import Tenant

ROOT = Path(__file__).resolve().parents[2]


async def test_encrypted_dump_restores_the_same_database(session_factory: async_sessionmaker[AsyncSession]) -> None:
    code = f"drill{uuid.uuid4().hex[:12]}"
    async with session_factory() as session:
        await set_bypass(session)
        session.add(
            Tenant(
                name="备份演练",
                code=code,
                status="active",
                allowed_scenarios=["notification"],
                timezone="Asia/Shanghai",
                is_demo=True,
            )
        )
        await session.commit()
    env = os.environ.copy()
    env["DRILL_SOURCE_DB"] = "echodesk_test"
    env["DRILL_RESTORE_DB"] = "echodesk_restore_drill"
    env["DRILL_MARKER"] = code
    env.setdefault("PGHOST", "127.0.0.1")
    env.setdefault("PGUSER", "echodesk")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backup_restore_drill.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "backup restore drill passed" in completed.stdout
    assert "encrypted=aes-256-gcm" in completed.stdout
