"""Test isolation: private database, Redis db 15, and reverse-FK cleanup."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import redis.asyncio as redis_async
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import make_engine, make_session_factory, set_bypass

BACKEND = Path(__file__).resolve().parents[1]
_VENDORED_ALEMBIC = Path("/tmp/echodesk-venv/bin/alembic")
SUPER_URL = os.environ.get("SUPER_DATABASE_URL", "postgresql+asyncpg://echodesk@127.0.0.1:5432/echodesk_test")
APP_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://echodesk_app@127.0.0.1:5432/echodesk_test")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")

os.environ["DATABASE_URL"] = APP_URL
os.environ["REDIS_URL"] = REDIS_URL
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production")
os.environ.setdefault("PHONE_PEPPER", "test-pepper-not-for-production")
os.environ.setdefault("FIELD_ENC_KEY", "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA=")
os.environ.setdefault("CALLING_ENABLED", "true")
os.environ.setdefault("FEATURE_TELESALES", "false")
os.environ.setdefault("FEATURE_COLLECTION", "false")

CLEANUP_ORDER = (
    "report_exports",
    "evidence_packs",
    "import_jobs",
    "idempotency_keys",
    "alerts",
    "circuit_states",
    "billing_disputes",
    "billing_items",
    "invoices",
    "subscriptions",
    "qc_results",
    "transcripts",
    "recordings",
    "call_turns",
    "call_transfers",
    "complaints",
    "contact_optins",
    "number_health_stats",
    "script_nodes",
    "redline_rules",
    "user_roles",
    "call_events",
    "calls",
    "tasks",
    "campaigns",
    "contacts",
    "contact_lists",
    "dnd_entries",
    "consent_records",
    "caller_numbers",
    "trunks",
    "qualifications",
    "users",
    "scenario_templates",
    "platform_dnd",
    "audit_logs",
    "billing_rates",
    "plans",
    "tenants",
)


def _psql(sql: str, database: str = "postgres") -> str:
    completed = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-U", "echodesk", "-d", database, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _alembic_program() -> list[str]:
    override = os.environ.get("ALEMBIC_BIN")
    if override:
        return [override]
    if _VENDORED_ALEMBIC.exists():
        return [str(_VENDORED_ALEMBIC)]
    found = shutil.which("alembic")
    if found:
        return [found]
    return [sys.executable, "-m", "alembic"]


def _alembic(command: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = SUPER_URL
    target = "head" if command == "upgrade" else "base"
    subprocess.run([*_alembic_program(), command, target], cwd=BACKEND, env=env, check=True)


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    _psql(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'echodesk_app') THEN
            CREATE ROLE echodesk_app LOGIN PASSWORD 'echodesk' NOSUPERUSER NOBYPASSRLS;
          END IF;
        END
        $$;
        """
    )
    if _psql("SELECT 1 FROM pg_database WHERE datname = 'echodesk_test'") != "1":
        _psql("CREATE DATABASE echodesk_test OWNER echodesk")
    _psql("ALTER ROLE echodesk_app WITH LOGIN PASSWORD 'echodesk' NOSUPERUSER NOBYPASSRLS")
    _psql("GRANT CONNECT ON DATABASE echodesk_test TO echodesk_app")
    _alembic("upgrade")
    _alembic("downgrade")
    _alembic("upgrade")
    yield


@pytest_asyncio.fixture
async def session_factory(database: None) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(APP_URL)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_database(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[None]:
    yield
    async with session_factory() as session:
        await set_bypass(session)
        for table in CLEANUP_ORDER:
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def redis_client() -> AsyncIterator[redis_async.Redis]:
    client = redis_async.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
