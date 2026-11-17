"""Tests for durable agent filesystem snapshot helpers."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import tarfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from tracecat import config
from tracecat.agent.filesystem import (
    AgentFilesystemService,
    AgentFilesystemSnapshotLimitExceeded,
    AgentFilesystemSnapshotMetadata,
    _extract_archive_to_work_dir,
    compute_work_dir_state,
    create_work_dir_archive,
    extract_work_dir_archive,
    hydrate_agent_work_dir,
    persist_agent_work_dir,
)

_LIMITS: dict[str, int] = {"max_uncompressed_bytes": 1024, "max_file_count": 10}


def _snapshot(**overrides: Any) -> AgentFilesystemSnapshotMetadata:
    values: dict[str, Any] = {
        "bucket": "agent-bucket",
        "key": "agent-fs/blobs/0.tar.gz",
        "state_hash": "0" * 64,
        "sha256": "0" * 64,
        "size_bytes": 1,
        "uncompressed_size_bytes": 1,
        "file_count": 1,
        "dir_count": 0,
        "archive_format": "tar.gz",
        "compression": "gzip",
        "created_at": "2026-05-28T00:00:00+00:00",
    }
    return AgentFilesystemSnapshotMetadata(**(values | overrides))


def _snapshot_of(work_dir: Path) -> AgentFilesystemSnapshotMetadata:
    state = compute_work_dir_state(work_dir, **_LIMITS)
    return _snapshot(
        state_hash=state.state_hash,
        uncompressed_size_bytes=state.uncompressed_size_bytes,
        file_count=state.file_count,
        dir_count=state.dir_count,
    )


class _FakeSnapshotService:
    def __init__(
        self,
        session_id: uuid.UUID,
        current: AgentFilesystemSnapshotMetadata | None = None,
        *,
        allow_update: bool = True,
    ) -> None:
        self.session_id = session_id
        self.current = current
        self.recorded: AgentFilesystemSnapshotMetadata | None = None
        self.allow_update = allow_update

    async def get_current_snapshot(
        self, requested_session_id: uuid.UUID
    ) -> AgentFilesystemSnapshotMetadata | None:
        assert requested_session_id == self.session_id
        return self.current

    async def update_current_snapshot(
        self,
        *,
        session_id: uuid.UUID,
        snapshot: AgentFilesystemSnapshotMetadata,
    ) -> AgentFilesystemSnapshotMetadata:
        assert self.allow_update, "this work dir state should not record a snapshot"
        assert session_id == self.session_id
        self.recorded = self.current = snapshot
        return snapshot


def _patch_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    service: _FakeSnapshotService,
    *,
    uploaded: dict[tuple[str, str], bytes] | None = None,
    download: Callable[..., Awaitable[int]] | None = None,
) -> None:
    """Patch the snapshot service and blob storage; ``uploaded=None`` forbids uploads."""

    @asynccontextmanager
    async def fake_with_session(**_kwargs: Any) -> AsyncIterator[_FakeSnapshotService]:
        yield service

    async def fake_upload_file_from_path(
        path: Path,
        *,
        key: str,
        bucket: str,
        content_type: str | None = None,
    ) -> None:
        assert uploaded is not None, "this work dir state should not upload an archive"
        assert content_type == "application/gzip"
        uploaded[(bucket, key)] = path.read_bytes()

    async def fail_download_file_to_path(*_args: Any, **_kwargs: Any) -> int:
        raise AssertionError("this work dir state should not download a snapshot")

    monkeypatch.setattr(config, "TRACECAT__AGENT_FS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_AGENT", "agent-bucket")
    monkeypatch.setattr(
        AgentFilesystemService, "with_session", staticmethod(fake_with_session)
    )
    monkeypatch.setattr(
        "tracecat.agent.filesystem.blob.upload_file_from_path",
        fake_upload_file_from_path,
    )
    monkeypatch.setattr(
        "tracecat.agent.filesystem.blob.download_file_to_path",
        download or fail_download_file_to_path,
    )


def _failing_walk(restricted_dir: Path) -> Callable[..., Iterator[Any]]:
    def failing_walk(
        top: Path | str,
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        del topdown, followlinks
        assert onerror is not None
        onerror(PermissionError(13, "permission denied", str(restricted_dir)))
        return iter(((str(top), [], []),))

    return failing_walk


def test_snapshot_metadata_round_trips_strict_json_dict() -> None:
    raw_snapshot = {
        "bucket": "agent-bucket",
        "key": "agent-fs/blobs/current.tar.gz",
        "state_hash": "1" * 64,
        "sha256": "2" * 64,
        "size_bytes": 10,
        "uncompressed_size_bytes": 20,
        "file_count": 1,
        "dir_count": 2,
        "archive_format": "tar.gz",
        "compression": "gzip",
        "created_at": "2026-05-28T00:00:00+00:00",
    }

    snapshot = AgentFilesystemSnapshotMetadata.from_raw(raw_snapshot)

    assert snapshot is not None
    assert snapshot.to_dict() == raw_snapshot
    assert (
        AgentFilesystemSnapshotMetadata.from_raw({**raw_snapshot, "size_bytes": "10"})
        is None
    )
    assert (
        AgentFilesystemSnapshotMetadata.from_raw({**raw_snapshot, "extra": "field"})
        is None
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


@pytest.mark.parametrize("operation", ["state", "archive", "extract"])
def test_entry_limit_counts_directories(tmp_path: Path, operation: str) -> None:
    work_dir = tmp_path / "work"
    (work_dir / "one").mkdir(parents=True)
    (work_dir / "two").mkdir()
    archive_path = tmp_path / "snapshot.tar.gz"
    destination = tmp_path / "restored"
    limits = {"max_uncompressed_bytes": 1024, "max_file_count": 1}
    if operation == "extract":
        with tarfile.open(archive_path, mode="w:gz") as tar:
            for name in ("one", "two"):
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)

    with pytest.raises(AgentFilesystemSnapshotLimitExceeded, match="max entry count"):
        if operation == "state":
            compute_work_dir_state(work_dir, **limits)
        elif operation == "archive":
            create_work_dir_archive(work_dir, archive_path, **limits)
        else:
            extract_work_dir_archive(archive_path, destination, **limits)

    if operation == "archive":
        assert not archive_path.exists()
        assert not archive_path.with_name(f"{archive_path.name}.part").exists()
    if operation == "extract":
        assert not destination.exists()


@pytest.mark.parametrize("operation", ["state", "archive"])
def test_work_dir_traversal_fails_on_walk_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    archive_path = tmp_path / "snapshot.tar.gz"
    monkeypatch.setattr(
        "tracecat.agent.filesystem.os.walk", _failing_walk(work_dir / "restricted")
    )

    with pytest.raises(OSError, match="Failed to traverse"):
        if operation == "state":
            compute_work_dir_state(work_dir, **_LIMITS)
        else:
            create_work_dir_archive(work_dir, archive_path, **_LIMITS)

    assert not archive_path.exists()
    assert not archive_path.with_name(f"{archive_path.name}.part").exists()


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


def test_archive_does_not_follow_file_replaced_by_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    victim = work_dir / "victim.txt"
    victim.write_text("safe")
    secret = tmp_path / "secret.txt"
    secret.write_text("outside work dir")
    real_open = os.open

    def racing_open(path: os.PathLike[str] | str, flags: int, mode: int = 0o777) -> int:
        if Path(path) == victim and victim.exists():
            victim.unlink()
            victim.symlink_to(secret)
        return real_open(path, flags, mode)

    monkeypatch.setattr("tracecat.agent.filesystem.os.open", racing_open)

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

    assert stats.file_count == 0
    assert not (restored_dir / "victim.txt").exists()


def test_archive_opens_candidate_files_nonblocking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "notes.txt").write_text("durable state")
    real_open = os.open
    observed_flags: list[int] = []

    def recording_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
    ) -> int:
        observed_flags.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr("tracecat.agent.filesystem.os.open", recording_open)

    create_work_dir_archive(
        work_dir,
        tmp_path / "snapshot.tar.gz",
        max_uncompressed_bytes=1024,
        max_file_count=10,
    )

    assert observed_flags
    assert all(flags & os.O_NONBLOCK for flags in observed_flags)


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


def test_extract_to_work_dir_surfaces_existing_work_dir_removal_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "snapshot.tar.gz"
    payload = b"new"
    with tarfile.open(archive_path, mode="w:gz") as tar:
        info = tarfile.TarInfo("new.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    work_dir = tmp_path / "agent-work-dir"
    work_dir.mkdir()
    (work_dir / "old.txt").write_text("old")
    original_rmtree = shutil.rmtree

    def fake_rmtree(path: str | Path, *args: Any, **kwargs: Any) -> None:
        if Path(path) == work_dir:
            assert kwargs.get("ignore_errors") is not True
            raise PermissionError("permission denied")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("tracecat.agent.filesystem.shutil.rmtree", fake_rmtree)

    with pytest.raises(PermissionError, match="permission denied"):
        _extract_archive_to_work_dir(archive_path, work_dir)

    assert (work_dir / "old.txt").read_text() == "old"
    assert not (work_dir / "new.txt").exists()


@pytest.mark.anyio
async def test_hydrate_skips_download_when_work_dir_already_matches_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)
    (work_dir / "notes.txt").write_text("durable state")
    current_snapshot = _snapshot_of(work_dir)
    _patch_storage(
        monkeypatch, tmp_path, _FakeSnapshotService(session_id, current_snapshot)
    )

    hydrated = await hydrate_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        work_dir=work_dir,
    )

    assert hydrated is current_snapshot
    assert (work_dir / "notes.txt").read_text() == "durable state"


@pytest.mark.anyio
async def test_hydrate_restores_restrictive_directory_from_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    archive_path = tmp_path / "restricted.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as tar:
        info = tarfile.TarInfo("restricted")
        info.type = tarfile.DIRTYPE
        info.mode = 0
        tar.addfile(info)
    archive_content = archive_path.read_bytes()
    current_snapshot = _snapshot(
        key="agent-fs/blobs/restricted.tar.gz",
        sha256=hashlib.sha256(archive_content).hexdigest(),
        size_bytes=len(archive_content),
        uncompressed_size_bytes=0,
        file_count=0,
        dir_count=1,
    )

    async def fake_download_file_to_path(
        *,
        key: str,
        bucket: str,
        output_path: Path,
        max_bytes: int | None = None,
        expected_sha256: str | None = None,
    ) -> int:
        assert key == current_snapshot.key
        assert bucket == current_snapshot.bucket
        assert max_bytes == current_snapshot.size_bytes
        assert expected_sha256 == current_snapshot.sha256
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(archive_content)
        return len(archive_content)

    _patch_storage(
        monkeypatch,
        tmp_path,
        _FakeSnapshotService(session_id, current_snapshot),
        download=fake_download_file_to_path,
    )

    hydrated = await hydrate_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        work_dir=work_dir,
    )

    restored_restricted_dir = work_dir / "restricted"
    try:
        assert hydrated is current_snapshot
        assert restored_restricted_dir.is_dir()
        assert stat.S_IMODE(restored_restricted_dir.stat().st_mode) == 0
    finally:
        if restored_restricted_dir.exists():
            restored_restricted_dir.chmod(0o700)


@pytest.mark.anyio
async def test_persist_and_hydrate_round_trip_through_blob_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)
    (work_dir / "notes.txt").write_text("durable state")
    uploaded: dict[tuple[str, str], bytes] = {}
    service = _FakeSnapshotService(session_id)

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

    _patch_storage(
        monkeypatch,
        tmp_path,
        service,
        uploaded=uploaded,
        download=fake_download_file_to_path,
    )

    snapshot = await persist_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        workspace_id=uuid.uuid4(),
        work_dir=work_dir,
    )

    assert snapshot is service.current
    assert service.current is not None
    assert uploaded.keys() == {("agent-bucket", service.current.key)}

    shutil.rmtree(work_dir)
    shutil.rmtree(tmp_path / "cache")

    hydrated = await hydrate_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        work_dir=work_dir,
    )

    assert hydrated is service.current
    assert (work_dir / "notes.txt").read_text() == "durable state"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("contents", "prior", "expected_counts"),
    [
        pytest.param("empty", "none", None, id="initial-empty-skipped"),
        pytest.param("empty-dir", "none", (0, 1), id="initial-directory-only-recorded"),
        pytest.param("empty", "stale", (0, 0), id="emptied-after-prior-recorded"),
        pytest.param("file", "matching", None, id="unchanged-state-skipped"),
    ],
)
async def test_persist_records_only_changed_work_dir_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    contents: Literal["empty", "empty-dir", "file"],
    prior: Literal["none", "stale", "matching"],
    expected_counts: tuple[int, int] | None,
) -> None:
    session_id = uuid.uuid4()
    work_dir = tmp_path / "session" / "agent-work-dir"
    work_dir.mkdir(parents=True)
    if contents == "empty-dir":
        (work_dir / "empty").mkdir()
    elif contents == "file":
        (work_dir / "notes.txt").write_text("durable state")
    prior_snapshot = {
        "none": None,
        "stale": _snapshot(),
        "matching": _snapshot_of(work_dir),
    }[prior]
    expect_record = expected_counts is not None
    service = _FakeSnapshotService(
        session_id, prior_snapshot, allow_update=expect_record
    )
    uploaded: dict[tuple[str, str], bytes] = {}
    _patch_storage(
        monkeypatch,
        tmp_path,
        service,
        uploaded=uploaded if expect_record else None,
    )

    snapshot = await persist_agent_work_dir(
        role=cast(Any, object()),
        session_id=session_id,
        workspace_id=uuid.uuid4(),
        work_dir=work_dir,
    )

    if expected_counts is None:
        assert snapshot is None
        assert not any((tmp_path / "cache" / "staging").glob("*.tar.gz"))
        return
    assert snapshot is service.recorded
    assert service.recorded is not None
    assert (service.recorded.file_count, service.recorded.dir_count) == expected_counts
    assert service.recorded.uncompressed_size_bytes == 0
    assert uploaded.keys() == {("agent-bucket", service.recorded.key)}
