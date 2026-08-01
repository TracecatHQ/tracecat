"""Registry artifact resolution and local materialization for executors."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import sysconfig
import tarfile
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import httpx
import tracecat_registry

from tracecat import config
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

CACHE_ENTRIES_DIR_NAME = "entries"
"""Directory containing one atomic subdirectory per cache key."""

CACHE_STAGING_DIR_NAME = "staging"
"""Directory containing in-progress materialization scratch."""

CACHE_TRASH_DIR_NAME = "trash"
"""Directory containing atomically retired entries pending physical deletion."""


class SquashfsMountCommandError(RuntimeError):
    """The ``mount`` command itself failed for a SquashFS registry artifact.

    Only this error drives SquashFS mount policy. Download, mkdir, and other
    preparation failures must not be mistaken for a missing mount capability or
    for loop-device exhaustion.
    """


class RegistryArtifactCacheLoopError(RuntimeError):
    """A registry artifact cache was used outside its owning event loop."""


@dataclass(frozen=True, slots=True)
class RegistryArtifactPaths:
    """Executor-local cache paths for one registry artifact key."""

    entry_dir: Path
    squashfs_image_path: Path
    squashfs_mount_dir: Path
    squashfs_extract_dir: Path
    tarball_target_dir: Path


@dataclass(slots=True)
class RegistryArtifactRuntimeState:
    """Process-local synchronization and lease state for one cache key."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refcount: int = 0
    last_used: float = 0.0


@dataclass(frozen=True, slots=True)
class RegistryArtifactEviction:
    """Outcome of atomically retiring and physically deleting one entry."""

    retired: bool
    reclaimed: bool


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
    staging_dir: Path
    paths: RegistryArtifactPaths

    def can_mount_squashfs(self) -> bool:
        return config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED and (
            shutil.which("mount") is not None
        )


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
        ctx.staging_dir.mkdir(parents=True, exist_ok=True)
        return ctx.staging_dir / f"{self.cache_key}.{os.getpid()}.{unique_id}{suffix}"


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
            try:
                return [await self.mount(ctx, image_path)]
            except SquashfsMountCommandError as e:
                logger.warning(
                    "Failed to mount SquashFS registry artifact, trying extraction",
                    cache_key=ctx.cache_key,
                    artifact_uri=self.uri,
                    artifact_format=self.format.value,
                    error=str(e),
                )

        return [await self.extract(ctx, image_path)]

    async def download(
        self,
        ctx: RegistryArtifactMaterializationContext,
        image_path: Path,
    ) -> float:
        """Ensure the SquashFS image exists locally and return download time."""
        if image_path.exists():
            return 0.0

        image_path.parent.mkdir(parents=True, exist_ok=True)
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
            return target_dir

        ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
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

        ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)

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
        """Extract a SquashFS image to target_dir using unsquashfs.

        Cancellation kills and reaps the extractor before the caller removes
        its scratch directory. Otherwise a live extractor could recreate
        scratch after cleanup, outside startup-sweep discovery.
        """
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
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise

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
            ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)

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
        """Extract a supported registry tarball to target directory.

        A cancelled caller rejoins the non-interruptible extraction thread
        before propagating cancellation so scratch cleanup cannot race a live
        writer and leave undiscoverable ephemeral storage behind.
        """

        def _do_extract() -> None:
            if tarball_path.name.endswith(".tar.gz"):
                with tarfile.open(tarball_path, "r:gz") as tar:
                    tar.extractall(path=target_dir, filter="data")
                return

            raise ValueError(f"Unsupported tarball format: {tarball_path}")

        extraction = asyncio.ensure_future(asyncio.to_thread(_do_extract))
        try:
            await asyncio.shield(extraction)
        except asyncio.CancelledError:
            # A thread cannot be killed. Rejoin it before materialize removes
            # scratch. Each cancellation can interrupt shield without stopping
            # the thread, so keep waiting until extraction reaches a terminal
            # state before propagating the original cancellation.
            while not extraction.done():
                try:
                    await asyncio.shield(extraction)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not extraction.cancelled():
                with contextlib.suppress(Exception):
                    extraction.result()
            raise

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


def _directory_footprint(directory: Path) -> int:
    """Return the total file size of a cache directory.

    Args:
        directory: Cache directory to measure.

    Returns:
        Total byte size of contained files, or zero when the directory is
        missing.
    """

    def raise_walk_error(error: OSError) -> None:
        raise error

    total_bytes = 0
    try:
        walker = os.walk(directory, onerror=raise_walk_error)
        for root, _dirs, files in walker:
            for file_name in files:
                try:
                    total_bytes += os.lstat(os.path.join(root, file_name)).st_size
                except FileNotFoundError:
                    continue
    except FileNotFoundError:
        return 0
    return total_bytes


def _delete_cache_path(path: Path) -> bool:
    """Best-effort delete one cache path while reporting filesystem failures."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(
            "Failed to delete registry artifact cache path",
            path=str(path),
            error=str(e),
        )
        return False
    return True


async def _delete_cache_path_off_loop(path: Path) -> bool:
    """Delete one path without abandoning its worker thread on cancellation."""
    deletion = asyncio.ensure_future(asyncio.to_thread(_delete_cache_path, path))
    try:
        return await asyncio.shield(deletion)
    except asyncio.CancelledError:
        # A worker thread cannot be killed. Rejoin it so no live deletion can
        # race a later trash-directory scan. Repeated cancellation can interrupt
        # shield without stopping the thread, so keep waiting for termination.
        while not deletion.done():
            try:
                await asyncio.shield(deletion)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not deletion.cancelled():
            with contextlib.suppress(Exception):
                deletion.result()
        raise


def _unique_work_path(root: Path, cache_key: str) -> Path:
    """Return a unique path beneath a cache work directory."""
    root.mkdir(parents=True, exist_ok=True)
    unique_id = time.time_ns()
    while True:
        path = root / f"{cache_key}.{os.getpid()}.{unique_id}"
        if not path.exists():
            return path
        unique_id += 1


def _move_entry_to_trash(entry_dir: Path, trash_dir: Path, cache_key: str) -> Path:
    """Atomically retire one cache entry and return its trash path."""
    trash_path = _unique_work_path(trash_dir, cache_key)
    entry_dir.rename(trash_path)
    return trash_path


class RegistryArtifactCache:
    """Materializes registry artifacts into executor-local Python paths."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.entries_dir = cache_dir / CACHE_ENTRIES_DIR_NAME
        self.staging_dir = cache_dir / CACHE_STAGING_DIR_NAME
        self.trash_dir = cache_dir / CACHE_TRASH_DIR_NAME
        # Runtime states live for the process lifetime so every operation for a
        # key always serializes on the same lock.
        self._runtime: dict[str, RegistryArtifactRuntimeState] = {}
        # The cache contains asyncio locks, tasks, and multi-step lease state.
        # Bind the public API to one loop/thread so a future synchronous
        # Temporal activity fails immediately instead of corrupting that state
        # through a thread-local event loop.
        self._owner_binding_lock = threading.Lock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_thread_id: int | None = None
        self._budget_lock = asyncio.Lock()
        # Guard the off-loop startup sweep independently from cache operations.
        self._swept: bool = False
        self._sweep_task: asyncio.Task[None] | None = None
        self._sweep_lock = asyncio.Lock()
        # Startup is the only time the whole staging directory is swept. Exact
        # paths that could not be removed are safe to retry later.
        self._failed_startup_cleanup: set[Path] = set()
        # Whether the on-disk cache may exceed its budget. Set when a new entry
        # is materialized and cleared once enforcement measures a cache that
        # fits, so steady-state cache hits never pay for a disk scan.
        self._budget_dirty = True

    async def ensure_swept(self) -> None:
        """Run the startup sweep exactly once successfully, off the event loop.

        Idempotent and cancellation-safe under concurrency: every caller joins
        one stored sweep task, and cancelling a waiter never abandons or
        restarts its live sweep. The lock is deliberately held while awaiting
        that shared task so queued callers observe its result before proceeding.
        Failures clear the task so the next caller retries. The sweep runs
        before the first lease or materialization, so it never observes
        in-flight cache entries.
        """
        self._assert_owner_loop()
        if self._swept:
            return
        async with self._sweep_lock:
            if self._swept:
                return
            sweep_task = self._sweep_task
            if sweep_task is None or (
                sweep_task.done()
                and (sweep_task.cancelled() or sweep_task.exception() is not None)
            ):
                sweep_task = asyncio.ensure_future(
                    asyncio.to_thread(self._sweep_startup_state)
                )
                self._sweep_task = sweep_task
            try:
                await asyncio.shield(sweep_task)
            except asyncio.CancelledError:
                if sweep_task.cancelled() and self._sweep_task is sweep_task:
                    self._sweep_task = None
                raise
            except Exception:
                if self._sweep_task is sweep_task:
                    self._sweep_task = None
                raise
            self._swept = True

    def _assert_owner_loop(self) -> None:
        """Bind to the current loop or reject use from another loop/thread."""
        current_loop = asyncio.get_running_loop()
        current_thread_id = threading.get_ident()
        with self._owner_binding_lock:
            if self._owner_loop is None:
                self._owner_loop = current_loop
                self._owner_thread_id = current_thread_id
                return
            if (
                self._owner_loop is current_loop
                and self._owner_thread_id == current_thread_id
            ):
                return

            raise RegistryArtifactCacheLoopError(
                "RegistryArtifactCache is bound to one event loop and thread "
                f"(owner_loop={id(self._owner_loop)}, "
                f"owner_thread={self._owner_thread_id}, "
                f"current_loop={id(current_loop)}, "
                f"current_thread={current_thread_id})"
            )

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
                cache_key, artifact_paths = await self._lease_artifact(artifact_uri)
                if cache_key is not None:
                    leased_keys.append(cache_key)
                registry_paths.extend(artifact_paths)
            logger.info(
                "Using registry artifact environments",
                count=len(registry_paths),
            )
            yield registry_paths
        finally:
            idle_keys = [
                cache_key for cache_key in leased_keys if self._release_lease(cache_key)
            ]
            cleanup_task = asyncio.ensure_future(self._finish_lease_cleanup(idle_keys))
            pending_cancellation: asyncio.CancelledError | None = None
            while True:
                try:
                    await asyncio.shield(cleanup_task)
                    break
                except asyncio.CancelledError as e:
                    if cleanup_task.cancelled():
                        raise
                    pending_cancellation = e

            if pending_cancellation is not None:
                raise pending_cancellation

    async def _finish_lease_cleanup(self, idle_keys: list[str]) -> None:
        """Unmount every newly idle entry and converge the cache budget."""
        for cache_key in idle_keys:
            await self._unmount_idle_entry(cache_key)
        await self._converge_cache_budget()

    async def _lease_artifact(self, artifact_uri: str) -> tuple[str | None, list[Path]]:
        """Pin and materialize one artifact, returning its releasable cache key."""
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        ctx = self._context_for(cache_key)
        if not _is_cache_entry_uri(artifact_uri):
            return None, await self._materialize_candidates(ctx, artifact_uri)

        lock = self._runtime_for(cache_key).lock
        lease_acquired = False
        try:
            async with lock:
                self._acquire_lease(cache_key)
                lease_acquired = True
                candidates = await self._artifact_candidates(ctx, artifact_uri)
                if cached_paths := self._first_cached_path(candidates, ctx):
                    return cache_key, cached_paths

            async with lock:
                paths = await self._materialize_candidates(ctx, artifact_uri)
                self._touch_entry(cache_key)

            # Enforce only after publication, and outside the per-key lock so
            # eviction never nests key locks. A failed cold admission must not
            # discard a usable warm entry before a replacement exists.
            try:
                await self._enforce_cache_budget(protected_key=cache_key)
            except OSError as e:
                # Cache maintenance must never block artifact admission.
                logger.warning(
                    "Failed to enforce registry artifact cache budget",
                    cache_dir=str(self.cache_dir),
                    error=str(e),
                )
            return cache_key, paths
        except BaseException:
            if lease_acquired and self._release_lease(cache_key):
                await self._unmount_idle_entry(cache_key)
            raise

    async def _materialize_candidates(
        self,
        ctx: RegistryArtifactMaterializationContext,
        artifact_uri: str,
    ) -> list[Path]:
        """Materialize the first viable artifact candidate.

        Callers hold the cache key's lock for evictable entries.
        """
        candidates = await self._artifact_candidates(ctx, artifact_uri)
        if cached_paths := self._first_cached_path(candidates, ctx):
            return cached_paths

        cache_key = ctx.cache_key
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
                materialized = False
                try:
                    registry_paths = await artifact.materialize(ctx)
                    materialized = True
                finally:
                    if _is_cache_entry_uri(artifact.uri):
                        # Any attempt may deposit canonical bytes, even when it
                        # fails or is cancelled.
                        self._budget_dirty = True
                        if not materialized:
                            self._remove_unpublished_entry(ctx)
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

    def _runtime_for(self, cache_key: str) -> RegistryArtifactRuntimeState:
        """Return the process-local state for one cache key."""
        if runtime := self._runtime.get(cache_key):
            return runtime
        runtime = RegistryArtifactRuntimeState()
        self._runtime[cache_key] = runtime
        return runtime

    def _context_for(self, cache_key: str) -> RegistryArtifactMaterializationContext:
        """Return a materialization context for a registry artifact key."""
        return RegistryArtifactMaterializationContext(
            cache_key=cache_key,
            staging_dir=self.staging_dir,
            paths=self._paths_for(cache_key),
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
        runtime = self._runtime_for(cache_key)
        runtime.refcount += 1
        runtime.last_used = time.time()
        self._touch_entry(cache_key)

    def _release_lease(self, cache_key: str) -> bool:
        """Release one pin and return whether the entry became idle."""
        runtime = self._runtime.get(cache_key)
        if runtime is None or runtime.refcount == 0:
            return False
        runtime.refcount -= 1
        runtime.last_used = time.time()
        return runtime.refcount == 0

    async def _unmount_idle_entry(self, cache_key: str) -> None:
        """Best-effort unmount an entry after its final lease is released."""
        try:
            await self._unmount_entry(cache_key)
        except OSError as e:
            logger.warning(
                "Failed to release idle registry artifact mount",
                cache_key=cache_key,
                error=str(e),
            )

    def _refcount(self, cache_key: str) -> int:
        """Return the number of live leases on a cache entry."""
        runtime = self._runtime.get(cache_key)
        return 0 if runtime is None else runtime.refcount

    def _touch_entry(self, cache_key: str) -> None:
        """Best-effort refresh of the entry-root mtime for restart-safe LRU."""
        entry_dir = self._paths_for(cache_key).entry_dir
        try:
            os.utime(entry_dir)
        except OSError:
            logger.debug(
                "Could not refresh registry artifact entry mtime",
                cache_key=cache_key,
            )

    def _paths_for(self, cache_key: str) -> RegistryArtifactPaths:
        """Return local cache paths for a registry artifact key."""
        entry_dir = self.entries_dir / cache_key
        return RegistryArtifactPaths(
            entry_dir=entry_dir,
            squashfs_image_path=entry_dir / "image.squashfs",
            squashfs_mount_dir=entry_dir / "mount",
            squashfs_extract_dir=entry_dir / "extracted",
            tarball_target_dir=entry_dir / "tarball",
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

    def _remove_unpublished_entry(
        self,
        ctx: RegistryArtifactMaterializationContext,
    ) -> None:
        """Remove an entry shell when an attempt published no reusable artifact.

        Callers hold the cache key lock. ``rmdir`` only removes empty
        directories, so canonical artifacts and unknown contents are preserved.
        """
        paths = ctx.paths
        try:
            if paths.squashfs_mount_dir.is_mount():
                return
        except OSError:
            return

        for directory in (paths.squashfs_mount_dir, paths.entry_dir):
            with contextlib.suppress(OSError):
                directory.rmdir()

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

        Successful materialization enforces the budget after publication while
        protecting the new entry. The cache can still sit over budget while
        entries are leased. This runs on release, when every newly idle entry is
        evictable. The scan is skipped entirely unless a materialization attempt
        has occurred since the last successful enforcement.

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
            protected_key: Newly materialized cache key. It is counted against
                the budget when present but never evicted. None when enforcing
                against idle entries after leases are released.

        Returns:
            Whether the cache is within budget once eviction has finished.
        """
        async with self._budget_lock:
            trash_clean, startup_clean = await asyncio.gather(
                asyncio.to_thread(self._clear_work_dir, self.trash_dir),
                asyncio.to_thread(self._retry_failed_startup_cleanup),
            )
            cleanup_complete = trash_clean and startup_clean
            if not cleanup_complete:
                return False

            max_entries = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES
            max_bytes = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES
            if max_entries <= 0 and max_bytes <= 0:
                return True

            entries = await asyncio.to_thread(self._scan_cache_entries)
            total_bytes = sum(entry.size_bytes for entry in entries.values())
            protected = set() if protected_key is None else {protected_key}
            skipped: set[str] = set()

            while (max_entries > 0 and len(entries) > max_entries) or (
                max_bytes > 0 and total_bytes > max_bytes
            ):
                candidate = self._least_recently_used(
                    entries.values(),
                    excluded=skipped | protected,
                )
                if candidate is None:
                    logger.warning(
                        "Registry artifact cache is over budget but every entry is in use",
                        cache_dir=str(self.cache_dir),
                        entries=len(entries),
                        max_entries=max_entries,
                        total_bytes=total_bytes,
                        max_bytes=max_bytes,
                    )
                    return False

                eviction = await self._evict_entry(candidate.cache_key)
                if eviction.retired:
                    del entries[candidate.cache_key]
                    if not eviction.reclaimed:
                        return False
                    total_bytes -= candidate.size_bytes
                else:
                    skipped.add(candidate.cache_key)

            return True

    def _least_recently_used(
        self,
        entries: Iterable[RegistryArtifactCacheEntry],
        *,
        excluded: set[str],
    ) -> RegistryArtifactCacheEntry | None:
        """Return the least recently used idle entry eligible for eviction."""
        eligible = [
            entry
            for entry in entries
            if entry.cache_key not in excluded and self._refcount(entry.cache_key) == 0
        ]
        if not eligible:
            return None
        return min(eligible, key=self._recency)

    def _recency(self, entry: RegistryArtifactCacheEntry) -> float:
        """Return the most recent known use time for a cache entry."""
        runtime = self._runtime.get(entry.cache_key)
        if runtime is None:
            return entry.last_used
        return max(entry.last_used, runtime.last_used)

    async def _unmount_entry(self, cache_key: str) -> bool:
        """Unmount one idle cache entry while retaining its reusable image.

        Loop-device reclamation is independent from disk-budget eviction. The
        per-key lock and lease recheck prevent an entry from being unmounted
        while an action is importing from it. The image and empty mount
        directory remain cached so a later admission can remount without
        downloading the artifact again.

        Args:
            cache_key: Cache key whose mounted artifact should be released.

        Returns:
            Whether a mounted entry was unmounted.
        """
        lock = self._runtime_for(cache_key).lock
        if lock.locked():
            logger.debug(
                "Skipping unmount of busy registry artifact",
                cache_key=cache_key,
            )
            return False

        async with lock:
            if self._refcount(cache_key) > 0:
                return False

            mount_dir = self._paths_for(cache_key).squashfs_mount_dir
            if not mount_dir.is_mount():
                return False
            if not await self._unmount(mount_dir):
                logger.warning(
                    "Failed to unmount registry artifact for loop-device reclamation",
                    cache_key=cache_key,
                    mount_dir=str(mount_dir),
                )
                return False

            logger.info(
                "Unmounted idle registry artifact",
                cache_key=cache_key,
                mount_dir=str(mount_dir),
            )
            return True

    async def _evict_entry(self, cache_key: str) -> RegistryArtifactEviction:
        """Remove one cache entry from disk, unmounting it first.

        The entry is skipped rather than forced when it is leased, busy, or
        cannot be unmounted: deleting the image file behind a live mount would
        leave an open-file zombie holding the loop device.

        After unmounting, the entry root is atomically renamed into ``trash``
        under the per-key lock. The lock is then released before physical
        deletion runs in a worker thread.

        Args:
            cache_key: Cache key to evict.

        Returns:
            Whether the entry was retired and its bytes were reclaimed.
        """
        lock = self._runtime_for(cache_key).lock
        if lock.locked():
            logger.debug(
                "Skipping eviction of busy registry artifact",
                cache_key=cache_key,
            )
            return RegistryArtifactEviction(retired=False, reclaimed=False)

        async with lock:
            if self._refcount(cache_key) > 0:
                return RegistryArtifactEviction(retired=False, reclaimed=False)

            paths = self._paths_for(cache_key)
            if not paths.entry_dir.exists():
                return RegistryArtifactEviction(retired=True, reclaimed=True)
            if paths.squashfs_mount_dir.is_mount() and not await self._unmount(
                paths.squashfs_mount_dir
            ):
                logger.warning(
                    "Failed to unmount registry artifact, skipping eviction",
                    cache_key=cache_key,
                    mount_dir=str(paths.squashfs_mount_dir),
                )
                return RegistryArtifactEviction(retired=False, reclaimed=False)

            try:
                trash_path = _move_entry_to_trash(
                    paths.entry_dir,
                    self.trash_dir,
                    cache_key,
                )
            except OSError as e:
                self._budget_dirty = True
                logger.warning(
                    "Failed to retire registry artifact cache entry",
                    cache_key=cache_key,
                    entry_dir=str(paths.entry_dir),
                    error=str(e),
                )
                return RegistryArtifactEviction(retired=False, reclaimed=False)

        try:
            deleted = await _delete_cache_path_off_loop(trash_path)
        except BaseException:
            self._budget_dirty = True
            raise
        if not deleted:
            self._budget_dirty = True
            logger.warning(
                "Registry artifact eviction remains pending physical deletion",
                cache_key=cache_key,
                trash_path=str(trash_path),
            )
            return RegistryArtifactEviction(retired=True, reclaimed=False)

        logger.info("Evicted registry artifact from cache", cache_key=cache_key)
        return RegistryArtifactEviction(retired=True, reclaimed=True)

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
        """Return cache keys represented by atomic entry directories."""
        try:
            entries = list(os.scandir(self.entries_dir))
        except FileNotFoundError:
            return set()

        return {
            entry.name
            for entry in entries
            if entry.name and entry.is_dir(follow_symlinks=False)
        }

    def _measure_entry(self, cache_key: str) -> RegistryArtifactCacheEntry:
        """Measure the on-disk footprint and recency of one cache entry.

        The mount directory is excluded because a mounted view only costs the
        image file that backs it. The image is measured with a single ``stat``
        so a concurrent eviction deleting it cannot fail the scan.
        """
        paths = self._paths_for(cache_key)
        size_bytes = 0

        try:
            image_stat = paths.squashfs_image_path.stat()
        except FileNotFoundError:
            pass
        else:
            size_bytes += image_stat.st_size

        for directory in (paths.squashfs_extract_dir, paths.tarball_target_dir):
            size_bytes += _directory_footprint(directory)

        try:
            last_used = paths.entry_dir.stat().st_mtime
        except FileNotFoundError:
            last_used = 0.0

        return RegistryArtifactCacheEntry(
            cache_key=cache_key,
            size_bytes=size_bytes,
            last_used=last_used,
        )

    def _sweep_startup_state(self) -> None:
        """Reclaim orphaned cache state left behind by a previous process.

        Scratch and trash paths from interrupted work are removed, and active
        entries are trimmed to budget using entry-root mtimes as LRU order.

        The worker warms this sweep before activities can run; lazy first-use
        sweeping remains a safe fallback.
        """
        if not self.cache_dir.is_dir():
            self._budget_dirty = False
            return

        try:
            staging_clean = self._clear_work_dir(
                self.staging_dir,
                remember_failures=True,
            )
            trash_clean = self._clear_work_dir(self.trash_dir)
            cleanup_complete = staging_clean and trash_clean
            within_budget = cleanup_complete and self._trim_startup_cache()
            self._budget_dirty = not (cleanup_complete and within_budget)
        except OSError as e:
            logger.warning(
                "Failed to sweep registry artifact cache",
                cache_dir=str(self.cache_dir),
                error=str(e),
            )
            raise

    def _clear_work_dir(
        self,
        work_dir: Path,
        *,
        remember_failures: bool = False,
    ) -> bool:
        """Best-effort remove every child of a staging or trash directory."""
        try:
            paths = list(work_dir.iterdir())
        except FileNotFoundError:
            return True
        except OSError as e:
            logger.warning(
                "Failed to inspect registry artifact work directory",
                path=str(work_dir),
                error=str(e),
            )
            raise

        deleted = True
        for path in paths:
            if _delete_cache_path(path):
                if remember_failures:
                    self._failed_startup_cleanup.discard(path)
                logger.info(
                    "Removed registry artifact work path",
                    path=str(path),
                )
            else:
                deleted = False
                if remember_failures:
                    self._failed_startup_cleanup.add(path)
        return deleted

    def _retry_failed_startup_cleanup(self) -> bool:
        """Retry exact startup paths without sweeping live staging work."""
        for path in tuple(self._failed_startup_cleanup):
            if _delete_cache_path(path):
                self._failed_startup_cleanup.discard(path)
        return not self._failed_startup_cleanup

    def _trim_startup_cache(self) -> bool:
        """Trim the cache to budget before any artifact is leased.

        Returns whether active entries and pending physical deletion fit within
        the configured budget.
        """
        max_entries = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES
        max_bytes = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES
        if max_entries <= 0 and max_bytes <= 0:
            return True

        entries = self._scan_cache_entries()
        total_bytes = sum(entry.size_bytes for entry in entries.values())
        # Mounted entries belong to a live process sharing this cache directory.
        candidates = sorted(
            (
                entry
                for entry in entries.values()
                if not self._paths_for(entry.cache_key).squashfs_mount_dir.is_mount()
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
            paths = self._paths_for(entry.cache_key)
            try:
                trash_path = _move_entry_to_trash(
                    paths.entry_dir,
                    self.trash_dir,
                    entry.cache_key,
                )
            except OSError as e:
                logger.warning(
                    "Failed to retire stale registry artifact during startup sweep",
                    cache_key=entry.cache_key,
                    entry_dir=str(paths.entry_dir),
                    error=str(e),
                )
                return False

            del entries[entry.cache_key]
            if _delete_cache_path(trash_path):
                total_bytes -= entry.size_bytes
            else:
                return False
            logger.info(
                "Evicted stale registry artifact during startup sweep",
                cache_key=entry.cache_key,
                size_bytes=entry.size_bytes,
            )

        return within_budget()
