"""Energy VAD for the mock media path. Silence and dropped frames are not speech."""

from __future__ import annotations


class EnergyVad:
    def is_speech(self, pcm: bytes) -> bool:
        if not pcm or pcm.startswith(b"SILENCE"):
            return False
        return True
