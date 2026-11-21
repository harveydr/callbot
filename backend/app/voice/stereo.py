"""Stereo call recording. Left is the caller. Right is the assistant."""

from __future__ import annotations

import base64
import os
import struct
import uuid
from pathlib import Path

SAMPLE_RATE = 8000
CALLER_SAMPLE = 1000
ASSISTANT_SAMPLE = -2000
_BLOCK = 8000


def stereo_wav(turns: list[dict[str, str]]) -> bytes:
    left: list[int] = []
    right: list[int] = []
    for turn in turns:
        channel = turn.get("channel") or turn.get("speaker") or ""
        if channel == "caller":
            left.extend([CALLER_SAMPLE] * _BLOCK)
            right.extend([0] * _BLOCK)
        elif channel == "assistant":
            left.extend([0] * _BLOCK)
            right.extend([ASSISTANT_SAMPLE] * _BLOCK)
    if not left:
        left = [0]
        right = [0]
    interleaved = bytearray()
    for caller, assistant in zip(left, right, strict=True):
        interleaved += struct.pack("<hh", caller, assistant)
    data = bytes(interleaved)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    fmt = struct.pack("<HHIIHH", 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16)
    return header + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data


def split_channels(wav: bytes) -> tuple[list[int], list[int]]:
    if wav[0:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("not a wav")
    channels = struct.unpack_from("<H", wav, 22)[0]
    rate = struct.unpack_from("<I", wav, 24)[0]
    bits = struct.unpack_from("<H", wav, 34)[0]
    if channels != 2 or rate != SAMPLE_RATE or bits != 16:
        raise ValueError("unexpected wav layout")
    data_at = wav.index(b"data")
    size = struct.unpack_from("<I", wav, data_at + 4)[0]
    payload = wav[data_at + 8 : data_at + 8 + size]
    left: list[int] = []
    right: list[int] = []
    for offset in range(0, len(payload), 4):
        caller, assistant = struct.unpack_from("<hh", payload, offset)
        left.append(caller)
        right.append(assistant)
    return left, right


def _root() -> Path:
    return Path(os.environ.get("RECORDINGS_DIR", "tmp/recordings")).resolve()


def write_recording(tenant_id: uuid.UUID, call_uuid: uuid.UUID, turns: list[dict[str, str]]) -> str:
    root = _root()
    path = root / str(tenant_id) / f"{call_uuid}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(stereo_wav(turns))
    return f"recordings/{tenant_id}/{call_uuid}.wav"


def recording_payload(tenant_id: uuid.UUID, call_uuid: uuid.UUID) -> dict[str, object] | None:
    path = (_root() / str(tenant_id) / f"{call_uuid}.wav").resolve()
    if path.parent != (_root() / str(tenant_id)).resolve() or not path.is_file():
        return None
    return {
        "codec": "wav16",
        "sample_rate": SAMPLE_RATE,
        "channels": 2,
        "layout": "left=caller,right=assistant",
        "media_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }
