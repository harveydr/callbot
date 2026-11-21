"""Operational alerts from the go-live list. They warn. They do not skip a compliance gate."""

from __future__ import annotations

import math
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extra import Alert

_HTTP = "ops:http_ms"
_ASR = "ops:asr"
_LLM = "ops:llm_ms"
_MIN_SAMPLE = 5
_API_WINDOW = 5 * 60
_ASR_WINDOW = 2 * 60
_LLM_WINDOW = 10 * 60
_API_LIMIT_MS = 1000.0
_LLM_LIMIT_MS = 1500.0
_DISK_LIMIT = 0.80
_DAY = 86400.0


async def record_http(redis: Redis, duration_ms: float, now: datetime) -> None:
    await _remember(redis, _HTTP, f"{duration_ms:.3f}", now, keep_sec=_API_WINDOW)


async def record_asr(redis: Redis, *, failed: bool, now: datetime) -> None:
    await _remember(redis, _ASR, "err" if failed else "ok", now, keep_sec=_ASR_WINDOW)


async def record_llm(redis: Redis, first_token_ms: float, now: datetime) -> None:
    await _remember(redis, _LLM, f"{first_token_ms:.3f}", now, keep_sec=_LLM_WINDOW)


async def record_voice(
    redis: Redis,
    *,
    asr_attempts: int,
    asr_errors: int,
    llm_first_token_ms: float | None,
    now: datetime,
) -> None:
    errors = min(asr_errors, asr_attempts)
    for _ in range(errors):
        await record_asr(redis, failed=True, now=now)
    for _ in range(asr_attempts - errors):
        await record_asr(redis, failed=False, now=now)
    if llm_first_token_ms is not None:
        await record_llm(redis, llm_first_token_ms, now)


async def raise_ops_alerts(
    session: AsyncSession,
    redis: Redis,
    *,
    now: datetime,
    disk_ratio: float | None = None,
    cert_not_after: datetime | None = None,
) -> int:
    """Write one alert per breached rule. A repeated sweep does not write the same notice again."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    created = 0
    try:
        http = await _numbers(redis, _HTTP, now, _API_WINDOW)
        if len(http) >= _MIN_SAMPLE and _p95(http) > _API_LIMIT_MS:
            created += await _once(session, "api_p95", "接口 P95 超过 1 秒", "warning")
        asr = await _payloads(redis, _ASR, now, _ASR_WINDOW)
        errors = sum(1 for item in asr if item == "err")
        if len(asr) >= _MIN_SAMPLE and errors * 10 > len(asr):
            created += await _once(session, "asr_error_rate", "语音识别错误率超过 0.10", "warning")
        llm = await _numbers(redis, _LLM, now, _LLM_WINDOW)
        if len(llm) >= _MIN_SAMPLE and _p95(llm) > _LLM_LIMIT_MS:
            created += await _once(session, "llm_first_token_p95", "模型首字 P95 超过 1500 毫秒", "warning")
    except RedisError:
        pass
    ratio = disk_used_ratio() if disk_ratio is None else disk_ratio
    if ratio is not None and ratio > _DISK_LIMIT:
        created += await _once(session, "disk_high", "磁盘使用率超过 0.80", "warning")
    created += await _cert_alert(session, now, cert_not_after)
    return created


def disk_used_ratio() -> float | None:
    path = Path(os.environ.get("RECORDINGS_DIR", "tmp/recordings")).resolve()
    target = path if path.exists() else path.parent
    if not target.exists():
        target = Path("/")
    usage = shutil.disk_usage(target)
    if usage.total <= 0:
        return None
    return usage.used / usage.total


def read_cert_not_after(path: Path) -> datetime:
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    return cert.not_valid_after_utc


async def _cert_alert(session: AsyncSession, now: datetime, supplied: datetime | None) -> int:
    if supplied is not None:
        return await _cert_window(session, now, supplied)
    raw = os.environ.get("TLS_CERT_FILE", "").strip()
    if not raw:
        return 0
    path = Path(raw)
    if not path.is_file():
        return await _once(session, "cert_unreadable", "证书文件无法读取", "warning")
    try:
        expires = read_cert_not_after(path)
    except ValueError:
        return await _once(session, "cert_unreadable", "证书文件无法读取", "warning")
    return await _cert_window(session, now, expires)


async def _cert_window(session: AsyncSession, now: datetime, expires: datetime) -> int:
    remaining = (expires - now).total_seconds()
    if remaining <= 0:
        return await _once(session, "cert_expired", "证书已到期", "critical")
    if remaining < _DAY:
        return await _once(session, "cert_expire_1", "证书将在 1 天内到期", "critical")
    if remaining < 7 * _DAY:
        return await _once(session, "cert_expire_7", "证书将在 7 天内到期", "warning")
    if remaining < 30 * _DAY:
        return await _once(session, "cert_expire_30", "证书将在 30 天内到期", "warning")
    return 0


async def _remember(redis: Redis, key: str, payload: str, now: datetime, *, keep_sec: int) -> None:
    try:
        score = now.timestamp()
        await redis.zadd(key, {f"{uuid.uuid4().hex}:{payload}": score})
        await redis.zremrangebyscore(key, "-inf", score - keep_sec)
    except RedisError:
        return


async def _payloads(redis: Redis, key: str, now: datetime, window_sec: int) -> list[str]:
    start = now.timestamp() - window_sec
    members = await redis.zrangebyscore(key, start, now.timestamp())
    return [str(member).split(":", 1)[1] for member in members if ":" in str(member)]


async def _numbers(redis: Redis, key: str, now: datetime, window_sec: int) -> list[float]:
    values: list[float] = []
    for payload in await _payloads(redis, key, now, window_sec):
        values.append(float(payload))
    return values


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


async def _once(session: AsyncSession, code: str, message: str, severity: str) -> int:
    found = await session.scalar(
        select(Alert.id).where(Alert.tenant_id.is_(None), Alert.code == code, Alert.message == message).limit(1)
    )
    if found is not None:
        return 0
    session.add(Alert(tenant_id=None, severity=severity, code=code, message=message))
    await session.flush()
    return 1


def cert_deadline(now: datetime, *, days: float) -> datetime:
    return now + timedelta(seconds=days * _DAY)
