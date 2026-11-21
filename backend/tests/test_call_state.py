"""Call status only moves along the published edges."""

from __future__ import annotations

import uuid

from app.models import Call
from app.services.call_state import IllegalCallTransition, move_call, reach


def _call(status: str = "queued") -> Call:
    return Call(
        tenant_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        contact_id=uuid.uuid4(),
        call_uuid=uuid.uuid4(),
        template_version=1,
        status=status,
    )


def test_a_completed_call_walks_the_answered_path() -> None:
    call = _call()
    reach(call, "completed")
    assert call.status == "completed"


def test_transfer_failure_returns_to_talking_and_cannot_jump_to_completed() -> None:
    call = _call()
    reach(call, "transferring")
    move_call(call, "transfer_failed")
    try:
        move_call(call, "completed")
    except IllegalCallTransition as exc:
        assert exc.current == "transfer_failed"
        assert exc.target == "completed"
    else:
        raise AssertionError("transfer_failed must not complete directly")
    assert call.status == "transfer_failed"
    move_call(call, "talking")
    move_call(call, "completed")
    assert call.status == "completed"


def test_an_outcome_off_the_path_is_refused() -> None:
    call = _call("ringing")
    try:
        reach(call, "blocked_compliance")
    except IllegalCallTransition as exc:
        assert (exc.current, exc.target) == ("ringing", "blocked_compliance")
    else:
        raise AssertionError("a ringing call cannot become a compliance block")
    assert call.status == "ringing"
