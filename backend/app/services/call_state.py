"""Legal call transitions. A status that is not on an edge is refused."""

from __future__ import annotations

from app.models import Call

_EDGES: dict[str, frozenset[str]] = {
    "queued": frozenset({"dialing", "blocked_compliance", "cancelled"}),
    "dialing": frozenset({"ringing", "no_answer", "busy", "rejected", "failed"}),
    "ringing": frozenset({"answered", "no_answer", "busy", "rejected", "failed"}),
    "answered": frozenset({"talking"}),
    "talking": frozenset({"completed", "transferring", "interrupted"}),
    "transferring": frozenset({"completed", "transfer_failed"}),
    "transfer_failed": frozenset({"talking"}),
    "interrupted": frozenset({"talking", "completed"}),
}

_PATHS: dict[str, tuple[str, ...]] = {
    "blocked_compliance": ("queued", "blocked_compliance"),
    "cancelled": ("queued", "cancelled"),
    "no_answer": ("queued", "dialing", "no_answer"),
    "busy": ("queued", "dialing", "busy"),
    "rejected": ("queued", "dialing", "rejected"),
    "failed": ("queued", "dialing", "failed"),
    "completed": ("queued", "dialing", "ringing", "answered", "talking", "completed"),
    "transferring": ("queued", "dialing", "ringing", "answered", "talking", "transferring"),
    "interrupted": ("queued", "dialing", "ringing", "answered", "talking", "interrupted"),
}


class IllegalCallTransition(Exception):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"{current} -> {target}")


def move_call(call: Call, target: str) -> None:
    current = call.status
    if target not in _EDGES.get(current, frozenset()):
        raise IllegalCallTransition(current, target)
    call.status = target


def reach(call: Call, target: str) -> None:
    """Walk the one legal path from the current status to a dial outcome."""
    path = _PATHS.get(target)
    if path is None or call.status not in path:
        raise IllegalCallTransition(call.status, target)
    for step in path[path.index(call.status) + 1 :]:
        move_call(call, step)
