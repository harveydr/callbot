"""Call windows are evaluated in the tenant timezone, and can only be tightened."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

HARD_START = time(9, 0)
HARD_END = time(19, 0)
HARD_FORBID = (time(12, 0), time(14, 0))
HARD_FREQ_DAILY = 1
HARD_FREQ_7D = 3
HARD_FREQ_30D = 6


def parse_window(spec: str) -> tuple[time, time]:
    start_raw, end_raw = spec.split("-", 1)
    start_h, start_m = start_raw.split(":")
    end_h, end_m = end_raw.split(":")
    return time(int(start_h), int(start_m)), time(int(end_h), int(end_m))


def _in_span(value: time, start: time, end: time) -> bool:
    return start <= value < end


def window_block_reason(now: datetime, *, timezone_name: str, call_window: str) -> str | None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    local = now.astimezone(ZoneInfo(timezone_name))
    local_time = local.timetz().replace(tzinfo=None)
    tenant_start, tenant_end = parse_window(call_window)
    # Intersection with the hard window. A wider tenant window is ignored.
    start = max(tenant_start, HARD_START)
    end = min(tenant_end, HARD_END)
    if start >= end or not _in_span(local_time, start, end):
        return "outside_call_window"
    forbid_start, forbid_end = HARD_FORBID
    if _in_span(local_time, forbid_start, forbid_end):
        return "forbid_lunch_window"
    return None


def effective_freq_daily(requested: int) -> int:
    return min(requested, HARD_FREQ_DAILY)


def effective_freq_7d(requested: int) -> int:
    return min(requested, HARD_FREQ_7D)


def effective_freq_30d(requested: int) -> int:
    return min(requested, HARD_FREQ_30D)


def local_period_start(now: datetime, *, timezone_name: str, days: int) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)
    start_day = local.date() - timedelta(days=days - 1)
    start_local = datetime.combine(start_day, time.min, tzinfo=zone)
    return start_local.astimezone(ZoneInfo("UTC"))
