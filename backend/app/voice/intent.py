"""Local intent labels. The vendor model is not required for this fallback."""

from __future__ import annotations

_REFUSE = ("别再打", "不要再打", "别打了", "不需要", "不要再联系")
_AGREE = ("方便", "可以", "好的", "同意", "没问题")
_VOICEMAIL = ("语音信箱", "嘀声后")


def classify_intent(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return "invalid"
    if any(word in cleaned for word in _VOICEMAIL):
        return "voicemail"
    if any(word in cleaned for word in _REFUSE):
        return "refuse"
    if any(word in cleaned for word in _AGREE):
        return "agree"
    return "inquiry"
