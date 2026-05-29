"""Durable agent work-dir snapshot helpers."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import gzip
import hashlib
import json
import os
import shutil
import stat
import tarfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from sqlalchemy import select

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.storage import blob

AGENT_FS_ARCHIVE_FORMAT = "tar.gz"
AGENT_FS_COMPRESSION = "gzip"
AGENT_FS_CONTENT_TYPE = "application/gzip"
SNAPSHOT_MARKER_FILENAME = ".agent-fs-snapshot.json"
STATE_HASH_ALGORITHM = "blake2b-256"
_STATE_HASH_DIGEST_SIZE = 32
_archive_cache_locks: dict[str, asyncio.Lock] = {}
_archive_cache_locks_guard = asyncio.Lock()


class _HashUpdater(Protocol):
    """Minimal hash object protocol used by streaming helpers."""

    def update(self, data: bytes, /) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentFilesystemArchiveStats:
    """Local archive statistics recorded with durable snapshot metadata."""

    sha256: str
    size_bytes: int
    uncompressed_size_bytes: int
    file_count: int


@dataclass(frozen=True, slots=True)
class AgentFilesystemStateStats:
    """Canonical agent work-dir state statistics."""

    state_hash: str
    uncompressed_size_bytes: int
    file_count: int
    dir_count: int

    @property
    def entry_count(self) -> int:
        return self.file_count + self.dir_count


@dataclass(frozen=True, slots=True)
class AgentFilesystemSnapshotMetadata:
    """Current durable snapshot pointer stored on an agent session."""

    bucket: str
    key: str
    state_hash: str
    sha256: str
    size_bytes: int
    uncompressed_size_bytes: int
    file_count: int
    dir_count: int
    archive_format: str
    compression: str
    created_at: str

    @classmethod
    def from_raw(
        cls, raw: Mapping[str, object] | None
    ) -> AgentFilesystemSnapshotMetadata | None:
        if raw is None:
            return None
        try:
            return cls(
                bucket=_required_str(raw, "bucket"),
                key=_required_str(raw, "key"),
                state_hash=_required_str(raw, "state_hash"),
                sha256=_required_str(raw, "sha256"),
                size_bytes=_required_int(raw, "size_bytes"),
                uncompressed_size_bytes=_required_int(raw, "uncompressed_size_bytes"),
                file_count=_required_int(raw, "file_count"),
                dir_count=_required_int(raw, "dir_count"),
                archive_format=_required_str(raw, "archive_format"),
                compression=_required_str(raw, "compression"),
                created_at=_required_str(raw, "created_at"),
            )
        except (TypeError, ValueError, KeyError):
            logger.warning("Ignoring malformed agent filesystem snapshot metadata")
            return None

    def to_dict(self) -> dict[str, object]:
        return {
            "bucket": self.bucket,
            "key": self.key,
            "state_hash": self.state_hash,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "uncompressed_size_bytes": self.uncompressed_size_bytes,
            "file_count": self.file_count,
            "dir_count": self.dir_count,
            "archive_format": self.archive_format,
            "compression": self.compression,
            "created_at": self.created_at,
        }


class AgentFilesystemSnapshotLimitExceeded(ValueError):
    """Raised when an agent work-dir snapshot exceeds configured limits."""


class AgentFilesystemService(BaseWorkspaceService):
    """Service for durable agent filesystem snapshot metadata."""

    service_name = "agent-filesystem"

    async def get_current_snapshot(
        self,
        session_id: uuid.UUID,
    ) -> AgentFilesystemSnapshotMetadata | None:
        """Return the session's current durable work-dir snapshot pointer."""
        raw_snapshot = await self.session.scalar(
            select(AgentSession.work_dir_snapshot)
            .where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
            )
            .limit(1)
        )
        return AgentFilesystemSnapshotMetadata.from_raw(raw_snapshot)

    async def update_current_snapshot(
        self,
        *,
        session_id: uuid.UUID,
        snapshot: AgentFilesystemSnapshotMetadata,
    ) -> AgentFilesystemSnapshotMetadata:
        """Persist the session's current durable work-dir snapshot pointer."""
        agent_session = await self.session.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
            )
        )
        if agent_session is None:
            raise ValueError(f"Agent session {session_id} not found")

        agent_session.work_dir_snapshot = snapshot.to_dict()
        await self.session.commit()
        return snapshot


def build_agent_fs_snapshot_key(
    *,
    sha256: str,
) -> str:
    """Build the content-addressed object key for a compressed work-dir archive."""
    return f"agent-fs/blobs/{sha256}.tar.gz"


async def hydrate_agent_work_dir(
    *,
    role: Role,
    session_id: uuid.UUID,
    work_dir: Path,
) -> AgentFilesystemSnapshotMetadata | None:
    """Hydrate ``work_dir`` from the session's current durable snapshot pointer."""
    async with AgentFilesystemService.with_session(role=role) as service:
        snapshot = await service.get_current_snapshot(session_id)

    if snapshot is None:
        work_dir.mkdir(parents=True, exist_ok=True)
        return None

    marker = _read_snapshot_marker(work_dir)
    if (
        marker is not None
        and _snapshot_marker_matches(marker, snapshot)
        and await asyncio.to_thread(_work_dir_matches_snapshot, work_dir, snapshot)
    ):
        logger.debug(
            "Agent filesystem snapshot already hydrated",
            session_id=str(session_id),
            state_hash=snapshot.state_hash,
            sha256=snapshot.sha256,
        )
        return snapshot

    archive_path = await _ensure_snapshot_archive_cached(snapshot)
    await asyncio.to_thread(_extract_archive_to_work_dir, archive_path, work_dir)
    await asyncio.to_thread(_write_snapshot_marker, work_dir, snapshot)
    logger.info(
        "Hydrated agent filesystem snapshot",
        session_id=str(session_id),
        state_hash=snapshot.state_hash,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
        file_count=snapshot.file_count,
        dir_count=snapshot.dir_count,
    )
    return snapshot


async def persist_agent_work_dir(
    *,
    role: Role,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_dir: Path,
) -> AgentFilesystemSnapshotMetadata | None:
    """Create, upload, and record a durable snapshot of ``work_dir``."""
    cache_root = _cache_root()
    staging_dir = cache_root / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    temp_archive = staging_dir / f"{session_id}-{uuid.uuid4().hex}.tar.gz"

    try:
        state_stats = await asyncio.to_thread(
            compute_work_dir_state,
            work_dir,
            max_uncompressed_bytes=config.TRACECAT__AGENT_FS_MAX_UNCOMPRESSED_BYTES,
            max_file_count=config.TRACECAT__AGENT_FS_MAX_FILE_COUNT,
        )

        async with AgentFilesystemService.with_session(role=role) as service:
            current_snapshot = await service.get_current_snapshot(session_id)

        if (
            current_snapshot is not None
            and current_snapshot.state_hash == state_stats.state_hash
        ):
            logger.debug(
                "Skipping unchanged agent filesystem snapshot",
                session_id=str(session_id),
                workspace_id=str(workspace_id),
                state_hash=state_stats.state_hash,
            )
            return None

        if current_snapshot is None and state_stats.entry_count == 0:
            logger.debug(
                "Skipping empty initial agent filesystem snapshot",
                session_id=str(session_id),
                workspace_id=str(workspace_id),
                state_hash=state_stats.state_hash,
            )
            return None

        archive_stats = await asyncio.to_thread(
            create_work_dir_archive,
            work_dir,
            temp_archive,
            max_uncompressed_bytes=config.TRACECAT__AGENT_FS_MAX_UNCOMPRESSED_BYTES,
            max_file_count=config.TRACECAT__AGENT_FS_MAX_FILE_COUNT,
        )
        archive_path = await asyncio.to_thread(
            _promote_archive_to_cache,
            temp_archive,
            archive_stats.sha256,
        )
        bucket = config.TRACECAT__BLOB_STORAGE_BUCKET_AGENT
        key = build_agent_fs_snapshot_key(sha256=archive_stats.sha256)
        await blob.upload_file_from_path(
            archive_path,
            key=key,
            bucket=bucket,
            content_type=AGENT_FS_CONTENT_TYPE,
        )
        snapshot = AgentFilesystemSnapshotMetadata(
            bucket=bucket,
            key=key,
            state_hash=state_stats.state_hash,
            sha256=archive_stats.sha256,
            size_bytes=archive_stats.size_bytes,
            uncompressed_size_bytes=state_stats.uncompressed_size_bytes,
            file_count=state_stats.file_count,
            dir_count=state_stats.dir_count,
            archive_format=AGENT_FS_ARCHIVE_FORMAT,
            compression=AGENT_FS_COMPRESSION,
            created_at=datetime.now(UTC).isoformat(),
        )
        async with AgentFilesystemService.with_session(role=role) as service:
            snapshot = await service.update_current_snapshot(
                session_id=session_id,
                snapshot=snapshot,
            )
        await asyncio.to_thread(_write_snapshot_marker, work_dir, snapshot)
        logger.info(
            "Persisted agent filesystem snapshot",
            session_id=str(session_id),
            workspace_id=str(workspace_id),
            state_hash=state_stats.state_hash,
            sha256=archive_stats.sha256,
            size_bytes=archive_stats.size_bytes,
            uncompressed_size_bytes=state_stats.uncompressed_size_bytes,
            file_count=state_stats.file_count,
            dir_count=state_stats.dir_count,
        )
        return snapshot
    finally:
        temp_archive.unlink(missing_ok=True)


def compute_work_dir_state(
    work_dir: Path,
    *,
    max_uncompressed_bytes: int,
    max_file_count: int,
) -> AgentFilesystemStateStats:
    """Compute a stable logical state hash for regular files and directories."""
    work_dir = work_dir.resolve()
    state_hasher = hashlib.blake2b(digest_size=_STATE_HASH_DIGEST_SIZE)
    _hash_state_field(state_hasher, b"tracecat-agent-fs-state-v1")
    total_bytes = 0
    file_count = 0
    dir_count = 0

    for root, dirs, files in os.walk(work_dir, followlinks=False):
        root_path = Path(root)
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            dir_path = root_path / dirname
            try:
                dir_stat = dir_path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(dir_stat.st_mode):
                continue
            kept_dirs.append(dirname)
            dir_count += 1
            _hash_state_entry(
                state_hasher,
                entry_type="dir",
                relative_path=dir_path.relative_to(work_dir).as_posix(),
                mode=stat.S_IMODE(dir_stat.st_mode),
                size_bytes=0,
                content_hash="",
            )
        dirs[:] = kept_dirs

        for filename in sorted(files):
            file_path = root_path / filename
            try:
                file_stat = file_path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                continue
            file_count += 1
            if file_count > max_file_count:
                raise AgentFilesystemSnapshotLimitExceeded(
                    "Agent filesystem snapshot exceeds max file count: "
                    f"{file_count} > {max_file_count}"
                )
            total_bytes += file_stat.st_size
            if total_bytes > max_uncompressed_bytes:
                raise AgentFilesystemSnapshotLimitExceeded(
                    "Agent filesystem snapshot exceeds max uncompressed bytes: "
                    f"{total_bytes} > {max_uncompressed_bytes}"
                )
            _hash_state_entry(
                state_hasher,
                entry_type="file",
                relative_path=file_path.relative_to(work_dir).as_posix(),
                mode=stat.S_IMODE(file_stat.st_mode),
                size_bytes=file_stat.st_size,
                content_hash=_blake2b_file(file_path),
            )

    return AgentFilesystemStateStats(
        state_hash=state_hasher.hexdigest(),
        uncompressed_size_bytes=total_bytes,
        file_count=file_count,
        dir_count=dir_count,
    )


def create_work_dir_archive(
    work_dir: Path,
    output_path: Path,
    *,
    max_uncompressed_bytes: int,
    max_file_count: int,
) -> AgentFilesystemArchiveStats:
    """Create a gzip-compressed tar snapshot of regular files and directories."""
    work_dir = work_dir.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.part")
    total_bytes = 0
    file_count = 0

    try:
        with temp_path.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_output, mtime=0
            ) as gzip_output:
                with tarfile.open(
                    fileobj=gzip_output,
                    mode="w",
                    dereference=False,
                    format=tarfile.PAX_FORMAT,
                ) as tar:
                    for root, dirs, files in os.walk(work_dir, followlinks=False):
                        root_path = Path(root)
                        kept_dirs: list[str] = []
                        for dirname in sorted(dirs):
                            dir_path = root_path / dirname
                            try:
                                dir_stat = dir_path.lstat()
                            except FileNotFoundError:
                                continue
                            if not stat.S_ISDIR(dir_stat.st_mode):
                                continue
                            kept_dirs.append(dirname)
                            _add_directory_to_archive(
                                tar,
                                dir_path,
                                work_dir,
                                dir_stat,
                            )
                        dirs[:] = kept_dirs

                        for filename in sorted(files):
                            file_path = root_path / filename
                            opened_file = _open_regular_file_for_snapshot(file_path)
                            if opened_file is None:
                                continue
                            source, file_stat = opened_file
                            file_count += 1
                            try:
                                if file_count > max_file_count:
                                    raise AgentFilesystemSnapshotLimitExceeded(
                                        "Agent filesystem snapshot exceeds max file count: "
                                        f"{file_count} > {max_file_count}"
                                    )
                                total_bytes += file_stat.st_size
                                if total_bytes > max_uncompressed_bytes:
                                    raise AgentFilesystemSnapshotLimitExceeded(
                                        "Agent filesystem snapshot exceeds max uncompressed bytes: "
                                        f"{total_bytes} > {max_uncompressed_bytes}"
                                    )
                                _add_regular_file_to_archive(
                                    tar,
                                    file_path,
                                    work_dir,
                                    source,
                                    file_stat,
                                )
                            finally:
                                source.close()
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise

    return AgentFilesystemArchiveStats(
        sha256=_sha256_file(output_path),
        size_bytes=output_path.stat().st_size,
        uncompressed_size_bytes=total_bytes,
        file_count=file_count,
    )


def extract_work_dir_archive(
    archive_path: Path,
    destination_dir: Path,
    *,
    max_uncompressed_bytes: int,
    max_file_count: int,
) -> None:
    """Safely extract a work-dir archive into a fresh destination directory."""
    if destination_dir.exists():
        raise FileExistsError(f"Destination already exists: {destination_dir}")
    destination_dir.mkdir(parents=True)
    total_bytes = 0
    file_count = 0
    directory_modes: list[tuple[Path, int]] = []

    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            for member in tar:
                member_path = _validate_archive_member(member)
                target_path = destination_dir / Path(*member_path.parts)
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    directory_modes.append((target_path, member.mode & 0o777))
                    continue
                if not member.isfile():
                    continue

                file_count += 1
                if file_count > max_file_count:
                    raise AgentFilesystemSnapshotLimitExceeded(
                        "Agent filesystem snapshot exceeds max file count during extraction: "
                        f"{file_count} > {max_file_count}"
                    )
                total_bytes += member.size
                if total_bytes > max_uncompressed_bytes:
                    raise AgentFilesystemSnapshotLimitExceeded(
                        "Agent filesystem snapshot exceeds max uncompressed bytes during extraction: "
                        f"{total_bytes} > {max_uncompressed_bytes}"
                    )

                source = tar.extractfile(member)
                if source is None:
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with source, target_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                _chmod_from_tarinfo(target_path, member)
        for target_path, mode in reversed(directory_modes):
            _chmod_path(target_path, mode)
    except Exception:
        shutil.rmtree(destination_dir, ignore_errors=True)
        raise


async def _ensure_snapshot_archive_cached(
    snapshot: AgentFilesystemSnapshotMetadata,
) -> Path:
    archive_path = _archive_cache_path(snapshot.sha256)
    if _cached_archive_matches(archive_path, snapshot):
        return archive_path

    lock = await _get_archive_cache_lock(snapshot.sha256)
    async with lock:
        if not _cached_archive_matches(archive_path, snapshot):
            await blob.download_file_to_path(
                key=snapshot.key,
                bucket=snapshot.bucket,
                output_path=archive_path,
                max_bytes=snapshot.size_bytes,
                expected_sha256=snapshot.sha256,
            )
    return archive_path


def _extract_archive_to_work_dir(archive_path: Path, work_dir: Path) -> None:
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = work_dir.with_name(f".tmp-{work_dir.name}-{uuid.uuid4().hex}")
    try:
        extract_work_dir_archive(
            archive_path,
            temp_dir,
            max_uncompressed_bytes=config.TRACECAT__AGENT_FS_MAX_UNCOMPRESSED_BYTES,
            max_file_count=config.TRACECAT__AGENT_FS_MAX_FILE_COUNT,
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        temp_dir.rename(work_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _promote_archive_to_cache(temp_archive: Path, sha256: str) -> Path:
    archive_path = _archive_cache_path(sha256)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        temp_archive.unlink(missing_ok=True)
        return archive_path
    os.replace(temp_archive, archive_path)
    return archive_path


def _archive_cache_path(sha256: str) -> Path:
    return _cache_root() / "archives" / f"{sha256}.tar.gz"


def _cache_root() -> Path:
    root = Path(config.TRACECAT__AGENT_FS_CACHE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cached_archive_matches(
    archive_path: Path,
    snapshot: AgentFilesystemSnapshotMetadata,
) -> bool:
    if not archive_path.exists() or archive_path.stat().st_size != snapshot.size_bytes:
        return False
    return _sha256_file(archive_path) == snapshot.sha256


async def _get_archive_cache_lock(sha256: str) -> asyncio.Lock:
    async with _archive_cache_locks_guard:
        lock = _archive_cache_locks.get(sha256)
        if lock is None:
            lock = asyncio.Lock()
            _archive_cache_locks[sha256] = lock
        return lock


def _add_directory_to_archive(
    tar: tarfile.TarFile,
    path: Path,
    work_dir: Path,
    path_stat: os.stat_result,
) -> None:
    relative_path = path.relative_to(work_dir).as_posix()
    tarinfo = _snapshot_tarinfo(
        name=relative_path,
        mode=path_stat.st_mode,
        size=0,
        entry_type=tarfile.DIRTYPE,
    )
    tar.addfile(tarinfo)


def _add_regular_file_to_archive(
    tar: tarfile.TarFile,
    path: Path,
    work_dir: Path,
    source: BinaryIO,
    path_stat: os.stat_result,
) -> None:
    relative_path = path.relative_to(work_dir).as_posix()
    tarinfo = _snapshot_tarinfo(
        name=relative_path,
        mode=path_stat.st_mode,
        size=path_stat.st_size,
        entry_type=tarfile.REGTYPE,
    )
    tar.addfile(tarinfo, source)


def _snapshot_tarinfo(
    *,
    name: str,
    mode: int,
    size: int,
    entry_type: bytes,
) -> tarfile.TarInfo:
    tarinfo = tarfile.TarInfo(name)
    tarinfo.type = entry_type
    tarinfo.size = size
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = ""
    tarinfo.gname = ""
    tarinfo.mode = mode & 0o777
    tarinfo.mtime = 0
    return tarinfo


def _open_regular_file_for_snapshot(
    path: Path,
) -> tuple[BinaryIO, os.stat_result] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as e:
        if e.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            return None
        raise

    try:
        path_stat = os.fstat(fd)
        if not stat.S_ISREG(path_stat.st_mode):
            os.close(fd)
            return None
        if not _path_still_names_open_file(path, path_stat):
            os.close(fd)
            return None
        return os.fdopen(fd, "rb"), path_stat
    except Exception:
        os.close(fd)
        raise


def _path_still_names_open_file(path: Path, open_stat: os.stat_result) -> bool:
    try:
        path_stat = path.lstat()
    except OSError as e:
        if e.errno in {errno.ENOENT, errno.ENOTDIR}:
            return False
        raise
    return (
        stat.S_ISREG(path_stat.st_mode)
        and path_stat.st_dev == open_stat.st_dev
        and path_stat.st_ino == open_stat.st_ino
    )


def _validate_archive_member(member: tarfile.TarInfo) -> PurePosixPath:
    member_path = PurePosixPath(member.name)
    if (
        member_path.is_absolute()
        or member_path.name == ""
        or any(part in {"", ".", ".."} for part in member_path.parts)
    ):
        raise ValueError(f"Unsafe path in agent filesystem archive: {member.name!r}")
    return member_path


def _chmod_from_tarinfo(path: Path, member: tarfile.TarInfo) -> None:
    _chmod_path(path, member.mode & 0o777)


def _chmod_path(path: Path, mode: int) -> None:
    with contextlib.suppress(OSError):
        path.chmod(mode)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        _copy_to_hash(f, hasher)
    return hasher.hexdigest()


def _blake2b_file(path: Path) -> str:
    hasher = hashlib.blake2b(digest_size=_STATE_HASH_DIGEST_SIZE)
    with path.open("rb") as f:
        _copy_to_hash(f, hasher)
    return hasher.hexdigest()


def _copy_to_hash(source: BinaryIO, hasher: _HashUpdater) -> None:
    while chunk := source.read(1024 * 1024):
        hasher.update(chunk)


def _hash_state_entry(
    hasher: _HashUpdater,
    *,
    entry_type: str,
    relative_path: str,
    mode: int,
    size_bytes: int,
    content_hash: str,
) -> None:
    _hash_state_field(hasher, entry_type.encode())
    _hash_state_field(hasher, relative_path.encode("utf-8", errors="surrogateescape"))
    _hash_state_field(hasher, str(mode).encode())
    _hash_state_field(hasher, str(size_bytes).encode())
    _hash_state_field(hasher, content_hash.encode())


def _hash_state_field(hasher: _HashUpdater, value: bytes) -> None:
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def _required_str(raw: Mapping[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise TypeError(f"Expected string field {key}")
    return value


def _required_int(raw: Mapping[str, object], key: str) -> int:
    value = raw[key]
    if not isinstance(value, int):
        raise TypeError(f"Expected integer field {key}")
    return value


def _work_dir_matches_snapshot(
    work_dir: Path,
    snapshot: AgentFilesystemSnapshotMetadata,
) -> bool:
    if not work_dir.exists():
        return False
    try:
        state_stats = compute_work_dir_state(
            work_dir,
            max_uncompressed_bytes=config.TRACECAT__AGENT_FS_MAX_UNCOMPRESSED_BYTES,
            max_file_count=config.TRACECAT__AGENT_FS_MAX_FILE_COUNT,
        )
    except Exception as e:
        logger.debug(
            "Failed to verify hydrated agent filesystem marker",
            error=str(e),
            sha256=snapshot.sha256,
        )
        return False
    return state_stats.state_hash == snapshot.state_hash


def _snapshot_marker_path(work_dir: Path) -> Path:
    return work_dir.parent / SNAPSHOT_MARKER_FILENAME


def _read_snapshot_marker(work_dir: Path) -> dict[str, str] | None:
    marker_path = _snapshot_marker_path(work_dir)
    try:
        with marker_path.open("r") as f:
            marker = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(marker, dict):
        return None
    sha256 = marker.get("sha256")
    state_hash = marker.get("state_hash")
    if not isinstance(sha256, str) or not isinstance(state_hash, str):
        return None
    return {"sha256": sha256, "state_hash": state_hash}


def _snapshot_marker_matches(
    marker: dict[str, str],
    snapshot: AgentFilesystemSnapshotMetadata,
) -> bool:
    return (
        marker.get("state_hash") == snapshot.state_hash
        and marker.get("sha256") == snapshot.sha256
    )


def _write_snapshot_marker(
    work_dir: Path,
    snapshot: AgentFilesystemSnapshotMetadata,
) -> None:
    marker_path = _snapshot_marker_path(work_dir)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = marker_path.with_name(f"{marker_path.name}.tmp")
    with temp_path.open("w") as f:
        json.dump(
            {
                "state_hash": snapshot.state_hash,
                "sha256": snapshot.sha256,
            },
            f,
            sort_keys=True,
        )
    os.replace(temp_path, marker_path)
