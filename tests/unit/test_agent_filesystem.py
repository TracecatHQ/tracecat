"""Tests for durable agent filesystem snapshot helpers."""

from __future__ import annotations

import io
import json
import shutil
import stat
import tarfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tracecat import config
from tracecat.agent.filesystem import (
    AgentFilesystemService,
    AgentFilesystemSnapshotLimitExceeded,
    create_work_dir_archive,
    extract_work_dir_archive,
    hydrate_agent_work_dir,
    persist_agent_work_dir,
)


def test_archive_round_trips_regular_files_and_empty_dirs(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "README.md").write_text("# Agent work\n")
    scripts_dir = work_dir / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (work_dir / "empty").mkdir()
    (work_dir / "link").symlink_to(work_dir / "README.md")

    archive_path = tmp_path / "snapshot.tar.gz"
    stats = create_work_dir_archive(
        work_dir,
        archive_path,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )

    restored_dir = tmp_path / "restored"
    extract_work_dir_archive(
        archive_path,
        restored_dir,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )

    assert stats.file_count == 2
    assert stats.uncompressed_size_bytes == len("# Agent work\n") + len("#!/bin/sh\n")
    assert stats.size_bytes == archive_path.stat().st_size
    assert len(stats.sha256) == 64
    assert (restored_dir / "README.md").read_text() == "# Agent work\n"
    assert (restored_dir / "scripts" / "run.sh").read_text() == "#!/bin/sh\n"
    assert (restored_dir / "empty").is_dir()
    assert not (restored_dir / "link").exists()
    assert stat.S_IMODE((restored_dir / "scripts" / "run.sh").stat().st_mode) == 0o755


def test_archive_enforces_snapshot_size_limit(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "large.txt").write_text("too large")

    with pytest.raises(AgentFilesystemSnapshotLimitExceeded, match="max uncompressed"):
        create_work_dir_archive(
            work_dir,
            tmp_path / "snapshot.tar.gz",
            max_uncompressed_bytes=4,
            max_file_count=10,
        )


def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "malicious.tar.gz"
    payload = b"escape"
    with tarfile.open(archive_path, mode="w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Unsafe path"):
        extract_work_dir_archive(
            archive_path,
            tmp_path / "restored",
            max_uncompressed_bytes=1024,
            max_file_count=10,
        )

    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.anyio
async def test_persist_and_hydrate_round_trip_through_blob_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)
    (work_dir / "notes.txt").write_text("durable state")
    uploaded: dict[tuple[str, str], bytes] = {}
    latest_snapshot: SimpleNamespace | None = None

    class FakeService:
        async def get_latest_snapshot_for_hydration(
            self,
            _session_id: uuid.UUID,
        ) -> SimpleNamespace | None:
            return latest_snapshot

        async def record_snapshot(
            self,
            *,
            session_id: uuid.UUID,
            bucket: str,
            key: str,
            stats: Any,
        ) -> SimpleNamespace:
            nonlocal latest_snapshot
            latest_snapshot = SimpleNamespace(
                id=uuid.uuid4(),
                session_id=session_id,
                bucket=bucket,
                key=key,
                sha256=stats.sha256,
                size_bytes=stats.size_bytes,
                uncompressed_size_bytes=stats.uncompressed_size_bytes,
                file_count=stats.file_count,
            )
            return latest_snapshot

    @asynccontextmanager
    async def fake_with_session(**_kwargs: Any):
        yield FakeService()

    async def fake_upload_file_from_path(
        path: Path,
        *,
        key: str,
        bucket: str,
        content_type: str | None = None,
    ) -> None:
        assert content_type == "application/gzip"
        uploaded[(bucket, key)] = path.read_bytes()

    async def fake_download_file_to_path(
        *,
        key: str,
        bucket: str,
        output_path: Path,
        max_bytes: int | None = None,
        expected_sha256: str | None = None,
    ) -> int:
        del max_bytes, expected_sha256
        content = uploaded[(bucket, key)]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        return len(content)

    monkeypatch.setattr(
        config,
        "TRACECAT__AGENT_FS_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_AGENT", "agent-bucket")
    monkeypatch.setattr(
        AgentFilesystemService,
        "with_session",
        staticmethod(fake_with_session),
    )
    monkeypatch.setattr(
        "tracecat.agent.filesystem.blob.upload_file_from_path",
        fake_upload_file_from_path,
    )
    monkeypatch.setattr(
        "tracecat.agent.filesystem.blob.download_file_to_path",
        fake_download_file_to_path,
    )

    snapshot = await persist_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        workspace_id=workspace_id,
        work_dir=work_dir,
    )

    assert snapshot is latest_snapshot
    assert latest_snapshot is not None
    assert uploaded.keys() == {("agent-bucket", latest_snapshot.key)}

    shutil.rmtree(work_dir)
    shutil.rmtree(tmp_path / "cache")
    (work_dir.parent / ".agent-fs-snapshot.json").unlink()

    hydrated = await hydrate_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        work_dir=work_dir,
    )

    assert hydrated is latest_snapshot
    assert (work_dir / "notes.txt").read_text() == "durable state"


@pytest.mark.anyio
async def test_persist_skips_initial_empty_work_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)

    @asynccontextmanager
    async def fail_with_session(**_kwargs: Any):
        raise AssertionError("empty initial work dir should not record a snapshot")
        yield

    async def fail_upload_file_from_path(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("empty initial work dir should not upload an archive")

    monkeypatch.setattr(
        config,
        "TRACECAT__AGENT_FS_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(
        AgentFilesystemService,
        "with_session",
        staticmethod(fail_with_session),
    )
    monkeypatch.setattr(
        "tracecat.agent.filesystem.blob.upload_file_from_path",
        fail_upload_file_from_path,
    )

    snapshot = await persist_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        workspace_id=workspace_id,
        work_dir=work_dir,
    )

    assert snapshot is None
    assert not (work_dir.parent / ".agent-fs-snapshot.json").exists()


@pytest.mark.anyio
async def test_persist_records_empty_work_dir_after_prior_snapshot_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)
    (work_dir.parent / ".agent-fs-snapshot.json").write_text(
        json.dumps({"snapshot_id": str(uuid.uuid4()), "sha256": "0" * 64})
    )
    uploaded: dict[tuple[str, str], bytes] = {}
    recorded_snapshot: SimpleNamespace | None = None

    class FakeService:
        async def record_snapshot(
            self,
            *,
            session_id: uuid.UUID,
            bucket: str,
            key: str,
            stats: Any,
        ) -> SimpleNamespace:
            nonlocal recorded_snapshot
            recorded_snapshot = SimpleNamespace(
                id=uuid.uuid4(),
                session_id=session_id,
                bucket=bucket,
                key=key,
                sha256=stats.sha256,
                size_bytes=stats.size_bytes,
                uncompressed_size_bytes=stats.uncompressed_size_bytes,
                file_count=stats.file_count,
            )
            return recorded_snapshot

    @asynccontextmanager
    async def fake_with_session(**_kwargs: Any):
        yield FakeService()

    async def fake_upload_file_from_path(
        path: Path,
        *,
        key: str,
        bucket: str,
        content_type: str | None = None,
    ) -> None:
        assert content_type == "application/gzip"
        uploaded[(bucket, key)] = path.read_bytes()

    monkeypatch.setattr(
        config,
        "TRACECAT__AGENT_FS_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_AGENT", "agent-bucket")
    monkeypatch.setattr(
        AgentFilesystemService,
        "with_session",
        staticmethod(fake_with_session),
    )
    monkeypatch.setattr(
        "tracecat.agent.filesystem.blob.upload_file_from_path",
        fake_upload_file_from_path,
    )

    snapshot = await persist_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        workspace_id=workspace_id,
        work_dir=work_dir,
    )

    assert snapshot is recorded_snapshot
    assert recorded_snapshot is not None
    assert recorded_snapshot.file_count == 0
    assert recorded_snapshot.uncompressed_size_bytes == 0
    assert uploaded.keys() == {("agent-bucket", recorded_snapshot.key)}
