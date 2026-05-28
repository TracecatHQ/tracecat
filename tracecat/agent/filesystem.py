"""Durable agent work-dir snapshot helpers."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import stat
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from sqlalchemy import select

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionFilesystemSnapshot
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.storage import blob

AGENT_FS_ARCHIVE_FORMAT = "tar.gz"
AGENT_FS_COMPRESSION = "gzip"
AGENT_FS_CONTENT_TYPE = "application/gzip"
SNAPSHOT_MARKER_FILENAME = ".agent-fs-snapshot.json"
STATE_HASH_ALGORITHM = "blake2b-256"
_STATE_HASH_DIGEST_SIZE = 32


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


class AgentFilesystemSnapshotLimitExceeded(ValueError):
    """Raised when an agent work-dir snapshot exceeds configured limits."""


class AgentFilesystemService(BaseWorkspaceService):
    """Service for durable agent filesystem snapshot metadata."""

    service_name = "agent-filesystem"

    async def get_latest_snapshot_for_hydration(
        self,
        session_id: uuid.UUID,
        *,
        include_parent: bool = True,
    ) -> AgentSessionFilesystemSnapshot | None:
        """Return the latest snapshot for this session, falling back to parent."""
        current = await self._get_latest_snapshot(session_id)
        if current is not None or not include_parent:
            return current

        parent_session_id = await self.session.scalar(
            select(AgentSession.parent_session_id).where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
            )
        )
        if parent_session_id is None:
            return None
        return await self._get_latest_snapshot(parent_session_id)

    async def _get_latest_snapshot(
        self,
        session_id: uuid.UUID,
    ) -> AgentSessionFilesystemSnapshot | None:
        result = await self.session.scalars(
            select(AgentSessionFilesystemSnapshot)
            .where(
                AgentSessionFilesystemSnapshot.session_id == session_id,
                AgentSessionFilesystemSnapshot.workspace_id == self.workspace_id,
            )
            .order_by(
                AgentSessionFilesystemSnapshot.created_at.desc(),
                AgentSessionFilesystemSnapshot.surrogate_id.desc(),
            )
            .limit(1)
        )
        return result.first()

    async def record_snapshot(
        self,
        *,
        session_id: uuid.UUID,
        bucket: str,
        key: str,
        archive_stats: AgentFilesystemArchiveStats,
        state_stats: AgentFilesystemStateStats,
    ) -> AgentSessionFilesystemSnapshot:
        """Persist metadata for an uploaded agent work-dir archive."""
        snapshot = AgentSessionFilesystemSnapshot(
            workspace_id=self.workspace_id,
            session_id=session_id,
            bucket=bucket,
            key=key,
            state_hash=state_stats.state_hash,
            sha256=archive_stats.sha256,
            size_bytes=archive_stats.size_bytes,
            uncompressed_size_bytes=state_stats.uncompressed_size_bytes,
            file_count=state_stats.file_count,
            archive_format=AGENT_FS_ARCHIVE_FORMAT,
            compression=AGENT_FS_COMPRESSION,
        )
        self.session.add(snapshot)
        await self.session.commit()
        await self.session.refresh(snapshot)
        return snapshot


def build_agent_fs_snapshot_key(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    sha256: str,
) -> str:
    """Build the durable object key for a compressed agent work-dir archive."""
    return (
        f"agent-fs/workspaces/{workspace_id}/sessions/{session_id}/"
        f"snapshots/{sha256}.tar.gz"
    )


async def hydrate_agent_work_dir(
    *,
    role: Role,
    session_id: uuid.UUID,
    work_dir: Path,
) -> AgentSessionFilesystemSnapshot | None:
    """Hydrate ``work_dir`` from the latest durable session snapshot if present."""
    async with AgentFilesystemService.with_session(role=role) as service:
        snapshot = await service.get_latest_snapshot_for_hydration(session_id)

    if snapshot is None:
        work_dir.mkdir(parents=True, exist_ok=True)
        return None

    marker = _read_snapshot_marker(work_dir)
    if marker is not None and _snapshot_marker_matches(marker, snapshot):
        if work_dir.exists():
            logger.debug(
                "Agent filesystem snapshot already hydrated",
                session_id=str(session_id),
                snapshot_id=str(snapshot.id),
                state_hash=getattr(snapshot, "state_hash", None),
                sha256=snapshot.sha256,
            )
            return snapshot

    cached_dir = await _ensure_snapshot_cached(snapshot)
    await asyncio.to_thread(_copy_cached_work_dir, cached_dir, work_dir)
    await asyncio.to_thread(_write_snapshot_marker, work_dir, snapshot)
    logger.info(
        "Hydrated agent filesystem snapshot",
        session_id=str(session_id),
        snapshot_id=str(snapshot.id),
        state_hash=getattr(snapshot, "state_hash", None),
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
        file_count=snapshot.file_count,
    )
    return snapshot


async def persist_agent_work_dir(
    *,
    role: Role,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_dir: Path,
) -> AgentSessionFilesystemSnapshot | None:
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
            latest_snapshot = await service.get_latest_snapshot_for_hydration(
                session_id
            )

        if (
            latest_snapshot is not None
            and getattr(latest_snapshot, "state_hash", None) == state_stats.state_hash
        ):
            logger.debug(
                "Skipping unchanged agent filesystem snapshot",
                session_id=str(session_id),
                snapshot_id=str(latest_snapshot.id),
                state_hash=state_stats.state_hash,
            )
            return None

        if latest_snapshot is None and state_stats.file_count == 0:
            logger.debug(
                "Skipping empty initial agent filesystem snapshot",
                session_id=str(session_id),
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
        key = build_agent_fs_snapshot_key(
            workspace_id=workspace_id,
            session_id=session_id,
            sha256=archive_stats.sha256,
        )
        await blob.upload_file_from_path(
            archive_path,
            key=key,
            bucket=bucket,
            content_type=AGENT_FS_CONTENT_TYPE,
        )
        await asyncio.to_thread(_ensure_unpacked_cache_from_archive, archive_path)

        async with AgentFilesystemService.with_session(role=role) as service:
            snapshot = await service.record_snapshot(
                session_id=session_id,
                bucket=bucket,
                key=key,
                archive_stats=archive_stats,
                state_stats=state_stats,
            )
        await asyncio.to_thread(_write_snapshot_marker, work_dir, snapshot)
        logger.info(
            "Persisted agent filesystem snapshot",
            session_id=str(session_id),
            snapshot_id=str(snapshot.id),
            state_hash=state_stats.state_hash,
            sha256=archive_stats.sha256,
            size_bytes=archive_stats.size_bytes,
            uncompressed_size_bytes=state_stats.uncompressed_size_bytes,
            file_count=state_stats.file_count,
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
    )


def create_work_dir_archive(
    work_dir: Path,
    output_path: Path,
    *,
    max_uncompressed_bytes: int,
    max_file_count: int,
) -> AgentFilesystemArchiveStats:
    """Create a gzip-compressed tar snapshot of regular files in ``work_dir``."""
    work_dir = work_dir.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.part")
    total_bytes = 0
    file_count = 0

    try:
        with tarfile.open(temp_path, mode="w:gz", dereference=False) as tar:
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
                    _add_path_to_archive(tar, dir_path, work_dir)
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
                    _add_path_to_archive(tar, file_path, work_dir)
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

    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            for member in tar:
                member_path = _validate_archive_member(member)
                target_path = destination_dir / Path(*member_path.parts)
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    _chmod_from_tarinfo(target_path, member)
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
    except Exception:
        shutil.rmtree(destination_dir, ignore_errors=True)
        raise


async def _ensure_snapshot_cached(
    snapshot: AgentSessionFilesystemSnapshot,
) -> Path:
    archive_path = _archive_cache_path(snapshot.sha256)
    if not _cached_archive_matches(archive_path, snapshot):
        await blob.download_file_to_path(
            key=snapshot.key,
            bucket=snapshot.bucket,
            output_path=archive_path,
            max_bytes=snapshot.size_bytes,
            expected_sha256=snapshot.sha256,
        )
    return await asyncio.to_thread(_ensure_unpacked_cache_from_archive, archive_path)


def _ensure_unpacked_cache_from_archive(archive_path: Path) -> Path:
    sha256 = archive_path.stem.removesuffix(".tar")
    unpacked_dir = _unpacked_cache_path(sha256)
    if unpacked_dir.exists():
        return unpacked_dir

    temp_dir = unpacked_dir.with_name(f".tmp-{sha256}-{uuid.uuid4().hex}")
    try:
        extract_work_dir_archive(
            archive_path,
            temp_dir,
            max_uncompressed_bytes=config.TRACECAT__AGENT_FS_MAX_UNCOMPRESSED_BYTES,
            max_file_count=config.TRACECAT__AGENT_FS_MAX_FILE_COUNT,
        )
        try:
            temp_dir.rename(unpacked_dir)
        except FileExistsError:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return unpacked_dir


def _copy_cached_work_dir(cached_dir: Path, work_dir: Path) -> None:
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(work_dir, ignore_errors=True)
    shutil.copytree(cached_dir, work_dir)


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


def _unpacked_cache_path(sha256: str) -> Path:
    return _cache_root() / "unpacked" / sha256


def _cache_root() -> Path:
    root = Path(config.TRACECAT__AGENT_FS_CACHE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cached_archive_matches(
    archive_path: Path,
    snapshot: AgentSessionFilesystemSnapshot,
) -> bool:
    if not archive_path.exists() or archive_path.stat().st_size != snapshot.size_bytes:
        return False
    return _sha256_file(archive_path) == snapshot.sha256


def _add_path_to_archive(tar: tarfile.TarFile, path: Path, work_dir: Path) -> None:
    relative_path = path.relative_to(work_dir).as_posix()
    tar.add(path, arcname=relative_path, recursive=False, filter=_normalize_tarinfo)


def _normalize_tarinfo(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if not (tarinfo.isfile() or tarinfo.isdir()):
        return None
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = ""
    tarinfo.gname = ""
    tarinfo.mode &= 0o777
    return tarinfo


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
    with contextlib.suppress(OSError):
        path.chmod(member.mode & 0o777)


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
    snapshot_id = marker.get("snapshot_id")
    sha256 = marker.get("sha256")
    if not isinstance(snapshot_id, str) or not isinstance(sha256, str):
        return None
    state_hash = marker.get("state_hash")
    parsed_marker = {"snapshot_id": snapshot_id, "sha256": sha256}
    if isinstance(state_hash, str):
        parsed_marker["state_hash"] = state_hash
    return parsed_marker


def _snapshot_marker_matches(
    marker: dict[str, str],
    snapshot: AgentSessionFilesystemSnapshot,
) -> bool:
    if marker.get("state_hash") == getattr(snapshot, "state_hash", None):
        return True
    return marker.get("sha256") == snapshot.sha256


def _write_snapshot_marker(
    work_dir: Path,
    snapshot: AgentSessionFilesystemSnapshot,
) -> None:
    marker_path = _snapshot_marker_path(work_dir)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = marker_path.with_name(f"{marker_path.name}.tmp")
    with temp_path.open("w") as f:
        json.dump(
            {
                "snapshot_id": str(snapshot.id),
                "state_hash": snapshot.state_hash,
                "sha256": snapshot.sha256,
            },
            f,
            sort_keys=True,
        )
    os.replace(temp_path, marker_path)
