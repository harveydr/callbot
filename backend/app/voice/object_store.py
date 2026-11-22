"""S3-compatible recording store. Unconfigured installs keep files on local disk.

A configured store receives the same labeled WAV that was written locally. A failed upload
leaves that file in a 4GB buffer for about two hours and a later flush retries it.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, cast

logger = logging.getLogger(__name__)

BUFFER_BYTES = 4 * 1024 * 1024 * 1024
BUFFER_AGE = timedelta(hours=2)
_NAMES = ("OSS_ENDPOINT", "OSS_BUCKET", "OSS_ACCESS_KEY", "OSS_SECRET_KEY")
_TENANT = re.compile(r"^[0-9a-fA-F-]{36}$")
_WAV = re.compile(r"^[0-9a-fA-F-]{36}\.wav$")


class StoreError(Exception):
    """The shared store refused a read, write, or delete. The message has no credentials."""


class ObjectStore(Protocol):
    def put(self, key: str, body: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class _Readable(Protocol):
    def read(self) -> bytes: ...


class _S3(Protocol):
    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...


@dataclass(frozen=True)
class BufferFlush:
    uploaded: tuple[str, ...]
    dropped_local: tuple[str, ...]
    overflow: tuple[str, ...]


def recordings_root() -> Path:
    return Path(os.environ.get("RECORDINGS_DIR", "tmp/recordings")).resolve()


def oss_mode() -> str:
    """off, incomplete, or on. Blank and placeholder values do not count as configured."""
    present = [value for value in (_value(name) for name in _NAMES) if value]
    if not present:
        return "off"
    if len(present) != len(_NAMES):
        return "incomplete"
    return "on"


def configured_store() -> ObjectStore | None:
    if oss_mode() != "on":
        return None
    endpoint, bucket, access_key, secret_key = (_value(name) for name in _NAMES)
    import boto3
    from botocore.client import Config

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return BotoStore(cast(_S3, client), bucket)


class BotoStore:
    def __init__(self, client: _S3, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, body: bytes) -> None:
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType="audio/wav")
        except Exception as exc:
            raise StoreError("object store write failed") from exc

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise StoreError("object store read failed") from exc
        body = response["Body"]
        read = getattr(body, "read", None)
        if not callable(read):
            raise StoreError("object store read failed")
        data = read()
        if not isinstance(data, (bytes, bytearray)):
            raise StoreError("object store read failed")
        return bytes(data)

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _gone(exc):
                return
            raise StoreError("object store delete failed") from exc


def publish_local(path: Path, key: str, store: ObjectStore) -> bool:
    """Upload the labeled file. A failure keeps the local copy and writes no marker."""
    try:
        store.put(key, path.read_bytes())
    except StoreError:
        logger.warning("recording stayed in the local buffer")
        return False
    path.with_name(path.name + ".uploaded").write_bytes(b"")
    return True


def flush_buffer(
    store: ObjectStore,
    *,
    now: datetime,
    root: Path | None = None,
    limit_bytes: int = BUFFER_BYTES,
    max_age: timedelta = BUFFER_AGE,
) -> BufferFlush:
    """Retry unmarked files, then drop local copies that are already stored."""
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    base = (root or recordings_root()).resolve()
    files = _wavs(base)
    uploaded: list[str] = []
    for item in files:
        if item.uploaded:
            continue
        if publish_local(item.path, item.key, store):
            item.uploaded = True
            uploaded.append(item.key)
    dropped = _trim(files, now=moment, limit_bytes=limit_bytes, max_age=max_age)
    pending = [item.key for item in files if item.path.is_file() and not _marked(item.path)]
    pending_bytes = sum(item.path.stat().st_size for item in files if item.path.is_file() and not _marked(item.path))
    overflow = tuple(pending) if pending_bytes > limit_bytes else ()
    return BufferFlush(uploaded=tuple(uploaded), dropped_local=tuple(dropped), overflow=overflow)


def _value(name: str) -> str:
    raw = os.environ.get(name, "").strip()
    if raw.startswith("replace-with-"):
        return ""
    return raw


def _gone(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    if not isinstance(error, dict):
        return False
    return error.get("Code") in {"NoSuchKey", "404", "NotFound"}


@dataclass
class _Local:
    path: Path
    key: str
    uploaded: bool


def _marked(path: Path) -> bool:
    return path.with_name(path.name + ".uploaded").is_file()


def _wavs(root: Path) -> list[_Local]:
    found: list[_Local] = []
    if not root.is_dir():
        return found
    for tenant_dir in root.iterdir():
        if not tenant_dir.is_dir() or not _TENANT.fullmatch(tenant_dir.name):
            continue
        resolved_tenant = tenant_dir.resolve()
        if resolved_tenant.parent != root:
            continue
        for path in resolved_tenant.iterdir():
            if not path.is_file() or not _WAV.fullmatch(path.name):
                continue
            resolved = path.resolve()
            if resolved.parent != resolved_tenant:
                continue
            found.append(_Local(resolved, f"recordings/{tenant_dir.name}/{path.name}", _marked(resolved)))
    return found


def _trim(files: list[_Local], *, now: datetime, limit_bytes: int, max_age: timedelta) -> list[str]:
    cutoff = now.timestamp() - max_age.total_seconds()
    dropped: list[str] = []
    for item in files:
        if not item.uploaded or not item.path.is_file():
            continue
        if item.path.stat().st_mtime > cutoff:
            continue
        _unlink(item)
        dropped.append(item.key)
    remaining = [item for item in files if item.uploaded and item.path.is_file()]
    remaining.sort(key=lambda item: item.path.stat().st_mtime)
    total = sum(item.path.stat().st_size for item in files if item.path.is_file())
    for item in remaining:
        if total <= limit_bytes:
            break
        total -= item.path.stat().st_size
        _unlink(item)
        dropped.append(item.key)
    return dropped


def _unlink(item: _Local) -> None:
    item.path.unlink(missing_ok=True)
    item.path.with_name(item.path.name + ".uploaded").unlink(missing_ok=True)
    item.uploaded = False
