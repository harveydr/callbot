"""TTS adapter. Local runs return a labeled byte string instead of a vendor waveform."""

from __future__ import annotations


class MockTts:
    async def synthesize(self, text: str) -> bytes:
        return f"TTS:{text}".encode("utf-8")
