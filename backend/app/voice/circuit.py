"""Vendor circuit shared by ASR, TTS, and LLM calls in this process.

A 429 opens immediately. Other failures open after 5 in a row, or when more than half of the
calls in the last 30 seconds failed and at least 5 calls were counted. An open circuit tries one
request after 30 seconds.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

import redis

logger = logging.getLogger(__name__)

_OPEN_SECONDS = 30.0
_WINDOW_SECONDS = 30.0
_MIN_SAMPLE = 5
_CONSECUTIVE = 5
_REGISTRY: dict[str, "VendorCircuit"] = {}
_T = TypeVar("_T")


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

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self._state,
            "reason": self._reason,
            "opened_at": self._opened_at,
            "consecutive": self._consecutive,
            "probe_used": self._probe_used,
            "samples": [[stamp, failed] for stamp, failed in self._samples],
            "alerts": [[code, reason] for code, reason in self._alerts],
        }

    def restore(self, payload: dict[str, object]) -> None:
        self._state = str(payload.get("state") or "closed")
        self._reason = str(payload.get("reason") or "")
        opened = payload.get("opened_at")
        self._opened_at = float(opened) if isinstance(opened, (int, float)) else 0.0
        consecutive = payload.get("consecutive")
        self._consecutive = consecutive if isinstance(consecutive, int) and not isinstance(consecutive, bool) else 0
        self._probe_used = bool(payload.get("probe_used"))
        samples = payload.get("samples")
        restored: list[tuple[float, bool]] = []
        if isinstance(samples, list):
            for item in samples:
                if isinstance(item, list) and len(item) == 2 and isinstance(item[0], (int, float)):
                    restored.append((float(item[0]), bool(item[1])))
        self._samples = restored
        alerts = payload.get("alerts")
        pending: list[tuple[str, str]] = []
        if isinstance(alerts, list):
            for item in alerts:
                if isinstance(item, list) and len(item) == 2:
                    pending.append((str(item[0]), str(item[1])))
        self._alerts = pending


def vendor_circuit(name: str) -> VendorCircuit:
    circuit = _REGISTRY.get(name)
    if circuit is None:
        circuit = VendorCircuit()
        _REGISTRY[name] = circuit
    return circuit


def reset_vendor_circuits() -> None:
    _REGISTRY.clear()


class CircuitPort(Protocol):
    @property
    def open(self) -> bool: ...

    def allow_vendor(self) -> bool: ...

    def observe(self, status: int) -> None: ...

    def observe_success(self) -> None: ...

    def drain_alerts(self) -> list[tuple[str, str]]: ...


class SharedVendorCircuit:
    """One named breaker for every worker. The clock is wall time so processes agree."""

    def __init__(self, redis_url: str, name: str, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._name = name
        self._data = f"vendor_circuit:{name}"
        self._lock = f"vendor_circuit:{name}:lock"
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def close(self) -> None:
        self._client.close()  # type: ignore[no-untyped-call]

    def reset(self) -> None:
        self._client.delete(self._data, self._lock)

    @property
    def open(self) -> bool:
        raw = self._client.get(self._data)
        if not isinstance(raw, str) or not raw:
            return False
        circuit = VendorCircuit(clock=self._clock)
        circuit.restore(_payload(raw))
        return circuit.open

    def allow_vendor(self) -> bool:
        def mutate(circuit: VendorCircuit) -> bool:
            return circuit.allow_vendor()

        return self._with_state(mutate)

    def observe(self, status: int) -> None:
        def mutate(circuit: VendorCircuit) -> None:
            circuit.observe(status)

        self._with_state(mutate)

    def observe_success(self) -> None:
        def mutate(circuit: VendorCircuit) -> None:
            circuit.observe_success()

        self._with_state(mutate)

    def drain_alerts(self) -> list[tuple[str, str]]:
        def mutate(circuit: VendorCircuit) -> list[tuple[str, str]]:
            return circuit.drain_alerts()

        return self._with_state(mutate)

    def _with_state(self, mutate: Callable[[VendorCircuit], _T]) -> _T:
        acquired = False
        for _ in range(20):
            if self._client.set(self._lock, "1", nx=True, ex=2):
                acquired = True
                break
            time.sleep(0.01)
        if not acquired:
            logger.warning("vendor circuit lock busy %s", self._name)
            return mutate(VendorCircuit(clock=self._clock))
        try:
            circuit = VendorCircuit(clock=self._clock)
            raw = self._client.get(self._data)
            if isinstance(raw, str) and raw:
                circuit.restore(_payload(raw))
            result = mutate(circuit)
            self._client.set(self._data, json.dumps(circuit.snapshot()))
            return result
        finally:
            self._client.delete(self._lock)


def shared_voice_mode(redis_url: str) -> str:
    """tts open pauses new calls. llm open keeps calls on the script tree. asr stays a local fallback."""
    tts = SharedVendorCircuit(redis_url, "tts")
    llm = SharedVendorCircuit(redis_url, "llm")
    try:
        if tts.open:
            return "paused_vendor"
        if llm.open:
            return "script"
        return "normal"
    except redis.RedisError:
        return "normal"
    finally:
        tts.close()
        llm.close()


def _payload(raw: str) -> dict[str, object]:
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        return {}
    return {str(key): value for key, value in loaded.items()}
