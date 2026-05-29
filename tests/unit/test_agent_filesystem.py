"""Tests for durable agent filesystem snapshot helpers."""

from __future__ import annotations

import io
import shutil
import stat
import tarfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from tracecat import config
from tracecat.agent.filesystem import (
    AgentFilesystemService,
    AgentFilesystemSnapshotLimitExceeded,
    AgentFilesystemSnapshotMetadata,
    compute_work_dir_state,
    create_work_dir_archive,
    extract_work_dir_archive,
    hydrate_agent_work_dir,
    persist_agent_work_dir,
)


def test_archive_round_trips_regular_files_and_empty_dirs(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "README.md").write_text("# Agent work\n")
    (work_dir / "README-hardlink.md").hardlink_to(work_dir / "README.md")
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

    assert stats.file_count == 3
    assert stats.uncompressed_size_bytes == (
        len("# Agent work\n") * 2 + len("#!/bin/sh\n")
    )
    assert stats.size_bytes == archive_path.stat().st_size
    assert len(stats.sha256) == 64
    assert (restored_dir / "README.md").read_text() == "# Agent work\n"
    assert (restored_dir / "README-hardlink.md").read_text() == "# Agent work\n"
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


def test_archive_hash_is_stable_for_same_work_dir_state(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "notes.txt").write_text("durable state")
    (work_dir / "empty").mkdir()

    first_archive = tmp_path / "first.tar.gz"
    second_archive = tmp_path / "second.tar.gz"
    first_stats = create_work_dir_archive(
        work_dir,
        first_archive,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )
    second_stats = create_work_dir_archive(
        work_dir,
        second_archive,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )

    assert first_stats.sha256 == second_stats.sha256
    assert first_archive.read_bytes() == second_archive.read_bytes()


def test_work_dir_state_hash_is_stable_and_tracks_file_boundaries(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "a.txt").write_text("hello")
    (work_dir / "b.txt").write_text("world")

    first_state = compute_work_dir_state(
        work_dir,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )
    second_state = compute_work_dir_state(
        work_dir,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )

    assert first_state == second_state
    assert len(first_state.state_hash) == 64

    (work_dir / "a.txt").write_text("helloworld")
    (work_dir / "b.txt").write_text("")
    changed_state = compute_work_dir_state(
        work_dir,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )

    assert changed_state.uncompressed_size_bytes == first_state.uncompressed_size_bytes
    assert changed_state.state_hash != first_state.state_hash


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
    current_snapshot: AgentFilesystemSnapshotMetadata | None = None
    expected_session_id = session_id

    class FakeService:
        async def get_current_snapshot(
            self,
            _session_id: uuid.UUID,
        ) -> AgentFilesystemSnapshotMetadata | None:
            return current_snapshot

        async def update_current_snapshot(
            self,
            *,
            session_id: uuid.UUID,
            snapshot: AgentFilesystemSnapshotMetadata,
        ) -> AgentFilesystemSnapshotMetadata:
            nonlocal current_snapshot
            assert session_id == expected_session_id
            current_snapshot = snapshot
            return current_snapshot

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

    assert snapshot is current_snapshot
    assert current_snapshot is not None
    assert uploaded.keys() == {("agent-bucket", current_snapshot.key)}

    shutil.rmtree(work_dir)
    shutil.rmtree(tmp_path / "cache")

    hydrated = await hydrate_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        work_dir=work_dir,
    )

    assert hydrated is current_snapshot
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

    class FakeService:
        async def get_current_snapshot(
            self,
            _session_id: uuid.UUID,
        ) -> None:
            return None

        async def update_current_snapshot(self, **_kwargs: Any) -> None:
            raise AssertionError("empty initial work dir should not record a snapshot")

    @asynccontextmanager
    async def fake_with_session(**_kwargs: Any):
        yield FakeService()

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
        staticmethod(fake_with_session),
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
async def test_persist_records_initial_directory_only_work_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    (work_dir / "empty").mkdir(parents=True)
    uploaded: dict[tuple[str, str], bytes] = {}
    recorded_snapshot: AgentFilesystemSnapshotMetadata | None = None
    expected_session_id = session_id

    class FakeService:
        async def get_current_snapshot(
            self,
            _session_id: uuid.UUID,
        ) -> None:
            return None

        async def update_current_snapshot(
            self,
            *,
            session_id: uuid.UUID,
            snapshot: AgentFilesystemSnapshotMetadata,
        ) -> AgentFilesystemSnapshotMetadata:
            nonlocal recorded_snapshot
            assert session_id == expected_session_id
            recorded_snapshot = snapshot
            return snapshot

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
    assert recorded_snapshot.dir_count == 1
    assert uploaded.keys() == {("agent-bucket", recorded_snapshot.key)}


@pytest.mark.anyio
async def test_persist_records_empty_work_dir_after_prior_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)
    uploaded: dict[tuple[str, str], bytes] = {}
    expected_session_id = session_id
    current_snapshot = AgentFilesystemSnapshotMetadata(
        bucket="agent-bucket",
        key="agent-fs/blobs/0.tar.gz",
        state_hash="0" * 64,
        sha256="0" * 64,
        size_bytes=1,
        uncompressed_size_bytes=1,
        file_count=1,
        dir_count=0,
        archive_format="tar.gz",
        compression="gzip",
        created_at="2026-05-28T00:00:00+00:00",
    )
    recorded_snapshot: AgentFilesystemSnapshotMetadata | None = None

    class FakeService:
        async def get_current_snapshot(
            self,
            _session_id: uuid.UUID,
        ) -> AgentFilesystemSnapshotMetadata:
            return current_snapshot

        async def update_current_snapshot(
            self,
            *,
            session_id: uuid.UUID,
            snapshot: AgentFilesystemSnapshotMetadata,
        ) -> AgentFilesystemSnapshotMetadata:
            nonlocal recorded_snapshot
            assert session_id == expected_session_id
            recorded_snapshot = snapshot
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


@pytest.mark.anyio
async def test_persist_skips_unchanged_state_without_local_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)
    (work_dir / "notes.txt").write_text("durable state")
    state_stats = compute_work_dir_state(
        work_dir,
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )
    current_snapshot = AgentFilesystemSnapshotMetadata(
        bucket="agent-bucket",
        key="agent-fs/blobs/0.tar.gz",
        state_hash=state_stats.state_hash,
        sha256="0" * 64,
        size_bytes=1,
        uncompressed_size_bytes=state_stats.uncompressed_size_bytes,
        file_count=state_stats.file_count,
        dir_count=state_stats.dir_count,
        archive_format="tar.gz",
        compression="gzip",
        created_at="2026-05-28T00:00:00+00:00",
    )

    class FakeService:
        async def get_current_snapshot(
            self,
            _session_id: uuid.UUID,
        ) -> AgentFilesystemSnapshotMetadata:
            return current_snapshot

        async def update_current_snapshot(self, **_kwargs: Any) -> None:
            raise AssertionError("unchanged state should not record a snapshot")

    @asynccontextmanager
    async def fake_with_session(**_kwargs: Any):
        yield FakeService()

    async def fail_upload_file_from_path(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("unchanged state should not upload an archive")

    monkeypatch.setattr(
        config,
        "TRACECAT__AGENT_FS_CACHE_DIR",
        str(tmp_path / "cache"),
    )
    monkeypatch.setattr(
        AgentFilesystemService,
        "with_session",
        staticmethod(fake_with_session),
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
