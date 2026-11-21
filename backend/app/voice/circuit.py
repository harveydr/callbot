"""Vendor circuit shared by ASR calls in this process.

A 429 opens immediately. Other failures open after 5 in a row, or when more than half of the
calls in the last 30 seconds failed and at least 5 calls were counted. An open circuit tries one
request after 30 seconds.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

_OPEN_SECONDS = 30.0
_WINDOW_SECONDS = 30.0
_MIN_SAMPLE = 5
_CONSECUTIVE = 5
_REGISTRY: dict[str, "VendorCircuit"] = {}


class VendorCircuit:
    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._state = "closed"
        self._reason = ""
        self._opened_at = 0.0
        self._consecutive = 0
        self._probe_used = False
        self._samples: list[tuple[float, bool]] = []
        self._alerts: list[tuple[str, str]] = []

    @property
    def open(self) -> bool:
        return self._state == "open"

    @property
    def reason(self) -> str:
        return self._reason

    def allow_vendor(self) -> bool:
        self._promote_half_open(self._clock())
        if self._state == "closed":
            return True
        if self._state == "half_open" and not self._probe_used:
            self._probe_used = True
            return True
        return False

    def observe(self, status: int) -> None:
        self._record(failed=status >= 400, immediate=status == 429)

    def observe_success(self) -> None:
        self._record(failed=False, immediate=False)

    def drain_alerts(self) -> list[tuple[str, str]]:
        pending = self._alerts
        self._alerts = []
        return pending

    def _record(self, *, failed: bool, immediate: bool) -> None:
        now = self._clock()
        self._promote_half_open(now)
        if self._state == "open":
            return
        if self._state == "half_open":
            if failed:
                self._trip(now, "probe_failed")
            else:
                self._close("probe_ok")
            return
        self._samples.append((now, failed))
        self._samples = [item for item in self._samples if now - item[0] <= _WINDOW_SECONDS]
        self._consecutive = 0 if not failed else self._consecutive + 1
        if immediate or self._consecutive >= _CONSECUTIVE or self._rate_is_high():
            reason = "vendor_429" if immediate else "vendor_failures"
            self._trip(now, reason)

    def _rate_is_high(self) -> bool:
        if len(self._samples) < _MIN_SAMPLE:
            return False
        failures = sum(1 for _, failed in self._samples if failed)
        return failures / len(self._samples) > 0.5

    def _promote_half_open(self, now: float) -> None:
        if self._state == "open" and now - self._opened_at >= _OPEN_SECONDS:
            self._state = "half_open"
            self._probe_used = False
            self._note("circuit_half_open", "open_elapsed")

    def _trip(self, now: float, reason: str) -> None:
        self._state = "open"
        self._reason = reason
        self._opened_at = now
        self._consecutive = 0
        self._probe_used = False
        self._samples.clear()
        self._note("circuit_open", reason)

    def _close(self, reason: str) -> None:
        self._state = "closed"
        self._reason = ""
        self._consecutive = 0
        self._probe_used = False
        self._samples.clear()
        self._note("circuit_closed", reason)

    def _note(self, code: str, reason: str) -> None:
        logger.warning("vendor circuit %s %s", code, reason)
        self._alerts.append((code, reason))


def vendor_circuit(name: str) -> VendorCircuit:
    circuit = _REGISTRY.get(name)
    if circuit is None:
        circuit = VendorCircuit()
        _REGISTRY[name] = circuit
    return circuit


def reset_vendor_circuits() -> None:
    _REGISTRY.clear()
