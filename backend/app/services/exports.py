"""Report download links expire 15 minutes after they are issued."""

from __future__ import annotations

from datetime import datetime, timedelta


def export_expires_at(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    return now + timedelta(minutes=15)


def export_is_usable(*, expires_at: datetime, now: datetime) -> bool:
    if expires_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return expires_at > now
