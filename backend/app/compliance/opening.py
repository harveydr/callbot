"""Opening line is a template. The model is not allowed to rewrite it."""

from __future__ import annotations

AI_IDENTITY = "人工智能"
AI_ROLE = "语音助理"
RECORDING_NOTICE = "本次通话将被录音"


def render_opening(*, company: str, purpose: str) -> str:
    company_name = company.strip() or "本单位"
    purpose_text = purpose.strip() or "服务通知"
    return (
        f"您好，我是{company_name}的{AI_IDENTITY}{AI_ROLE}。"
        f"{RECORDING_NOTICE}。"
        f"本次来电是{purpose_text}。"
        "如果您不希望再接到电话，可以直接告诉我。"
    )


def opening_is_complete(text: str) -> bool:
    has_identity = "人工智能" in text or "AI" in text
    has_role = "助理" in text or "语音助手" in text
    has_recording = "录音" in text
    return has_identity and has_role and has_recording
