"""Fake ASR, intent, and TTS. Tests must not call a real vendor."""

from __future__ import annotations

from dataclasses import dataclass

from app.compliance.opening import render_opening
from app.compliance.redline import scan_redline

_REFUSE = ("不需要", "别再打", "不要")
_AGREE = ("方便", "可以", "好的")


@dataclass(frozen=True)
class MockTurn:
    asr_text: str
    intent: str
    reply_text: str
    audio: bytes
    redline_hits: list[str]


def _asr(pcm: bytes) -> str:
    if not pcm:
        return ""
    try:
        text = pcm.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if text.startswith("PCM:"):
        return text.removeprefix("PCM:").strip()
    return ""


def _intent(text: str) -> str:
    if not text:
        return "invalid"
    if any(word in text for word in _REFUSE):
        return "refuse"
    if any(word in text for word in _AGREE):
        return "agree"
    return "inquiry"


async def mock_roundtrip(pcm: bytes, *, company: str = "示例公司", purpose: str = "预约提醒") -> MockTurn:
    heard = _asr(pcm)
    intent = _intent(heard)
    opening = render_opening(company=company, purpose=purpose)
    if intent == "invalid":
        reply = f"{opening}您好，我没有听清，方便的话请再说一次。"
    elif intent == "refuse":
        reply = f"{opening}好的，不再打扰您。"
    elif intent == "agree":
        reply = f"{opening}感谢您的时间，我确认一下事项。"
    else:
        reply = f"{opening}我记下了，让同事回电给您。"
    hits = scan_redline(reply)
    audio = f"TTS:{reply}".encode("utf-8")
    return MockTurn(heard, intent, reply, audio, hits)
