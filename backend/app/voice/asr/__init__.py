"""ASR adapter. The default implementation decodes the test PCM envelope and never calls a vendor."""

from __future__ import annotations


class VendorRejected(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"vendor status {status}")


def decode_pcm(pcm: bytes) -> str:
    if not pcm:
        return ""
    try:
        text = pcm.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if text.startswith("PCM:"):
        return text.removeprefix("PCM:").strip()
    return ""


class MockAsr:
    def __init__(self, *, status: int | None = None) -> None:
        self.status = status
        self.calls = 0

    async def transcribe(self, pcm: bytes) -> str:
        self.calls += 1
        if self.status is not None:
            raise VendorRejected(self.status)
        return decode_pcm(pcm)
