"""Registry artifact resolution and local materialization for executors."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import sysconfig
import tarfile
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import httpx
import tracecat_registry

from tracecat import config
from tracecat.executor.schemas import ExecutorBackendType, resolve_backend_type
from tracecat.logger import logger
from tracecat.registry.artifact_keys import parse_s3_uri
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.storage import blob


class RegistryArtifactFormat(StrEnum):
    """Executor-supported registry artifact encodings."""

    BUILTIN = "builtin"
    SQUASHFS = "squashfs"
    TAR_GZ = "tar.gz"


SQUASHFS_MOUNT_OPTIONS = "loop,ro,nodev,nosuid"
"""Mount options for executor-managed SquashFS registry artifacts.

The image must stay read-only and should not expose device nodes or setuid bits
from registry package contents. Avoid noexec because Python packages may include
native extension modules that need to be loaded from the mounted artifact.
"""

BUNDLED_BUILTIN_REGISTRY_URI_PREFIX = f"tracecat-builtin://{DEFAULT_REGISTRY_ORIGIN}/"
"""Pseudo-URI for the builtin registry already installed in the executor image."""

BASE_PYTHONPATH_DIR_NAME = "base"
"""Cache subdirectory used as the PYTHONPATH entry when no artifact is requested."""

CACHE_ENTRY_PREFIXES = ("squashfs-", "unsquashfs-", "tarball-")
"""On-disk name prefixes owned by a registry artifact cache entry."""

TEMP_ARTIFACT_PATTERN = re.compile(
    r"^[^.]+\.\d+\.\d+\.(?:squashfs|unsquashfs|tar\.gz|tmp)$"
)
"""Matches materialization scratch and doomed eviction paths."""

type MountSlotReleaser = Callable[[str], Awaitable[bool]]
"""Evicts one idle mounted artifact, excluding the given cache key."""


class SquashfsMountCommandError(RuntimeError):
    """The ``mount`` command itself failed for a SquashFS registry artifact.

    Only this error drives SquashFS mount policy. Download, mkdir, and other
    preparation failures must not be mistaken for a missing mount capability or
    for loop-device exhaustion.
    """


@dataclass(frozen=True, slots=True)
class RegistryArtifactPaths:
    """Executor-local cache paths for one registry artifact key."""

    squashfs_image_path: Path
    squashfs_mount_dir: Path
    squashfs_extract_dir: Path
    tarball_target_dir: Path


@dataclass(slots=True)
class SquashfsMountState:
    """Shared process-local SquashFS mount state."""

    disabled: bool = False
    mounted_once: bool = False
    probe_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(slots=True)
class RegistryArtifactLease:
    """In-process lease bookkeeping for one registry artifact cache key."""

    refcount: int = 0
    last_used: float = 0.0


@dataclass(frozen=True, slots=True)
class RegistryArtifactCacheEntry:
    """Measured on-disk footprint and recency for one registry artifact key."""

    cache_key: str
    size_bytes: int
    last_used: float


@dataclass(slots=True)
class RegistryArtifactMaterializationContext:
    """Shared runtime state for artifact materialization."""

    cache_key: str
    cache_dir: Path
    paths: RegistryArtifactPaths
    squashfs_mount_state: SquashfsMountState
    mount_slot_releaser: MountSlotReleaser | None = None

    def can_mount_squashfs(self) -> bool:
        return (
            config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED
            and not self.squashfs_mount_state.disabled
            and (shutil.which("mount") is not None)
        )

    def disable_squashfs_mount(self) -> None:
        """Disable mounting after the serialized first capability probe fails."""
        self.squashfs_mount_state.disabled = True

    def record_squashfs_mount(self) -> None:
        """Record that this process has successfully mounted a SquashFS image."""
        self.squashfs_mount_state.mounted_once = True

    def has_mounted_squashfs(self) -> bool:
        """Return whether any SquashFS mount has ever succeeded in this process."""
        return self.squashfs_mount_state.mounted_once

    async def release_mounted_slot(self) -> bool:
        """Evict one idle mounted artifact to free a loop device."""
        if self.mount_slot_releaser is None:
            return False
        return await self.mount_slot_releaser(self.cache_key)


@dataclass(frozen=True, slots=True)
class RegistryArtifact(ABC):
    """An executor-local materializable registry artifact."""

    uri: str
    cache_key: str

    @property
    @abstractmethod
    def format(self) -> RegistryArtifactFormat:
        """Artifact format used for logging and dispatch."""

    @abstractmethod
    def cached_path(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path] | None:
        """Return already-materialized import paths for this artifact, if present."""

    @abstractmethod
    async def materialize(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path]:
        """Return importable Python paths, materializing the artifact if needed."""

    def _temp_path(
        self,
        ctx: RegistryArtifactMaterializationContext,
        suffix: str,
    ) -> Path:
        unique_id = id(asyncio.current_task())
        return ctx.cache_dir / f"{self.cache_key}.{os.getpid()}.{unique_id}{suffix}"


@dataclass(frozen=True, slots=True)
class BuiltinArtifact(RegistryArtifact):
    """Current builtin registry package already installed in the executor image."""

    version: str

    @property
    def format(self) -> RegistryArtifactFormat:
        return RegistryArtifactFormat.BUILTIN

    def cached_path(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path] | None:
        return None

    async def materialize(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path]:
        del ctx
        import_paths = _bundled_builtin_registry_import_paths(self.version)
        logger.info(
            "Using bundled builtin registry environment",
            registry_version=self.version,
            paths=[str(p) for p in import_paths],
        )
        return import_paths


@dataclass(frozen=True, slots=True)
class SquashfsArtifact(RegistryArtifact):
    """SquashFS registry environment image."""

    @property
    def format(self) -> RegistryArtifactFormat:
        return RegistryArtifactFormat.SQUASHFS

    def cached_path(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path] | None:
        if ctx.paths.squashfs_mount_dir.is_mount():
            logger.debug(
                "Using cached SquashFS registry mount",
                cache_key=ctx.cache_key,
            )
            return [ctx.paths.squashfs_mount_dir]
        if ctx.paths.squashfs_extract_dir.exists():
            logger.debug(
                "Using cached SquashFS registry extraction",
                cache_key=ctx.cache_key,
            )
            return [ctx.paths.squashfs_extract_dir]
        return None

    async def materialize(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path]:
        image_path = ctx.paths.squashfs_image_path
        if ctx.can_mount_squashfs():
            if (mount_dir := await self._try_mount(ctx, image_path)) is not None:
                return [mount_dir]

        return [await self.extract(ctx, image_path)]

    async def _try_mount(
        self,
        ctx: RegistryArtifactMaterializationContext,
        image_path: Path,
    ) -> Path | None:
        """Mount the image, retrying once after reclaiming a loop device.

        The first mount-command failure in a process is treated as a capability
        probe and disables mounting process-wide. Once any mount has succeeded,
        later failures are attributed to exhausted loop devices instead: one idle
        mounted artifact is evicted and the mount is retried once, so a single
        failure never downgrades the whole process to extraction.

        The probe lock deliberately covers the first mount's download and mount
        command. It nests inside a per-key materialization lock and acquires no
        other lock; reclaim and retry remain outside it.

        Only ``SquashfsMountCommandError`` drives this policy. Download and
        preparation errors propagate to the caller so a transient S3 failure
        never disables mounting or evicts an unrelated idle mount.

        Args:
            ctx: Materialization context for the artifact being mounted.
            image_path: Local path of the SquashFS image.

        Returns:
            The mount directory, or None if the caller should extract instead.

        Raises:
            Exception: Any non-mount failure raised while preparing the image.
        """
        if not ctx.has_mounted_squashfs():
            async with ctx.squashfs_mount_state.probe_lock:
                if ctx.squashfs_mount_state.disabled:
                    return None
                if not ctx.has_mounted_squashfs():
                    try:
                        return await self.mount(ctx, image_path)
                    except SquashfsMountCommandError as e:
                        # This is the only disable path: the probe lock is held
                        # and no mount has succeeded, so disabled and mounted_once
                        # cannot both become true.
                        ctx.disable_squashfs_mount()
                        logger.warning(
                            "Failed to mount SquashFS registry artifact, trying extraction",
                            cache_key=ctx.cache_key,
                            artifact_uri=self.uri,
                            artifact_format=self.format.value,
                            error=str(e),
                        )
                        return None

        try:
            return await self.mount(ctx, image_path)
        except SquashfsMountCommandError as e:
            mount_error = e

        logger.warning(
            "Failed to mount SquashFS registry artifact, reclaiming an idle mount",
            cache_key=ctx.cache_key,
            artifact_uri=self.uri,
            artifact_format=self.format.value,
            error=str(mount_error),
        )
        if not await ctx.release_mounted_slot():
            logger.warning(
                "No idle SquashFS mount to reclaim, trying extraction",
                cache_key=ctx.cache_key,
                artifact_uri=self.uri,
                artifact_format=self.format.value,
            )
            return None

        try:
            return await self.mount(ctx, image_path)
        except SquashfsMountCommandError as e:
            logger.warning(
                "SquashFS mount retry failed, trying extraction",
                cache_key=ctx.cache_key,
                artifact_uri=self.uri,
                artifact_format=self.format.value,
                error=str(e),
            )
            return None

    async def download(
        self,
        ctx: RegistryArtifactMaterializationContext,
        image_path: Path,
    ) -> float:
        """Ensure the SquashFS image exists locally and return download time."""
        if image_path.exists():
            return 0.0

        temp_image = self._temp_path(ctx, ".squashfs")
        try:
            download_start = time.monotonic()
            await _download_s3_artifact(self.uri, temp_image)
            try:
                temp_image.rename(image_path)
            except OSError:
                if not image_path.exists():
                    raise
            return (time.monotonic() - download_start) * 1000
        finally:
            temp_image.unlink(missing_ok=True)

    async def mount(
        self,
        ctx: RegistryArtifactMaterializationContext,
        image_path: Path,
    ) -> Path:
        """Download the image if needed and mount it read-only.

        Args:
            ctx: Materialization context for the artifact being mounted.
            image_path: Local path of the SquashFS image.

        Returns:
            The mount directory.

        Raises:
            SquashfsMountCommandError: The ``mount`` command failed.
            Exception: The image could not be downloaded or prepared.
        """
        target_dir = ctx.paths.squashfs_mount_dir
        if target_dir.is_mount():
            ctx.record_squashfs_mount()
            return target_dir

        ctx.cache_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Materializing SquashFS registry artifact",
            cache_key=ctx.cache_key,
            artifact_uri=self.uri,
            artifact_format=self.format.value,
        )
        start_time = time.monotonic()
        download_elapsed = await self.download(ctx, image_path)

        mount_start = time.monotonic()
        await self._mount_image(image_path, target_dir)
        ctx.record_squashfs_mount()
        mount_elapsed = (time.monotonic() - mount_start) * 1000
        total_elapsed = (time.monotonic() - start_time) * 1000

        logger.info(
            "SquashFS registry artifact mounted",
            cache_key=ctx.cache_key,
            artifact_uri=self.uri,
            artifact_format=self.format.value,
            download_ms=f"{download_elapsed:.1f}",
            mount_ms=f"{mount_elapsed:.1f}",
            total_ms=f"{total_elapsed:.1f}",
        )
        return target_dir

    async def extract(
        self,
        ctx: RegistryArtifactMaterializationContext,
        image_path: Path,
    ) -> Path:
        target_dir = ctx.paths.squashfs_extract_dir
        if target_dir.exists():
            return target_dir

        ctx.cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Extracting SquashFS registry artifact",
            cache_key=ctx.cache_key,
            artifact_uri=self.uri,
            artifact_format=self.format.value,
        )
        start_time = time.monotonic()
        download_elapsed = await self.download(ctx, image_path)

        temp_dir = self._temp_path(ctx, ".unsquashfs")
        try:
            extract_start = time.monotonic()
            temp_dir.mkdir(parents=True, exist_ok=True)
            await self._extract_image(image_path, temp_dir)
            extract_elapsed = (time.monotonic() - extract_start) * 1000

            try:
                temp_dir.rename(target_dir)
                total_elapsed = (time.monotonic() - start_time) * 1000
                logger.info(
                    "SquashFS registry artifact extracted",
                    cache_key=ctx.cache_key,
                    artifact_uri=self.uri,
                    artifact_format=self.format.value,
                    download_ms=f"{download_elapsed:.1f}",
                    extract_ms=f"{extract_elapsed:.1f}",
                    total_ms=f"{total_elapsed:.1f}",
                )
            except OSError:
                if target_dir.exists():
                    logger.debug(
                        "SquashFS already extracted by another process",
                        cache_key=ctx.cache_key,
                        artifact_uri=self.uri,
                        artifact_format=self.format.value,
                    )
                else:
                    raise
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

        return target_dir

    async def _mount_image(self, image_path: Path, target_dir: Path) -> None:
        """Mount a SquashFS image read-only at target_dir.

        Cancellation kills and reaps the mount subprocess before propagating,
        so the caller's per-key lock covers the complete mount lifecycle. The
        target therefore remains an unmounted cache miss if mount never took
        effect, or is already mounted and reusable by the next admission.

        Args:
            image_path: Local path of the SquashFS image.
            target_dir: Existing directory to mount the image at.

        Raises:
            SquashfsMountCommandError: The ``mount`` command failed.
        """
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
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise

        if proc.returncode == 0 or target_dir.is_mount():
            return

        output = (stderr or stdout).decode(errors="replace").strip()
        raise SquashfsMountCommandError(output or "mount command failed")

    async def _extract_image(self, image_path: Path, target_dir: Path) -> None:
        """Extract a SquashFS image to target_dir using unsquashfs."""
        unsquashfs = shutil.which("unsquashfs")
        if unsquashfs is None:
            raise RuntimeError("unsquashfs command is not installed")

        proc = await asyncio.create_subprocess_exec(
            unsquashfs,
            "-f",
            "-d",
            str(target_dir),
            str(image_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return

        output = (stderr or stdout).decode(errors="replace").strip()
        raise RuntimeError(output or "unsquashfs command failed")


@dataclass(frozen=True, slots=True)
class TarballArtifact(RegistryArtifact):
    """Legacy gzip tarball registry environment."""

    @property
    def format(self) -> RegistryArtifactFormat:
        return RegistryArtifactFormat.TAR_GZ

    def cached_path(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path] | None:
        if ctx.paths.tarball_target_dir.exists():
            logger.debug(
                "Using cached tarball extraction",
                cache_key=ctx.cache_key,
            )
            return [ctx.paths.tarball_target_dir]
        return None

    async def materialize(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path]:
        target_dir = ctx.paths.tarball_target_dir
        logger.info(
            "Materializing tarball registry artifact",
            cache_key=ctx.cache_key,
            artifact_uri=self.uri,
            artifact_format=self.format.value,
        )
        start_time = time.monotonic()

        temp_tarball = self._temp_path(ctx, ".tar.gz")
        temp_dir = self._temp_path(ctx, ".tmp")

        try:
            ctx.cache_dir.mkdir(parents=True, exist_ok=True)

            download_start = time.monotonic()
            await self.download(ctx, temp_tarball)
            download_elapsed = (time.monotonic() - download_start) * 1000

            extract_start = time.monotonic()
            temp_dir.mkdir(parents=True, exist_ok=True)
            await self.extract(temp_tarball, temp_dir)
            extract_elapsed = (time.monotonic() - extract_start) * 1000

            try:
                temp_dir.rename(target_dir)
                total_elapsed = (time.monotonic() - start_time) * 1000
                logger.info(
                    "Tarball extracted and cached",
                    cache_key=ctx.cache_key,
                    artifact_uri=self.uri,
                    artifact_format=self.format.value,
                    download_ms=f"{download_elapsed:.1f}",
                    extract_ms=f"{extract_elapsed:.1f}",
                    total_ms=f"{total_elapsed:.1f}",
                )
            except OSError:
                if target_dir.exists():
                    logger.debug(
                        "Tarball already extracted by another process",
                        cache_key=ctx.cache_key,
                        artifact_uri=self.uri,
                        artifact_format=self.format.value,
                    )
                else:
                    raise
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if temp_tarball.exists():
                temp_tarball.unlink(missing_ok=True)

        return [target_dir]

    async def download(
        self,
        ctx: RegistryArtifactMaterializationContext,
        output_path: Path,
    ) -> None:
        await _download_s3_artifact(self.uri, output_path)

    async def extract(self, tarball_path: Path, target_dir: Path) -> None:
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


async def _download_s3_artifact(artifact_uri: str, output_path: Path) -> None:
    """Download an S3 registry artifact to a local path."""
    bucket, key = parse_s3_uri(artifact_uri)
    try:
        await blob.download_file_to_path(
            key=key,
            bucket=bucket,
            output_path=output_path,
        )
    except FileNotFoundError as e:
        request = httpx.Request("GET", artifact_uri)
        response = httpx.Response(status_code=404, request=request)
        raise httpx.HTTPStatusError(
            f"Registry artifact not found: {artifact_uri}",
            request=request,
            response=response,
        ) from e


def compute_registry_artifact_cache_key(artifact_uri: str) -> str:
    """Compute the local cache key for a registry artifact URI."""
    if not artifact_uri:
        return "base"
    # S3 keys are case-sensitive, so preserve URI case when hashing.
    content = artifact_uri.strip()
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def bundled_builtin_registry_uri(version: str) -> str:
    """Return the pseudo-URI for the installed builtin registry package."""
    return f"{BUNDLED_BUILTIN_REGISTRY_URI_PREFIX}{version}"


def _bundled_builtin_registry_version(artifact_uri: str) -> str | None:
    """Return the builtin registry version encoded in a bundled pseudo-URI."""
    if not artifact_uri.startswith(BUNDLED_BUILTIN_REGISTRY_URI_PREFIX):
        return None
    version = artifact_uri.removeprefix(BUNDLED_BUILTIN_REGISTRY_URI_PREFIX)
    return version or None


def _bundled_builtin_registry_import_paths(version: str) -> list[Path]:
    """Return import paths for the current builtin registry and its dependencies.

    Dependencies always live in the executor's site-packages. For editable
    installs the parent of ``package_dir`` (the package wrapper, e.g.
    ``packages/tracecat-registry/``) is exposed first so its ``tracecat_registry/``
    shadows any stale copy in site-packages.
    """
    installed_version = tracecat_registry.__version__
    if version != installed_version:
        raise RuntimeError(
            "Bundled builtin registry version does not match installed version: "
            f"requested={version!r}, installed={installed_version!r}"
        )

    package_file = tracecat_registry.__file__
    if package_file is None:
        raise RuntimeError("Installed tracecat_registry package has no __file__")

    site_packages_path = sysconfig.get_path("purelib")
    if site_packages_path is None:
        raise RuntimeError("Could not resolve installed Python site-packages path")

    site_packages = Path(site_packages_path).resolve()
    if not site_packages.exists():
        raise RuntimeError(
            f"Installed Python site-packages path does not exist: {site_packages}"
        )

    package_dir = Path(package_file).resolve().parent
    if package_dir.is_relative_to(site_packages):
        return [site_packages]

    return [package_dir.parent, site_packages]


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


def _is_cache_entry_uri(artifact_uri: str) -> bool:
    """Return whether an artifact URI materializes into an evictable cache entry.

    The bundled builtin registry is served from the executor image and never
    writes into the cache directory, so it is exempt from eviction accounting.
    """
    return _bundled_builtin_registry_version(artifact_uri) is None


def _cache_key_from_entry_name(name: str) -> str | None:
    """Return the cache key owning a cache directory entry name, if any."""
    if TEMP_ARTIFACT_PATTERN.fullmatch(name) is not None:
        return None
    for prefix in CACHE_ENTRY_PREFIXES:
        if name.startswith(prefix):
            cache_key = name.removeprefix(prefix).removesuffix(".squashfs")
            return cache_key or None
    return None


def _directory_footprint(directory: Path) -> tuple[int, float]:
    """Return the total file size and local creation time of a cache directory.

    Directory ``mtime`` values inside extracted artifacts come from the artifact
    build, so ``ctime`` (updated when the staging directory is renamed into
    place) is used as the local recency signal instead.

    Args:
        directory: Cache directory to measure.

    Returns:
        Total byte size of contained files and the directory's ctime, or
        ``(0, 0.0)`` when the directory is missing.
    """
    if not directory.is_dir():
        return 0, 0.0

    try:
        created_at = directory.stat().st_ctime
    except OSError:
        created_at = 0.0

    total_bytes = 0
    for root, _dirs, files in os.walk(directory):
        for file_name in files:
            try:
                total_bytes += os.lstat(os.path.join(root, file_name)).st_size
            except OSError:
                continue
    return total_bytes, created_at


def _delete_entry_paths(paths: RegistryArtifactPaths) -> None:
    """Delete every on-disk path owned by a registry artifact cache key.

    The caller must unmount ``squashfs_mount_dir`` first: unlinking the image
    file behind a live mount leaves an open-file zombie pinning a loop device.
    """
    shutil.rmtree(paths.squashfs_extract_dir, ignore_errors=True)
    shutil.rmtree(paths.tarball_target_dir, ignore_errors=True)
    paths.squashfs_image_path.unlink(missing_ok=True)
    shutil.rmtree(paths.squashfs_mount_dir, ignore_errors=True)


def _rename_entry_paths(
    paths: RegistryArtifactPaths,
    *,
    cache_dir: Path,
    cache_key: str,
) -> RegistryArtifactPaths:
    """Synchronously rename live entry paths to unique startup-sweep scratch."""
    unique_id = time.time_ns()
    while True:
        doomed_paths = RegistryArtifactPaths(
            squashfs_image_path=cache_dir
            / f"{cache_key}.{os.getpid()}.{unique_id}.squashfs",
            squashfs_mount_dir=cache_dir / f"{cache_key}.{os.getpid()}.{unique_id}.tmp",
            squashfs_extract_dir=cache_dir
            / f"{cache_key}.{os.getpid()}.{unique_id}.unsquashfs",
            tarball_target_dir=cache_dir
            / f"{cache_key}.{os.getpid()}.{unique_id}.tar.gz",
        )
        if not any(
            path.exists()
            for path in (
                doomed_paths.squashfs_image_path,
                doomed_paths.squashfs_mount_dir,
                doomed_paths.squashfs_extract_dir,
                doomed_paths.tarball_target_dir,
            )
        ):
            break
        unique_id += 1

    for source, target in (
        (paths.squashfs_extract_dir, doomed_paths.squashfs_extract_dir),
        (paths.tarball_target_dir, doomed_paths.tarball_target_dir),
        (paths.squashfs_image_path, doomed_paths.squashfs_image_path),
        (paths.squashfs_mount_dir, doomed_paths.squashfs_mount_dir),
    ):
        if source.exists():
            source.rename(target)

    return doomed_paths


class RegistryArtifactCache:
    """Materializes registry artifacts into executor-local Python paths."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        # Per-key locks live for the process lifetime: eviction and lease
        # admission must serialize on the same object for a given key, so a
        # lock is never dropped and re-created underneath a waiter.
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()
        self._budget_lock = asyncio.Lock()
        # Guard the off-loop startup sweep independently from cache operations.
        self._swept: bool = False
        self._sweep_lock = asyncio.Lock()
        self._squashfs_mount_state = SquashfsMountState()
        self._leases: dict[str, RegistryArtifactLease] = {}
        # Whether the on-disk cache may exceed its budget. Set when a new entry
        # is materialized and cleared once enforcement measures a cache that
        # fits, so steady-state cache hits never pay for a disk scan.
        self._budget_dirty = True

    async def ensure_swept(self) -> None:
        """Run the startup sweep exactly once, off the event loop.

        Idempotent and safe under concurrency: the first caller performs the
        sweep in a thread; concurrent callers wait; later callers return
        immediately. Runs before the first lease or materialization so the
        sweep never observes in-flight cache entries.
        """
        if self._swept:
            return
        async with self._sweep_lock:
            if self._swept:
                return
            await asyncio.to_thread(self._sweep_startup_state)
            self._swept = True

    @asynccontextmanager
    async def lease(self, artifact_uris: list[str] | None) -> AsyncIterator[list[Path]]:
        """Materialize registry artifacts and pin them for the life of the context.

        Leased cache entries are never evicted, so callers may keep importing
        from the returned paths until the context exits.

        Args:
            artifact_uris: Registry artifact URIs in deterministic PYTHONPATH
                order, or None to use the base PYTHONPATH directory.

        Yields:
            Importable Python paths for the requested artifacts.
        """
        await self.ensure_swept()

        if not artifact_uris:
            logger.info("No registry artifact URIs provided, using base PYTHONPATH")
            yield [self._base_pythonpath_dir()]
            return

        leased_keys: list[str] = []
        try:
            registry_paths: list[Path] = []
            for artifact_uri in artifact_uris:
                cache_key = compute_registry_artifact_cache_key(artifact_uri)
                if not _is_cache_entry_uri(artifact_uri):
                    registry_paths.extend(
                        await self.materialize(cache_key, artifact_uri)
                    )
                    continue

                cached_paths = await self._admit_lease(cache_key, artifact_uri)
                leased_keys.append(cache_key)
                if cached_paths is not None:
                    registry_paths.extend(cached_paths)
                    continue
                registry_paths.extend(await self.materialize(cache_key, artifact_uri))
            logger.info(
                "Using registry artifact environments",
                count=len(registry_paths),
            )
            yield registry_paths
        finally:
            for cache_key in leased_keys:
                self._release_lease(cache_key)
            if leased_keys:
                await self._converge_cache_budget()

    async def _admit_lease(
        self, cache_key: str, artifact_uri: str
    ) -> list[Path] | None:
        """Pin a cache entry and return its already-materialized paths, if any.

        The refcount increment and the cached-path check run under the same
        per-key lock that eviction holds. An in-flight eviction therefore always
        completes before a lease is admitted, and once the refcount is raised no
        eviction can delete the entry, so a lease can never be handed a path
        that is about to disappear.

        Args:
            cache_key: Cache key to pin.
            artifact_uri: Registry artifact URI backing the cache key.

        Returns:
            Importable paths when the entry is already materialized, else None.

        Raises:
            BaseException: Any failure or cancellation while resolving artifact
                candidates. The lease is released before it propagates.
        """
        lock = await self._lock_for(cache_key)
        async with lock:
            self._acquire_lease(cache_key)
            try:
                ctx = self._context_for(cache_key)
                candidates = await self._artifact_candidates(ctx, artifact_uri)
            except BaseException:
                # Cancellation is a BaseException and must not leak the pin.
                self._release_lease(cache_key)
                raise
            return self._first_cached_path(candidates, ctx)

    async def materialize(self, cache_key: str, artifact_uri: str) -> list[Path]:
        """Materialize a registry artifact as local importable directories."""
        await self.ensure_swept()

        ctx = self._context_for(cache_key)
        candidates = await self._artifact_candidates(ctx, artifact_uri)

        if cached_paths := self._first_cached_path(candidates, ctx):
            return cached_paths

        if _is_cache_entry_uri(artifact_uri):
            # Make room before downloading or expanding a new entry. This runs
            # outside the per-key lock so eviction never nests key locks.
            await self._enforce_cache_budget(protected_key=cache_key)

        lock = await self._lock_for(cache_key)
        async with lock:
            candidates = await self._artifact_candidates(ctx, artifact_uri)
            if cached_paths := self._first_cached_path(candidates, ctx):
                return cached_paths

            for index, artifact in enumerate(candidates):
                try:
                    logger.info(
                        "Trying registry artifact candidate",
                        cache_key=cache_key,
                        artifact_uri=artifact.uri,
                        artifact_format=artifact.format.value,
                        candidate=index + 1,
                        candidates=len(candidates),
                    )
                    registry_paths = await artifact.materialize(ctx)
                    if _is_cache_entry_uri(artifact.uri):
                        # A new entry landed on disk after the budget was
                        # measured, so the cache must be re-checked once the
                        # entry goes idle.
                        self._budget_dirty = True
                    return registry_paths
                except Exception as e:
                    if index == len(candidates) - 1:
                        raise
                    logger.warning(
                        "Failed to materialize registry artifact candidate, trying fallback",
                        cache_key=cache_key,
                        artifact_uri=artifact.uri,
                        artifact_format=artifact.format.value,
                        error=str(e),
                    )

        raise RuntimeError(f"No registry artifact candidates for {artifact_uri}")

    async def _lock_for(self, cache_key: str) -> asyncio.Lock:
        """Get or create a lock for the given cache key."""
        async with self._locks_lock:
            if cache_key not in self._locks:
                self._locks[cache_key] = asyncio.Lock()
            return self._locks[cache_key]

    def _context_for(self, cache_key: str) -> RegistryArtifactMaterializationContext:
        """Return a materialization context for a registry artifact key."""
        return RegistryArtifactMaterializationContext(
            cache_key=cache_key,
            cache_dir=self.cache_dir,
            paths=self._paths_for(cache_key),
            squashfs_mount_state=self._squashfs_mount_state,
            mount_slot_releaser=self._release_mounted_slot,
        )

    def _base_pythonpath_dir(self) -> Path:
        """Return the base PYTHONPATH directory used when no artifact is requested."""
        base_dir = self.cache_dir / BASE_PYTHONPATH_DIR_NAME
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    def _acquire_lease(self, cache_key: str) -> None:
        """Pin a cache entry against eviction and mark it as recently used.

        Callers must hold the per-key lock so the increment is ordered against
        in-flight eviction of the same key.
        """
        lease = self._leases.setdefault(cache_key, RegistryArtifactLease())
        lease.refcount += 1
        lease.last_used = time.time()
        self._touch_image(cache_key)

    def _release_lease(self, cache_key: str) -> None:
        """Release one pin on a cache entry."""
        lease = self._leases.get(cache_key)
        if lease is None:
            return
        lease.refcount = max(0, lease.refcount - 1)
        lease.last_used = time.time()

    def _refcount(self, cache_key: str) -> int:
        """Return the number of live leases on a cache entry."""
        lease = self._leases.get(cache_key)
        return 0 if lease is None else lease.refcount

    def _touch_image(self, cache_key: str) -> None:
        """Best-effort refresh of an artifact image mtime for restart-safe LRU.

        Only the downloaded image file has a locally meaningful mtime; mount and
        extraction directory timestamps come from the artifact build.
        """
        image_path = self._paths_for(cache_key).squashfs_image_path
        try:
            os.utime(image_path)
        except OSError:
            logger.debug(
                "Could not refresh registry artifact image mtime",
                cache_key=cache_key,
            )

    def _paths_for(self, cache_key: str) -> RegistryArtifactPaths:
        """Return local cache paths for a registry artifact key."""
        return RegistryArtifactPaths(
            squashfs_image_path=self.cache_dir / f"squashfs-{cache_key}.squashfs",
            squashfs_mount_dir=self.cache_dir / f"squashfs-{cache_key}",
            squashfs_extract_dir=self.cache_dir / f"unsquashfs-{cache_key}",
            tarball_target_dir=self.cache_dir / f"tarball-{cache_key}",
        )

    def _first_cached_path(
        self,
        candidates: list[RegistryArtifact],
        ctx: RegistryArtifactMaterializationContext,
    ) -> list[Path] | None:
        """Return the first already-materialized candidate paths."""
        for artifact in candidates:
            if cached_paths := artifact.cached_path(ctx):
                return cached_paths
        return None

    async def _artifact_candidates(
        self,
        ctx: RegistryArtifactMaterializationContext,
        artifact_uri: str,
    ) -> list[RegistryArtifact]:
        """Return artifact candidates in executor preference order."""
        if version := _bundled_builtin_registry_version(artifact_uri):
            return [
                BuiltinArtifact(
                    uri=artifact_uri,
                    cache_key=ctx.cache_key,
                    version=version,
                )
            ]

        artifact_format = _artifact_format(artifact_uri)
        if artifact_format == RegistryArtifactFormat.SQUASHFS:
            candidates = [
                SquashfsArtifact(
                    uri=artifact_uri,
                    cache_key=ctx.cache_key,
                )
            ]
            if tarball_uri := _tarball_uri_for_squashfs(artifact_uri):
                candidates.append(
                    TarballArtifact(
                        uri=tarball_uri,
                        cache_key=ctx.cache_key,
                    )
                )
            return candidates

        candidates: list[RegistryArtifact] = []
        if self._can_try_squashfs():
            squashfs_uri = _squashfs_sidecar_uri(artifact_uri)
            if squashfs_uri:
                if ctx.paths.squashfs_image_path.exists():
                    candidates.append(
                        SquashfsArtifact(
                            uri=squashfs_uri,
                            cache_key=ctx.cache_key,
                        )
                    )
                elif await self._sidecar_exists(
                    base_uri=artifact_uri,
                    sidecar_uri=squashfs_uri,
                    artifact_format=RegistryArtifactFormat.SQUASHFS,
                ):
                    candidates.append(
                        SquashfsArtifact(
                            uri=squashfs_uri,
                            cache_key=ctx.cache_key,
                        )
                    )

        candidates.append(
            TarballArtifact(
                uri=artifact_uri,
                cache_key=ctx.cache_key,
            )
        )
        return candidates

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
        """Return whether this process should prefer SquashFS artifacts."""
        return config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED

    async def _converge_cache_budget(self) -> None:
        """Bring an idle cache back under budget after a lease is released.

        Materialization enforces the budget before a new entry exists, so the
        cache can legitimately sit over budget while that entry is leased. This
        runs on release, when the real on-disk size is known and the entry is
        evictable. The scan is skipped entirely unless a new entry has landed
        since the last successful enforcement.

        Each successful pass consumes the dirty signal before its awaited scan.
        A follow-up pass therefore occurs only when a concurrent materialization
        sets the flag again. Without new materializations the loop terminates,
        while an over-budget or failed scan restores the flag and breaks so it
        cannot spin while entries remain leased. Cancellation also restores the
        consumed flag before propagating.
        """
        while self._budget_dirty:
            self._budget_dirty = False
            try:
                within_budget = await self._enforce_cache_budget()
            except OSError as e:
                logger.warning(
                    "Failed to converge registry artifact cache to budget",
                    cache_dir=str(self.cache_dir),
                    error=str(e),
                )
                self._budget_dirty = True
                break
            except BaseException:
                # Cancellation must re-arm the consumed dirty signal before propagating.
                self._budget_dirty = True
                raise

            if not within_budget:
                self._budget_dirty = True
                break

    async def _enforce_cache_budget(self, *, protected_key: str | None = None) -> bool:
        """Evict least-recently-used idle entries until the cache fits its budget.

        The budget lock serializes the complete scan/select/evict pass. It is
        always acquired before any candidate's per-key lock, and callers must
        invoke enforcement without holding a per-key lock.

        Args:
            protected_key: Cache key about to be materialized. It is counted
                against the budget but never evicted. None when enforcing
                against the entries already on disk.

        Returns:
            Whether the cache is within budget once eviction has finished.
        """
        async with self._budget_lock:
            max_entries = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES
            max_bytes = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES
            if max_entries <= 0 and max_bytes <= 0:
                return True

            entries = await asyncio.to_thread(self._scan_cache_entries)
            # The protected key is not on disk yet when it is a fresh entry.
            pending_entries = (
                1 if protected_key is not None and protected_key not in entries else 0
            )
            total_bytes = sum(entry.size_bytes for entry in entries.values())
            protected = set() if protected_key is None else {protected_key}
            skipped: set[str] = set()

            while (
                max_entries > 0 and len(entries) + pending_entries > max_entries
            ) or (max_bytes > 0 and total_bytes > max_bytes):
                candidate = self._least_recently_used(
                    entries.values(),
                    excluded=skipped | protected,
                )
                if candidate is None:
                    logger.warning(
                        "Registry artifact cache is over budget but every entry is in use",
                        cache_dir=str(self.cache_dir),
                        entries=len(entries) + pending_entries,
                        max_entries=max_entries,
                        total_bytes=total_bytes,
                        max_bytes=max_bytes,
                    )
                    return False

                if await self._evict_entry(candidate.cache_key):
                    del entries[candidate.cache_key]
                    total_bytes -= candidate.size_bytes
                else:
                    skipped.add(candidate.cache_key)

            return True

    async def _release_mounted_slot(self, protected_key: str) -> bool:
        """Evict one idle mounted artifact so its loop device can be reused.

        This path deliberately does not take the budget lock: ``_try_mount`` may
        call it while holding ``protected_key``'s per-key lock. It excludes that
        key and only tries candidate per-key locks, so it cannot invert the
        budget-lock-to-per-key-lock ordering used by budget enforcement.

        Args:
            protected_key: Cache key that must not be evicted.

        Returns:
            Whether a mounted artifact was unmounted and removed.
        """
        entries = await asyncio.to_thread(self._scan_cache_entries)
        mounted = [
            entry
            for entry in entries.values()
            if entry.cache_key != protected_key
            and self._paths_for(entry.cache_key).squashfs_mount_dir.is_mount()
        ]
        skipped: set[str] = set()
        while (
            candidate := self._least_recently_used(mounted, excluded=skipped)
        ) is not None:
            if await self._evict_entry(candidate.cache_key):
                return True
            skipped.add(candidate.cache_key)
        return False

    def _is_pool_worker_visible(
        self,
        cache_key: str,
        backend_type: ExecutorBackendType,
    ) -> bool:
        """Return whether a warm pool worker may retain this tarball path.

        Pool workers may start before the cache is constructed, then inherit
        tarball paths that remain invisible to in-process lease refcounts. Both
        runtime eviction and startup trimming must therefore protect these paths
        whenever the resolved backend is the pool.
        """
        return (
            backend_type == ExecutorBackendType.POOL
            and self._paths_for(cache_key).tarball_target_dir.exists()
        )

    def _least_recently_used(
        self,
        entries: Iterable[RegistryArtifactCacheEntry],
        *,
        excluded: set[str],
    ) -> RegistryArtifactCacheEntry | None:
        """Return the least recently used idle entry eligible for eviction."""
        backend_type = resolve_backend_type()
        eligible = [
            entry
            for entry in entries
            if entry.cache_key not in excluded
            and self._refcount(entry.cache_key) == 0
            and not self._is_pool_worker_visible(entry.cache_key, backend_type)
        ]
        if not eligible:
            return None
        return min(eligible, key=self._recency)

    def _recency(self, entry: RegistryArtifactCacheEntry) -> float:
        """Return the most recent known use time for a cache entry."""
        lease = self._leases.get(entry.cache_key)
        if lease is None:
            return entry.last_used
        return max(entry.last_used, lease.last_used)

    async def _evict_entry(self, cache_key: str) -> bool:
        """Remove one cache entry from disk, unmounting it first.

        The entry is skipped rather than forced when it is leased, busy, or
        cannot be unmounted: deleting the image file behind a live mount would
        leave an open-file zombie holding the loop device.

        After unmounting, live paths are synchronously renamed under the per-key
        lock to scratch names ignored by cache discovery and budget accounting.
        The lease record is then dropped and the lock released before physical
        deletion runs in a worker thread. If that await is cancelled, the live
        key remains a clean cache miss and the next startup sweep removes any
        doomed scratch left behind.

        Args:
            cache_key: Cache key to evict.

        Returns:
            Whether the entry was removed.
        """
        lock = await self._lock_for(cache_key)
        if lock.locked():
            logger.debug(
                "Skipping eviction of busy registry artifact",
                cache_key=cache_key,
            )
            return False

        async with lock:
            if self._refcount(cache_key) > 0:
                return False

            paths = self._paths_for(cache_key)
            if paths.squashfs_mount_dir.is_mount() and not await self._unmount(
                paths.squashfs_mount_dir
            ):
                logger.warning(
                    "Failed to unmount registry artifact, skipping eviction",
                    cache_key=cache_key,
                    mount_dir=str(paths.squashfs_mount_dir),
                )
                return False

            doomed_paths = _rename_entry_paths(
                paths,
                cache_dir=self.cache_dir,
                cache_key=cache_key,
            )
            self._leases.pop(cache_key, None)
            logger.info("Evicted registry artifact from cache", cache_key=cache_key)

        await asyncio.to_thread(_delete_entry_paths, doomed_paths)
        return True

    async def _unmount(self, mount_dir: Path) -> bool:
        """Unmount a SquashFS artifact directory, releasing its loop device.

        Cancellation kills and reaps the umount subprocess before propagating,
        so the caller's per-key lock covers the complete unmount lifecycle. If
        umount never took effect, the mounted entry stays consistent and can be
        reused; if it already took effect, the missing extraction directory
        makes the entry a plain cache miss on the next admission.
        """
        umount = shutil.which("umount")
        if umount is None:
            return False

        proc = await asyncio.create_subprocess_exec(
            umount,
            str(mount_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode == 0 or not mount_dir.is_mount():
            return True

        logger.warning(
            "umount command failed",
            mount_dir=str(mount_dir),
            output=(stderr or stdout).decode(errors="replace").strip(),
        )
        return False

    def _scan_cache_entries(self) -> dict[str, RegistryArtifactCacheEntry]:
        """Measure every registry artifact entry currently on disk."""
        return {
            cache_key: self._measure_entry(cache_key)
            for cache_key in self._discover_cache_keys()
        }

    def _discover_cache_keys(self) -> set[str]:
        """Return the cache keys with at least one path in the cache directory."""
        try:
            names = os.listdir(self.cache_dir)
        except OSError:
            return set()

        cache_keys: set[str] = set()
        for name in names:
            if (cache_key := _cache_key_from_entry_name(name)) is not None:
                cache_keys.add(cache_key)
        return cache_keys

    def _measure_entry(self, cache_key: str) -> RegistryArtifactCacheEntry:
        """Measure the on-disk footprint and recency of one cache entry.

        The mount directory is excluded because a mounted view only costs the
        image file that backs it. The image is measured with a single ``stat``
        so a concurrent eviction deleting it cannot fail the scan.
        """
        paths = self._paths_for(cache_key)
        size_bytes = 0
        image_mtime = 0.0
        created_at = 0.0

        try:
            image_stat = paths.squashfs_image_path.stat()
        except OSError:
            pass
        else:
            size_bytes += image_stat.st_size
            image_mtime = image_stat.st_mtime

        for directory in (paths.squashfs_extract_dir, paths.tarball_target_dir):
            directory_bytes, directory_created_at = _directory_footprint(directory)
            size_bytes += directory_bytes
            created_at = max(created_at, directory_created_at)

        # Image mtimes are refreshed on lease; directory ctimes are only a
        # fallback for entries that have no locally downloaded image.
        last_used = image_mtime or created_at

        return RegistryArtifactCacheEntry(
            cache_key=cache_key,
            size_bytes=size_bytes,
            last_used=last_used,
        )

    def _sweep_startup_state(self) -> None:
        """Reclaim orphaned cache state left behind by a previous process.

        Mounts never survive a container restart, so any non-mountpoint mount
        directory is stale. Scratch paths from interrupted materializations are
        removed, and the cache is trimmed to budget using image mtimes as LRU
        order. Scratch and stale empty mount directories are never worker import
        paths, so they are removed unconditionally. Tarball-bearing entries are
        protected for the pool backend because cache construction may happen
        after warm workers have already inherited those paths. A missing or
        empty cache directory is a no-op.

        The worker warms this sweep before activities can run; lazy first-use
        sweeping remains a safe fallback.
        """
        if not self.cache_dir.is_dir():
            self._budget_dirty = False
            return

        try:
            self._remove_orphaned_temp_paths()
            self._remove_stale_mount_dirs()
            self._trim_startup_cache()
        except OSError as e:
            logger.warning(
                "Failed to sweep registry artifact cache",
                cache_dir=str(self.cache_dir),
                error=str(e),
            )

    def _remove_orphaned_temp_paths(self) -> None:
        """Delete every materialization scratch path during startup.

        The sweep runs before the first lease or materialization in this process.
        Every matching path is therefore interrupted scratch from an earlier
        process and is safe to remove even when the operating system reused that
        process's PID.
        """
        for name in os.listdir(self.cache_dir):
            if TEMP_ARTIFACT_PATTERN.match(name) is None:
                continue
            path = self.cache_dir / name
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            logger.info("Removed orphaned registry artifact scratch path", path=name)

    def _remove_stale_mount_dirs(self) -> None:
        """Remove empty mount directories left over from a previous process."""
        for cache_key in self._discover_cache_keys():
            mount_dir = self._paths_for(cache_key).squashfs_mount_dir
            if not mount_dir.is_dir() or mount_dir.is_mount():
                continue
            try:
                mount_dir.rmdir()
            except OSError:
                continue
            logger.debug(
                "Removed stale registry artifact mount directory",
                cache_key=cache_key,
            )

    def _trim_startup_cache(self) -> None:
        """Trim the cache to budget before any artifact is leased.

        Tarball-bearing entries are ineligible when the resolved backend is the
        pool because existing workers may already import from those paths.
        Clears the budget-dirty flag when the cache ends up within budget, so a
        healthy cache never rescans until a new entry is materialized.
        """
        max_entries = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES
        max_bytes = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES
        if max_entries <= 0 and max_bytes <= 0:
            self._budget_dirty = False
            return

        entries = self._scan_cache_entries()
        total_bytes = sum(entry.size_bytes for entry in entries.values())
        backend_type = resolve_backend_type()
        # Mounted entries belong to a live process sharing this cache directory.
        candidates = sorted(
            (
                entry
                for entry in entries.values()
                if not self._paths_for(entry.cache_key).squashfs_mount_dir.is_mount()
                and not self._is_pool_worker_visible(entry.cache_key, backend_type)
            ),
            key=lambda entry: entry.last_used,
        )

        def within_budget() -> bool:
            return (max_entries <= 0 or len(entries) <= max_entries) and (
                max_bytes <= 0 or total_bytes <= max_bytes
            )

        for entry in candidates:
            if within_budget():
                break
            _delete_entry_paths(self._paths_for(entry.cache_key))
            del entries[entry.cache_key]
            total_bytes -= entry.size_bytes
            logger.info(
                "Evicted stale registry artifact during startup sweep",
                cache_key=entry.cache_key,
                size_bytes=entry.size_bytes,
            )

        self._budget_dirty = not within_budget()
