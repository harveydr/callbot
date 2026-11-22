"""One call's playback state. Barge-in stops the assistant channel."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Playback:
    playing: bool = True
    barged_in: bool = False
    dropped_assistant_frames: int = 0
    turns: list[dict[str, str]] = field(default_factory=list)

    def speak(self, text: str) -> None:
        self.turns.append({"speaker": "assistant", "channel": "assistant", "text": text})

    def hear(self, text: str) -> None:
        if self.playing:
            self.playing = False
            self.barged_in = True
        self.turns.append({"speaker": "caller", "channel": "caller", "text": text})

    def drop_assistant(self) -> None:
        self.dropped_assistant_frames += 1
