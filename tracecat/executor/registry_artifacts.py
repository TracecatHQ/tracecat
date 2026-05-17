"""Registry artifact resolution and local materialization for executors."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tarfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tracecat import config
from tracecat.logger import logger
from tracecat.storage import blob


class RegistryArtifactFormat(StrEnum):
    """Executor-supported registry artifact encodings."""

    SQUASHFS = "squashfs"
    TAR_GZ = "tar.gz"


SQUASHFS_MOUNT_OPTIONS = "loop,ro,nodev,nosuid"
"""Mount options for executor-managed SquashFS registry artifacts.

The image must stay read-only and should not expose device nodes or setuid bits
from registry package contents. Avoid noexec because Python packages may include
native extension modules that need to be loaded from the mounted artifact.
"""


@dataclass(frozen=True, slots=True)
class RegistryArtifact:
    """A concrete downloadable registry artifact."""

    uri: str
    format: RegistryArtifactFormat


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse an s3://bucket/key URI into (bucket, key)."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}")
    rest = uri.removeprefix("s3://")
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return bucket, key


def compute_registry_artifact_cache_key(artifact_uri: str) -> str:
    """Compute the local cache key for a registry artifact URI."""
    if not artifact_uri:
        return "base"
    # S3 keys are case-sensitive, so preserve URI case when hashing.
    content = artifact_uri.strip()
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _squashfs_sidecar_uri(tarball_uri: str) -> str | None:
    """Return the sibling SquashFS URI for registry site-packages tarballs."""
    if not tarball_uri.endswith("site-packages.tar.gz"):
        return None
    return tarball_uri.removesuffix(".tar.gz") + ".squashfs"


def _tarball_uri_for_squashfs(squashfs_uri: str) -> str | None:
    """Return the sibling gzip tarball URI for registry SquashFS artifacts."""
    if not squashfs_uri.endswith("site-packages.squashfs"):
        return None
    return squashfs_uri.removesuffix(".squashfs") + ".tar.gz"


def _artifact_format(artifact_uri: str) -> RegistryArtifactFormat:
    """Return the materialization format for an artifact URI."""
    if artifact_uri.endswith(".squashfs"):
        return RegistryArtifactFormat.SQUASHFS
    return RegistryArtifactFormat.TAR_GZ


def _tarball_suffix(artifact: RegistryArtifact) -> str:
    """Return a local filename suffix for tarball artifacts."""
    if artifact.format == RegistryArtifactFormat.TAR_GZ:
        return ".tar.gz"
    raise ValueError(f"Unsupported tarball artifact format: {artifact.format}")


def _kernel_supports_squashfs() -> bool:
    """Return whether the kernel advertises SquashFS filesystem support."""
    filesystems_path = Path("/proc/filesystems")
    if not filesystems_path.exists():
        return True

    try:
        return any(
            split_line[-1] == "squashfs"
            for line in filesystems_path.read_text().splitlines()
            if (split_line := line.split())
        )
    except OSError:
        logger.debug("Could not inspect kernel filesystems")
        return True


class RegistryArtifactCache:
    """Materializes registry artifacts into executor-local Python paths."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()
        self._squashfs_mount_disabled = False

    async def ensure_environment(self, artifact_uri: str | None) -> Path | None:
        """Materialize an optional registry artifact and return a PYTHONPATH entry."""
        if not artifact_uri:
            return None
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        return await self.materialize(cache_key, artifact_uri)

    async def materialize(self, cache_key: str, artifact_uri: str) -> Path:
        """Materialize a registry artifact as a local importable directory."""
        squashfs_mount_dir = self.cache_dir / f"squashfs-{cache_key}"
        tarball_target_dir = self.cache_dir / f"tarball-{cache_key}"

        if cached_path := self._cached_path(
            squashfs_mount_dir=squashfs_mount_dir,
            tarball_target_dir=tarball_target_dir,
        ):
            return cached_path

        lock = await self._lock_for(cache_key)
        async with lock:
            if cached_path := self._cached_path(
                squashfs_mount_dir=squashfs_mount_dir,
                tarball_target_dir=tarball_target_dir,
            ):
                return cached_path

            artifact = await self._resolve_preferred_artifact(artifact_uri)
            logger.info(
                "Resolved registry artifact",
                cache_key=cache_key,
                artifact_uri=artifact.uri,
                artifact_format=artifact.format.value,
            )
            if artifact.format == RegistryArtifactFormat.SQUASHFS:
                try:
                    return await self._materialize_squashfs(
                        cache_key=cache_key,
                        artifact=artifact,
                        image_path=self.cache_dir / f"squashfs-{cache_key}.squashfs",
                        target_dir=squashfs_mount_dir,
                    )
                except Exception as e:
                    self._squashfs_mount_disabled = True
                    logger.warning(
                        "Failed to mount SquashFS registry artifact, falling back",
                        cache_key=cache_key,
                        artifact_uri=artifact.uri,
                        artifact_format=artifact.format.value,
                        error=str(e),
                    )
                    artifact = await self._resolve_preferred_artifact(
                        artifact_uri,
                        allow_squashfs=False,
                    )
                    logger.info(
                        "Resolved registry artifact fallback",
                        cache_key=cache_key,
                        artifact_uri=artifact.uri,
                        artifact_format=artifact.format.value,
                    )

            return await self._materialize_tarball(
                cache_key=cache_key,
                artifact=artifact,
                target_dir=tarball_target_dir,
            )

    async def _lock_for(self, cache_key: str) -> asyncio.Lock:
        """Get or create a lock for the given cache key."""
        async with self._locks_lock:
            if cache_key not in self._locks:
                self._locks[cache_key] = asyncio.Lock()
            return self._locks[cache_key]

    def _cached_path(
        self,
        *,
        squashfs_mount_dir: Path,
        tarball_target_dir: Path,
    ) -> Path | None:
        """Return an already-materialized path if one exists."""
        if squashfs_mount_dir.is_mount():
            logger.debug(
                "Using cached SquashFS registry mount",
                cache_key=squashfs_mount_dir.name.removeprefix("squashfs-"),
            )
            return squashfs_mount_dir
        if tarball_target_dir.exists():
            logger.debug(
                "Using cached tarball extraction",
                cache_key=tarball_target_dir.name.removeprefix("tarball-"),
            )
            return tarball_target_dir
        return None

    async def _resolve_preferred_artifact(
        self,
        artifact_uri: str,
        *,
        allow_squashfs: bool = True,
    ) -> RegistryArtifact:
        """Prefer SquashFS sidecars when present, otherwise use gzip."""
        if (
            not allow_squashfs
            and _artifact_format(artifact_uri) == RegistryArtifactFormat.SQUASHFS
            and (tarball_uri := _tarball_uri_for_squashfs(artifact_uri))
        ):
            artifact_uri = tarball_uri

        if allow_squashfs and self._can_try_squashfs():
            squashfs_uri = _squashfs_sidecar_uri(artifact_uri)
            if squashfs_uri and await self._sidecar_exists(
                base_uri=artifact_uri,
                sidecar_uri=squashfs_uri,
                artifact_format=RegistryArtifactFormat.SQUASHFS,
            ):
                return RegistryArtifact(
                    uri=squashfs_uri,
                    format=RegistryArtifactFormat.SQUASHFS,
                )

        return RegistryArtifact(uri=artifact_uri, format=_artifact_format(artifact_uri))

    async def _sidecar_exists(
        self,
        *,
        base_uri: str,
        sidecar_uri: str,
        artifact_format: RegistryArtifactFormat,
    ) -> bool:
        """Return whether a registry sidecar exists, logging lookup failures."""
        bucket, key = parse_s3_uri(sidecar_uri)
        try:
            if await blob.file_exists(key=key, bucket=bucket):
                logger.debug(
                    "Using registry artifact sidecar",
                    artifact_uri=base_uri,
                    sidecar_uri=sidecar_uri,
                    artifact_format=artifact_format.value,
                )
                return True
        except Exception as e:
            logger.warning(
                "Failed to check for registry artifact sidecar, falling back",
                artifact_uri=base_uri,
                sidecar_uri=sidecar_uri,
                artifact_format=artifact_format.value,
                error=str(e),
            )

        return False

    def _can_try_squashfs(self) -> bool:
        """Return whether this process should attempt SquashFS registry mounts."""
        return (
            config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED
            and not self._squashfs_mount_disabled
            and shutil.which("mount") is not None
            and _kernel_supports_squashfs()
        )

    async def _materialize_squashfs(
        self,
        *,
        cache_key: str,
        artifact: RegistryArtifact,
        image_path: Path,
        target_dir: Path,
    ) -> Path:
        """Download and mount a SquashFS registry artifact."""
        if target_dir.is_mount():
            return target_dir

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Downloading and mounting SquashFS registry artifact",
            cache_key=cache_key,
            artifact_uri=artifact.uri,
            artifact_format=artifact.format.value,
        )
        start_time = time.monotonic()
        download_elapsed = 0.0

        if not image_path.exists():
            unique_id = id(asyncio.current_task())
            temp_image = self.cache_dir / (
                f"{cache_key}.{os.getpid()}.{unique_id}.squashfs"
            )
            try:
                download_start = time.monotonic()
                await self._download_artifact(artifact.uri, temp_image)
                try:
                    temp_image.rename(image_path)
                except OSError:
                    if not image_path.exists():
                        raise
                download_elapsed = (time.monotonic() - download_start) * 1000
            finally:
                temp_image.unlink(missing_ok=True)

        mount_start = time.monotonic()
        await self._mount_squashfs(image_path, target_dir)
        mount_elapsed = (time.monotonic() - mount_start) * 1000
        total_elapsed = (time.monotonic() - start_time) * 1000

        logger.info(
            "SquashFS registry artifact mounted",
            cache_key=cache_key,
            artifact_uri=artifact.uri,
            artifact_format=artifact.format.value,
            download_ms=f"{download_elapsed:.1f}",
            mount_ms=f"{mount_elapsed:.1f}",
            total_ms=f"{total_elapsed:.1f}",
        )
        return target_dir

    async def _materialize_tarball(
        self,
        *,
        cache_key: str,
        artifact: RegistryArtifact,
        target_dir: Path,
    ) -> Path:
        """Download and extract a tarball registry artifact."""
        suffix = _tarball_suffix(artifact)
        logger.info(
            "Downloading and extracting tarball",
            cache_key=cache_key,
            artifact_uri=artifact.uri,
            artifact_format=artifact.format.value,
        )
        start_time = time.monotonic()

        unique_id = id(asyncio.current_task())
        temp_tarball = self.cache_dir / f"{cache_key}.{os.getpid()}.{unique_id}{suffix}"
        temp_dir = self.cache_dir / f"{cache_key}.{os.getpid()}.{unique_id}.tmp"

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

            download_start = time.monotonic()
            await self._download_artifact(artifact.uri, temp_tarball)
            download_elapsed = (time.monotonic() - download_start) * 1000

            extract_start = time.monotonic()
            temp_dir.mkdir(parents=True, exist_ok=True)
            await self._extract_tarball(temp_tarball, temp_dir)
            extract_elapsed = (time.monotonic() - extract_start) * 1000

            try:
                temp_dir.rename(target_dir)
                total_elapsed = (time.monotonic() - start_time) * 1000
                logger.info(
                    "Tarball extracted and cached",
                    cache_key=cache_key,
                    artifact_uri=artifact.uri,
                    artifact_format=artifact.format.value,
                    download_ms=f"{download_elapsed:.1f}",
                    extract_ms=f"{extract_elapsed:.1f}",
                    total_ms=f"{total_elapsed:.1f}",
                )
            except OSError:
                if target_dir.exists():
                    logger.debug(
                        "Tarball already extracted by another process",
                        cache_key=cache_key,
                        artifact_uri=artifact.uri,
                        artifact_format=artifact.format.value,
                    )
                else:
                    raise
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if temp_tarball.exists():
                temp_tarball.unlink(missing_ok=True)

        return target_dir

    async def _mount_squashfs(self, image_path: Path, target_dir: Path) -> None:
        """Mount a SquashFS image read-only at target_dir."""
        if target_dir.is_mount():
            return

        proc = await asyncio.create_subprocess_exec(
            "mount",
            "-t",
            "squashfs",
            "-o",
            SQUASHFS_MOUNT_OPTIONS,
            str(image_path),
            str(target_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0 or target_dir.is_mount():
            return

        output = (stderr or stdout).decode(errors="replace").strip()
        raise RuntimeError(output or "mount command failed")

    async def _download_artifact(self, artifact_uri: str, output_path: Path) -> None:
        """Download an S3 registry artifact to a local path."""
        bucket, key = parse_s3_uri(artifact_uri)
        await blob.download_file_to_path(
            key=key,
            bucket=bucket,
            output_path=output_path,
        )

    async def _extract_tarball(self, tarball_path: Path, target_dir: Path) -> None:
        """Extract a supported registry tarball to target directory."""

        def _do_extract() -> None:
            if tarball_path.name.endswith(".tar.gz"):
                with tarfile.open(tarball_path, "r:gz") as tar:
                    tar.extractall(path=target_dir, filter="data")
                return

            raise ValueError(f"Unsupported tarball format: {tarball_path}")

        await asyncio.to_thread(_do_extract)
        logger.debug(
            "Tarball extracted",
            target=str(target_dir),
            artifact_format=_artifact_format(str(tarball_path)).value,
        )
