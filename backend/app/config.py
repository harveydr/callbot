"""Runtime settings. Names match .env.example and are not renamed."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    jwt_secret: str
    field_enc_key: bytes
    phone_pepper: bytes
    calling_enabled: bool
    feature_telesales: bool
    feature_collection: bool
    aigc_producer_code: str
    default_tz: str
    app_env: str
    cors_origins: tuple[str, ...]
    celery_enqueue: bool


def _key(name: str) -> bytes:
    raw = os.environ.get(name, "")
    if not raw or raw.startswith("replace-with-"):
        raise RuntimeError(f"{name} is not set")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"{name} must be standard base64") from exc
    if len(decoded) != 32:
        raise RuntimeError(f"{name} must decode to 32 bytes")
    return decoded


def load_settings() -> Settings:
    database_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    jwt_secret = os.environ.get("JWT_SECRET", "")
    pepper = os.environ.get("PHONE_PEPPER", "")
    if not database_url or not redis_url or not jwt_secret or not pepper:
        raise RuntimeError("DATABASE_URL, REDIS_URL, JWT_SECRET, and PHONE_PEPPER are required")
    if jwt_secret.startswith("replace-with-") or pepper.startswith("replace-with-"):
        raise RuntimeError("JWT_SECRET and PHONE_PEPPER must be replaced before startup")
    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        jwt_secret=jwt_secret,
        field_enc_key=_key("FIELD_ENC_KEY"),
        phone_pepper=pepper.encode("utf-8"),
        calling_enabled=_bool("CALLING_ENABLED", True),
        feature_telesales=_bool("FEATURE_TELESALES", False),
        feature_collection=_bool("FEATURE_COLLECTION", False),
        aigc_producer_code=os.environ.get("AIGC_PRODUCER_CODE", ""),
        default_tz=os.environ.get("DEFAULT_TZ", "Asia/Shanghai"),
        app_env=os.environ.get("APP_ENV", "production"),
        cors_origins=tuple(
            item.strip()
            for item in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
            if item.strip()
        ),
        celery_enqueue=_bool("CELERY_ENQUEUE", False),
    )
