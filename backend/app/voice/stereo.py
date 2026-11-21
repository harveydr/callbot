"""Stereo call recording. Left is the caller. Right is the assistant.

The file always starts with the explicit AIGC voice notice, and the same notice is stored in an AIGC chunk.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import cast

SAMPLE_RATE = 8000
CALLER_SAMPLE = 1000
ASSISTANT_SAMPLE = -2000
EXPLICIT_LABEL = "以下为人工智能生成合成语音"
BLOCK_SIZE = 8000


def label_meta(call_uuid: uuid.UUID, producer: str) -> dict[str, object]:
    code = producer.strip() or "UNREGISTERED"
    return {"AIGC": {"Label": "1", "ContentProducer": code, "ProduceID": str(call_uuid)}}


def stereo_wav(
    turns: list[dict[str, str]],
    *,
    aigc: dict[str, object] | None = None,
    explicit_label: str | None = None,
) -> bytes:
    spoken = turns
    if explicit_label:
        spoken = [{"speaker": "assistant", "channel": "assistant", "text": explicit_label}, *turns]
    left: list[int] = []
    right: list[int] = []
    for turn in spoken:
        channel = turn.get("channel") or turn.get("speaker") or ""
        if channel == "caller":
            left.extend([CALLER_SAMPLE] * BLOCK_SIZE)
            right.extend([0] * BLOCK_SIZE)
        elif channel == "assistant":
            left.extend([0] * BLOCK_SIZE)
            right.extend([ASSISTANT_SAMPLE] * BLOCK_SIZE)
    if not left:
        left = [0]
        right = [0]
    interleaved = bytearray()
    for caller, assistant in zip(left, right, strict=True):
        interleaved += struct.pack("<hh", caller, assistant)
    data = bytes(interleaved)
    fmt = struct.pack("<HHIIHH", 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16)
    chunks = _chunk(b"fmt ", fmt)
    if aigc is not None or explicit_label:
        body = json.dumps(
            {"AIGC": aigc or {}, "explicit_label": explicit_label or ""},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        chunks += _chunk(b"AIGC", body)
    chunks += _chunk(b"data", data)
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _chunk(tag: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) % 2 else b""
    return tag + struct.pack("<I", len(payload)) + payload + pad


def _iter_chunks(wav: bytes) -> Iterator[tuple[bytes, bytes]]:
    if len(wav) < 12 or wav[0:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("not a wav")
    offset = 12
    while offset + 8 <= len(wav):
        tag = wav[offset : offset + 4]
        size = struct.unpack_from("<I", wav, offset + 4)[0]
        start = offset + 8
        end = start + size
        if end > len(wav):
            raise ValueError("truncated wav")
        yield tag, wav[start:end]
        offset = end + (size % 2)


def split_channels(wav: bytes) -> tuple[list[int], list[int]]:
    fmt: bytes | None = None
    payload: bytes | None = None
    for tag, chunk in _iter_chunks(wav):
        if tag == b"fmt ":
            fmt = chunk
        elif tag == b"data":
            payload = chunk
    if fmt is None or payload is None or len(fmt) < 16:
        raise ValueError("not a wav")
    channels = struct.unpack_from("<H", fmt, 2)[0]
    rate = struct.unpack_from("<I", fmt, 4)[0]
    bits = struct.unpack_from("<H", fmt, 14)[0]
    if channels != 2 or rate != SAMPLE_RATE or bits != 16:
        raise ValueError("unexpected wav layout")
    left: list[int] = []
    right: list[int] = []
    for offset in range(0, len(payload), 4):
        caller, assistant = struct.unpack_from("<hh", payload, offset)
        left.append(caller)
        right.append(assistant)
    return left, right


def read_labels(wav: bytes) -> dict[str, object]:
    for tag, chunk in _iter_chunks(wav):
        if tag != b"AIGC":
            continue
        parsed = json.loads(chunk.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("aigc chunk is invalid")
        return parsed
    return {}


def _root() -> Path:
    return Path(os.environ.get("RECORDINGS_DIR", "tmp/recordings")).resolve()


def write_recording(tenant_id: uuid.UUID, call_uuid: uuid.UUID, turns: list[dict[str, str]], meta: dict[str, object]) -> str:
    raw = meta.get("AIGC")
    if not isinstance(raw, dict) or raw.get("Label") != "1" or raw.get("ProduceID") != str(call_uuid):
        raise ValueError("recording requires the AIGC label for this call")
    aigc = cast(dict[str, object], raw)
    root = _root()
    path = root / str(tenant_id) / f"{call_uuid}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_bytes(stereo_wav(turns, aigc=aigc, explicit_label=EXPLICIT_LABEL))
    temporary.replace(path)
    return f"recordings/{tenant_id}/{call_uuid}.wav"


def recording_payload(tenant_id: uuid.UUID, call_uuid: uuid.UUID) -> dict[str, object] | None:
    path = (_root() / str(tenant_id) / f"{call_uuid}.wav").resolve()
    if path.parent != (_root() / str(tenant_id)).resolve() or not path.is_file():
        return None
    raw = path.read_bytes()
    labels = read_labels(raw)
    aigc = labels.get("AIGC")
    return {
        "codec": "wav16",
        "sample_rate": SAMPLE_RATE,
        "channels": 2,
        "layout": "left=caller,right=assistant",
        "explicit_label": labels.get("explicit_label") or None,
        "aigc": aigc if isinstance(aigc, dict) else None,
        "media_base64": base64.b64encode(raw).decode("ascii"),
    }
