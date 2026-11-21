"""Vendor circuit opens on quota errors and on a measured failure burst, then probes once."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import load_settings
from app.db import set_bypass
from app.models import Task
from app.models.extra import Alert
from app.services.dialer import dial_pending
from app.voice.circuit import SharedVendorCircuit, VendorCircuit, reset_vendor_circuits
from tests.test_qc import NOW, _ready


def _clock(ticks: list[float]) -> VendorCircuit:
    return VendorCircuit(clock=lambda: ticks[0])


def test_one_429_opens_and_a_later_probe_can_close(caplog: logging.LogCaptureFixture) -> None:
    ticks = [0.0]
    circuit = _clock(ticks)
    with caplog.at_level(logging.WARNING):
        assert circuit.allow_vendor() is True
        circuit.observe(429)
        assert circuit.open is True
        assert circuit.allow_vendor() is False
        ticks[0] = 30.0
        assert circuit.allow_vendor() is True
        assert circuit.allow_vendor() is False
        circuit.observe_success()
    assert circuit.open is False
    assert circuit.allow_vendor() is True
    assert [code for code, _reason in circuit.drain_alerts()] == [
        "circuit_open",
        "circuit_half_open",
        "circuit_closed",
    ]
    assert any("circuit_open" in record.message for record in caplog.records)


def test_four_failures_stay_closed_until_the_fifth() -> None:
    circuit = VendorCircuit()
    for _ in range(4):
        assert circuit.allow_vendor() is True
        circuit.observe(500)
    assert circuit.open is False
    circuit.observe(500)
    assert circuit.open is True
    assert circuit.reason == "vendor_failures"


def test_mixed_failures_open_when_the_rate_is_above_half() -> None:
    circuit = VendorCircuit()
    for status in (500, 500, 200, 500, 500):
        assert circuit.allow_vendor() is True
        if status == 200:
            circuit.observe_success()
        else:
            circuit.observe(status)
    assert circuit.open is True
    assert circuit.reason == "vendor_failures"


def test_an_even_failure_rate_stays_closed() -> None:
    circuit = VendorCircuit()
    for status in (500, 200, 500, 200, 200):
        circuit.allow_vendor()
        if status == 200:
            circuit.observe_success()
        else:
            circuit.observe(status)
    assert circuit.open is False


def test_a_failed_probe_opens_the_circuit_again() -> None:
    ticks = [0.0]
    circuit = _clock(ticks)
    circuit.observe(429)
    ticks[0] = 30.0
    assert circuit.allow_vendor() is True
    circuit.observe(500)
    assert circuit.open is True
    ticks[0] = 59.0
    assert circuit.allow_vendor() is False
    ticks[0] = 60.0
    assert circuit.allow_vendor() is True


def test_two_processes_share_one_open_circuit() -> None:
    settings = load_settings()
    ticks = [1_000.0]
    first = SharedVendorCircuit(settings.redis_url, "asr-share", clock=lambda: ticks[0])
    second = SharedVendorCircuit(settings.redis_url, "asr-share", clock=lambda: ticks[0])
    try:
        first.reset()
        first.observe(429)
        assert second.open is True
        assert second.allow_vendor() is False
        ticks[0] = 1_030.0
        assert first.allow_vendor() is True
        assert second.allow_vendor() is False
        assert [code for code, _reason in first.drain_alerts()] == ["circuit_open", "circuit_half_open"]
    finally:
        first.reset()
        first.close()
        second.close()


async def test_dial_stores_the_circuit_alert(
    session_factory: async_sessionmaker[AsyncSession], redis_client
) -> None:
    settings = load_settings()
    shared = SharedVendorCircuit(settings.redis_url, "asr")
    shared.reset()
    reset_vendor_circuits()
    try:
        shared.observe(429)
        async with session_factory() as session:
            await set_bypass(session)
            tenant, trunk, campaign, contact = await _ready(session, "circuit-co", "13800006661")
            session.add(
                Task(tenant_id=tenant.id, campaign_id=campaign.id, contact_id=contact.id, attempt_no=1, status="pending")
            )
            await session.flush()
            started = await dial_pending(
                session,
                campaign=campaign,
                tenant=tenant,
                trunk=trunk,
                settings=settings,
                redis=redis_client,
                now=NOW,
                heard="好的",
            )
            alert = await session.scalar(select(Alert).where(Alert.tenant_id == tenant.id, Alert.code == "circuit_open"))
            assert started == 1
            assert alert is not None and alert.severity == "warning" and "vendor_429" in alert.message
    finally:
        shared.reset()
        shared.close()
        reset_vendor_circuits()
