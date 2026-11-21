from app.compliance.opening import opening_is_complete
from app.voice.mock_roundtrip import mock_roundtrip


async def test_mock_roundtrip_agrees_and_declares_ai() -> None:
    turn = await mock_roundtrip("PCM:现在方便".encode("utf-8"), company="回声", purpose="预约提醒")
    assert turn.asr_text == "现在方便"
    assert turn.intent == "agree"
    assert opening_is_complete(turn.reply_text)
    assert turn.audio.startswith(b"TTS:")
    assert turn.redline_hits == []


async def test_mock_roundtrip_silence_is_invalid() -> None:
    turn = await mock_roundtrip(b"")
    assert turn.asr_text == ""
    assert turn.intent == "invalid"
    assert "没有听清" in turn.reply_text
