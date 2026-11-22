"""TTS adapter. Local runs return a labeled byte string instead of a vendor waveform."""

from __future__ import annotations

from app.voice.asr import VendorRejected


class MockTts:
    def __init__(self, *, status: int | None = None) -> None:
        self.status = status
        self.calls = 0

    async def synthesize(self, text: str) -> bytes:
        self.calls += 1
        if self.status is not None:
            raise VendorRejected(self.status)
        return f"TTS:{text}".encode("utf-8")
