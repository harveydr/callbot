"""Labeled recordings move to a shared store. The local disk is only a 4GB buffer."""

from __future__ import annotations

import base64
import io
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import set_bypass
from app.models import AuditLog, Call
from app.models.extra import Alert, Recording
from app.services.erasure import purge_closed_tenants
from app.services.recording_buffer import note_recording_buffer
from app.tasks import celery_app
from app.voice.object_store import BotoStore, StoreError, configured_store, flush_buffer, oss_mode
from app.voice.stereo import EXPLICIT_LABEL, label_meta, read_labels, recording_payload, write_recording
from tests.test_dispatch import NOW, _world

class MemoryStore:
    def __init__(self, *, fail_put: bool = False, fail_delete: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_put = fail_put
        self.fail_delete = fail_delete

    def put(self, key: str, body: bytes) -> None:
        if self.fail_put:
            raise StoreError("object store write failed")
        self.objects[key] = body

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise StoreError("object store read failed") from exc

    def delete(self, key: str) -> None:
        if self.fail_delete:
            raise StoreError("object store delete failed")
        self.objects.pop(key, None)


class _ClientError(Exception):
    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object:
        del ContentType
        self.objects[(Bucket, Key)] = Body
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:
        try:
            body = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise _ClientError("NoSuchKey") from exc
        return {"Body": io.BytesIO(body)}

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        if (Bucket, Key) not in self.objects:
            raise _ClientError("NoSuchKey")
        del self.objects[(Bucket, Key)]
        return {}


def test_boto_store_reads_and_deletes_the_same_bytes() -> None:
    client = _FakeS3()
    store = BotoStore(client, "echodesk-rec")
    store.put("recordings/a/b.wav", b"labeled-wav")
    assert store.get("recordings/a/b.wav") == b"labeled-wav"
    store.delete("recordings/a/b.wav")
    store.delete("recordings/a/b.wav")
    with pytest.raises(StoreError):
        store.get("recordings/a/b.wav")


def test_a_complete_oss_config_builds_a_boto_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss.example")
    monkeypatch.setenv("OSS_BUCKET", "echodesk-rec")
    monkeypatch.setenv("OSS_ACCESS_KEY", "test-access")
    monkeypatch.setenv("OSS_SECRET_KEY", "test-secret")
    store = configured_store()
    assert isinstance(store, BotoStore)


def test_blank_oss_settings_stay_local(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OSS_ENDPOINT", "OSS_BUCKET", "OSS_ACCESS_KEY", "OSS_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert oss_mode() == "off"
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss.example")
    assert oss_mode() == "incomplete"


def test_a_failed_upload_keeps_the_labeled_local_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    store = MemoryStore(fail_put=True)
    tenant_id = uuid.uuid4()
    call_uuid = uuid.uuid4()
    key = write_recording(tenant_id, call_uuid, [{"speaker": "caller", "text": "好的"}], label_meta(call_uuid, "PLATFORM"), store)
    path = tmp_path / str(tenant_id) / f"{call_uuid}.wav"
    assert key == f"recordings/{tenant_id}/{call_uuid}.wav"
    assert path.is_file()
    assert not path.with_name(path.name + ".uploaded").exists()
    assert store.objects == {}
    labels = read_labels(path.read_bytes())
    assert labels["explicit_label"] == EXPLICIT_LABEL


def test_playback_uses_the_stored_file_after_the_local_copy_is_removed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    store = MemoryStore()
    tenant_id = uuid.uuid4()
    call_uuid = uuid.uuid4()
    write_recording(tenant_id, call_uuid, [{"speaker": "assistant", "text": "开场"}], label_meta(call_uuid, ""), store)
    path = tmp_path / str(tenant_id) / f"{call_uuid}.wav"
    stored = store.objects[f"recordings/{tenant_id}/{call_uuid}.wav"]
    assert stored == path.read_bytes()
    path.unlink()
    payload = recording_payload(tenant_id, call_uuid, store)
    assert payload is not None
    played = base64.b64decode(str(payload["media_base64"]))
    assert played == stored
    labels = read_labels(played)
    assert labels["explicit_label"] == EXPLICIT_LABEL
    aigc = labels["AIGC"]
    assert isinstance(aigc, dict) and aigc["ProduceID"] == str(call_uuid)


def test_flush_retries_then_drops_only_stored_local_copies(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    store = MemoryStore()
    tenant_id = uuid.uuid4()
    fresh = uuid.uuid4()
    old = uuid.uuid4()
    stuck = uuid.uuid4()
    write_recording(tenant_id, fresh, [], label_meta(fresh, ""), store)
    write_recording(tenant_id, old, [], label_meta(old, ""), store)
    write_recording(tenant_id, stuck, [], label_meta(stuck, ""), MemoryStore(fail_put=True))
    old_path = tmp_path / str(tenant_id) / f"{old}.wav"
    stuck_path = tmp_path / str(tenant_id) / f"{stuck}.wav"
    fresh_path = tmp_path / str(tenant_id) / f"{fresh}.wav"
    moment = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)
    fresh_stamp = (moment - timedelta(minutes=20)).timestamp()
    os.utime(fresh_path, (fresh_stamp, fresh_stamp))
    os.utime(fresh_path.with_name(fresh_path.name + ".uploaded"), (fresh_stamp, fresh_stamp))
    os.utime(stuck_path, (fresh_stamp, fresh_stamp))
    old_stamp = (moment - timedelta(hours=3)).timestamp()
    os.utime(old_path, (old_stamp, old_stamp))
    os.utime(old_path.with_name(old_path.name + ".uploaded"), (old_stamp, old_stamp))
    result = flush_buffer(store, now=moment, root=tmp_path, limit_bytes=10**9)
    assert f"recordings/{tenant_id}/{stuck}.wav" in result.uploaded
    assert f"recordings/{tenant_id}/{old}.wav" in result.dropped_local
    assert not old_path.exists()
    assert fresh_path.is_file()
    assert stuck_path.is_file() and stuck_path.with_name(stuck_path.name + ".uploaded").is_file()
    assert store.get(f"recordings/{tenant_id}/{old}.wav")
    assert result.overflow == ()


def test_a_full_buffer_keeps_files_the_store_does_not_have(tmp_path) -> None:
    tenant_id = uuid.uuid4()
    folder = tmp_path / str(tenant_id)
    folder.mkdir()
    first = folder / f"{uuid.uuid4()}.wav"
    second = folder / f"{uuid.uuid4()}.wav"
    first.write_bytes(b"a" * 6)
    second.write_bytes(b"b" * 6)
    store = MemoryStore(fail_put=True)
    moment = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)
    result = flush_buffer(store, now=moment, root=tmp_path, limit_bytes=10)
    assert result.uploaded == ()
    assert result.dropped_local == ()
    assert first.is_file() and second.is_file()
    assert set(result.overflow) == {f"recordings/{tenant_id}/{first.name}", f"recordings/{tenant_id}/{second.name}"}


def test_stored_files_are_trimmed_when_the_buffer_is_over_the_limit(tmp_path) -> None:
    tenant_id = uuid.uuid4()
    folder = tmp_path / str(tenant_id)
    folder.mkdir()
    older = folder / f"{uuid.uuid4()}.wav"
    newer = folder / f"{uuid.uuid4()}.wav"
    older.write_bytes(b"a" * 8)
    newer.write_bytes(b"b" * 8)
    older.with_name(older.name + ".uploaded").write_bytes(b"")
    newer.with_name(newer.name + ".uploaded").write_bytes(b"")
    moment = datetime(2026, 11, 22, 2, 0, tzinfo=timezone.utc)
    os.utime(older, (moment.timestamp() - 1800, moment.timestamp() - 1800))
    os.utime(newer, (moment.timestamp() - 60, moment.timestamp() - 60))
    store = MemoryStore()
    store.objects[f"recordings/{tenant_id}/{older.name}"] = older.read_bytes()
    store.objects[f"recordings/{tenant_id}/{newer.name}"] = newer.read_bytes()
    result = flush_buffer(store, now=moment, root=tmp_path, limit_bytes=10)
    assert not older.exists()
    assert newer.is_file()
    assert f"recordings/{tenant_id}/{older.name}" in result.dropped_local


def test_the_flush_task_is_scheduled_every_minute() -> None:
    entry = celery_app.conf.beat_schedule["flush-recording-buffer"]
    assert entry["task"] == "flush_recording_buffer"
    assert entry["schedule"] == timedelta(minutes=1)
    assert celery_app.conf.task_annotations["flush_recording_buffer"]["time_limit"] == 120
    assert celery_app.conf.task_routes["flush_recording_buffer"]["queue"] == "io_heavy"


async def test_buffer_alerts_are_recorded_once(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await set_bypass(session)
        first = await note_recording_buffer(session, incomplete=True, overflow=2)
        second = await note_recording_buffer(session, incomplete=True, overflow=2)
        rows = (await session.scalars(select(Alert).where(Alert.tenant_id.is_(None)))).all()
        assert first == 2
        assert second == 0
        assert {row.code for row in rows} == {"recording_store_incomplete", "recording_buffer_full"}
        assert any("4GB" in row.message and "2" in row.message for row in rows)


async def test_erasure_keeps_the_row_until_the_shared_object_is_gone(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))
    store = MemoryStore(fail_delete=True)
    async with session_factory() as session:
        await set_bypass(session)
        tenant, _trunk, campaign, contact, _caller = await _world(session, "store-erase", phone="13800008121")
        call_uuid = uuid.uuid4()
        call = Call(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            call_uuid=call_uuid,
            status="completed",
            template_version=1,
            created_at=NOW - timedelta(days=40),
        )
        session.add(call)
        await session.flush()
        key = f"recordings/{tenant.id}/{call_uuid}.wav"
        session.add(
            Recording(
                tenant_id=tenant.id,
                call_id=call.id,
                object_key=key,
                aigc_label_meta={"AIGC": {"Label": "1"}},
                explicit_label=EXPLICIT_LABEL,
                retain_until=NOW - timedelta(days=1),
            )
        )
        session.add(
            AuditLog(
                tenant_id=tenant.id,
                action="tenant.close",
                target_type="tenant",
                target_id=str(tenant.id),
                detail={},
                created_at=NOW - timedelta(days=31),
            )
        )
        tenant.status = "closed"
        await session.commit()
        await set_bypass(session)
        tenant_id = tenant.id
        path = tmp_path / str(tenant_id) / f"{call_uuid}.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"labeled")
        store.objects[key] = b"labeled"
        blocked = await purge_closed_tenants(session, NOW, store)
        recording = await session.scalar(select(Recording).where(Recording.tenant_id == tenant_id))
        assert blocked == [str(tenant_id)]
        assert recording is not None and recording.object_key == key
        assert path.is_file()
        assert store.objects[key] == b"labeled"
        store.fail_delete = False
        released = await purge_closed_tenants(session, NOW, store)
        await session.flush()
        await session.refresh(recording)
        reports = (await session.scalars(select(AuditLog).where(AuditLog.action == "tenant.deletion_report"))).all()
        assert released == [str(tenant_id)]
        assert recording.object_key == f"purged/{key}"
        assert reports[-1].detail["recordings"] == [key]
        assert key not in store.objects
        assert not path.exists()
