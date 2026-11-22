"""API latency, ASR errors, model latency, disk, and certificate expiry raise one notice each."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models.extra import Alert
from app.services.ops_alerts import cert_deadline, raise_ops_alerts, record_asr, record_http, record_llm
from app.voice.asr import MockAsr
from app.voice.orchestrator import Frame, run_session

NOW = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)


async def _codes(session: AsyncSession) -> list[str]:
    rows = (await session.scalars(select(Alert).where(Alert.tenant_id.is_(None)))).all()
    return sorted(row.code for row in rows)


async def test_latency_windows_warn_once(session_factory: async_sessionmaker[AsyncSession], redis_client) -> None:
    far = cert_deadline(NOW, days=40)
    async with session_factory() as session:
        await set_bypass(session)
        for _ in range(4):
            await record_http(redis_client, 5000, NOW)
        quiet = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=far)
        await redis_client.delete("ops:http_ms")
        for _ in range(5):
            await record_http(redis_client, 1000, NOW)
        at_limit = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.80, cert_not_after=cert_deadline(NOW, days=30))
        await redis_client.delete("ops:http_ms")
        for _ in range(5):
            await record_http(redis_client, 1001, NOW - timedelta(minutes=6))
        stale = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.80, cert_not_after=cert_deadline(NOW, days=30))
        for _ in range(5):
            await record_http(redis_client, 1001, NOW - timedelta(minutes=4))
        warned = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.80, cert_not_after=cert_deadline(NOW, days=30))
        again = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.80, cert_not_after=cert_deadline(NOW, days=30))
        assert quiet == 0 and at_limit == 0 and stale == 0
        assert warned == 1 and again == 0
        assert await _codes(session) == ["api_p95"]


async def test_asr_and_llm_rates_use_their_own_windows(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        for _ in range(9):
            await record_asr(redis_client, failed=False, now=NOW)
        await record_asr(redis_client, failed=True, now=NOW)
        exact = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=40))
        await record_asr(redis_client, failed=True, now=NOW - timedelta(minutes=3))
        stale = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=40))
        await record_asr(redis_client, failed=True, now=NOW)
        asr = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=40))
        for _ in range(5):
            await record_llm(redis_client, 1500, NOW)
        held = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=40))
        for _ in range(5):
            await record_llm(redis_client, 1501, NOW - timedelta(minutes=11))
        old = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=40))
        await record_llm(redis_client, 1501, NOW)
        llm = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=40))
        assert exact == 0 and stale == 0 and held == 0 and old == 0
        assert asr == 1 and llm == 1
        assert await _codes(session) == ["asr_error_rate", "llm_first_token_p95"]


async def test_disk_and_certificate_windows(session_factory: async_sessionmaker[AsyncSession], redis_client) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        full = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.81, cert_not_after=cert_deadline(NOW, days=29))
        repeat = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.81, cert_not_after=cert_deadline(NOW, days=29))
        week = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=6))
        day = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=cert_deadline(NOW, days=0.5))
        expired = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10, cert_not_after=NOW - timedelta(hours=1))
        rows = (await session.scalars(select(Alert).where(Alert.tenant_id.is_(None)))).all()
        by_code = {row.code: row.severity for row in rows}
        assert full == 2 and repeat == 0 and week == 1 and day == 1 and expired == 1
        assert by_code["disk_high"] == "warning"
        assert by_code["cert_expire_30"] == "warning"
        assert by_code["cert_expire_7"] == "warning"
        assert by_code["cert_expire_1"] == "critical"
        assert by_code["cert_expired"] == "critical"


async def test_a_certificate_file_uses_the_same_window(
    session_factory: async_sessionmaker[AsyncSession], redis_client, tmp_path: Path, monkeypatch
) -> None:
    pem = tmp_path / "server.pem"
    pem.write_bytes(_certificate(cert_deadline(NOW, days=6)))
    monkeypatch.setenv("TLS_CERT_FILE", str(pem))
    async with session_factory() as session:
        await set_bypass(session)
        read = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10)
        assert read == 1
        assert await _codes(session) == ["cert_expire_7"]
    monkeypatch.setenv("TLS_CERT_FILE", str(tmp_path / "missing.pem"))
    async with session_factory() as session:
        await set_bypass(session)
        missing = await raise_ops_alerts(session, redis_client, now=NOW, disk_ratio=0.10)
        assert missing == 1
        assert "cert_unreadable" in await _codes(session)


async def test_a_vendor_rejection_counts_and_an_open_circuit_does_not() -> None:
    failed = await run_session([Frame("caller", b"PCM:hello")], company="回声", purpose="通知", asr=MockAsr(status=429))
    assert failed.asr_attempts == 1 and failed.asr_errors == 1
    assert failed.llm_first_token_ms is not None
    from app.voice.circuit import VendorCircuit

    opened = VendorCircuit()
    opened.observe(429)
    skipped = await run_session(
        [Frame("caller", b"PCM:hello")], company="回声", purpose="通知", asr=MockAsr(status=429), circuit=opened
    )
    assert skipped.asr_attempts == 0 and skipped.asr_errors == 0


def _certificate(not_after: datetime) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "echodesk.local")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_after - timedelta(days=30))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)
