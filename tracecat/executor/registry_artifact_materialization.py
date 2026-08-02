"""Registry artifact formats and local materialization primitives."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import sysconfig
import tarfile
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

import httpx
import tracecat_registry

from tracecat import config
from tracecat.executor import registry_artifact_mounts
from tracecat.logger import logger
from tracecat.registry.artifact_keys import parse_s3_uri
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.sandbox.utils import communicate_process_group
from tracecat.storage import blob

__all__ = [
    "_is_cache_entry_uri",
    "_squashfs_sidecar_uri",
    "_tarball_uri_for_squashfs",
]


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


@dataclass(frozen=True, slots=True)
class RegistryArtifactPaths:
    """Executor-local cache paths for one registry artifact key."""

    entry_dir: Path
    squashfs_image_path: Path
    squashfs_mount_dir: Path
    squashfs_extract_dir: Path
    tarball_target_dir: Path


class SquashfsMountCommandError(RuntimeError):
    """The ``mount`` command itself failed for a SquashFS registry artifact.

    Only this error drives SquashFS mount policy. Download, mkdir, and other
    preparation failures must not be mistaken for a missing mount capability or
    for loop-device exhaustion.
    """


@dataclass(frozen=True, slots=True)
class RegistryArtifactAdmission:
    """Byte-bound admission hook shared by one cold materialization."""

    max_bytes: int
    allocation_unit: int
    ensure_capacity: Callable[[int], Awaitable[None]]


@dataclass(slots=True)
class RegistryArtifactMaterializationContext:
    """Shared runtime state for artifact materialization."""

    cache_key: str
    staging_dir: Path
    paths: RegistryArtifactPaths
    defer_cleanup: Callable[[Path], None]
    admission: RegistryArtifactAdmission | None = None

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

    def discard_failed_materialization(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> None:
        """Discard canonical bytes that cannot serve a fallback candidate."""
        del ctx

    def _temp_path(
        self,
        ctx: RegistryArtifactMaterializationContext,
        suffix: str,
    ) -> Path:
        unique_id = id(asyncio.current_task())
        ctx.staging_dir.mkdir(parents=True, exist_ok=True)
        return ctx.staging_dir / f"{self.cache_key}.{os.getpid()}.{unique_id}{suffix}"


async def _rejoin_future_on_cancel[T](future: asyncio.Future[T]) -> T:
    """Shield a future and rejoin it through repeated caller cancellation."""
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not future.cancelled():
            with contextlib.suppress(Exception):
                future.result()
        raise


async def _run_blocking_rejoin_on_cancel[T](operation: Callable[[], T]) -> T:
    """Run blocking work without abandoning its thread on cancellation."""
    worker = asyncio.ensure_future(asyncio.to_thread(operation))
    return await _rejoin_future_on_cancel(worker)


async def _remove_tree_rejoin_on_cancel(
    path: Path,
    *,
    defer_cleanup: Callable[[Path], None],
) -> None:
    """Remove a tree off-loop and retain failed paths for a later retry."""
    if not path.exists():
        return
    try:
        await _run_blocking_rejoin_on_cancel(lambda: shutil.rmtree(path))
    except FileNotFoundError:
        return
    except asyncio.CancelledError:
        if path.exists():
            defer_cleanup(path)
            logger.warning(
                "Deferred cancelled registry artifact staging cleanup",
                path=str(path),
            )
        raise
    except OSError as e:
        defer_cleanup(path)
        logger.warning(
            "Deferred failed registry artifact staging cleanup",
            path=str(path),
            error=str(e),
        )


def _remove_file_or_defer(
    path: Path,
    *,
    defer_cleanup: Callable[[Path], None],
) -> None:
    """Remove one artifact file without masking the materialization outcome."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        defer_cleanup(path)
        logger.warning(
            "Deferred failed registry artifact file cleanup",
            path=str(path),
            error=str(e),
        )


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
        if registry_artifact_mounts.is_mount(ctx.paths.squashfs_mount_dir):
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

    def discard_failed_materialization(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> None:
        """Remove an unusable image before admitting a tarball fallback."""
        try:
            if self.cached_path(ctx) is not None:
                return
        except OSError as e:
            logger.warning(
                "Cannot determine whether failed SquashFS candidate is reusable",
                cache_key=ctx.cache_key,
                artifact_uri=self.uri,
                error=str(e),
            )
            return

        _remove_file_or_defer(
            ctx.paths.squashfs_image_path,
            defer_cleanup=ctx.defer_cleanup,
        )

        for directory in (
            ctx.paths.squashfs_extract_dir,
            ctx.paths.squashfs_mount_dir,
            ctx.paths.entry_dir,
        ):
            with contextlib.suppress(OSError):
                directory.rmdir()

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
            await _download_s3_artifact(
                self.uri,
                temp_image,
                admission=ctx.admission,
                defer_cleanup=ctx.defer_cleanup,
            )
            try:
                temp_image.rename(image_path)
            except OSError:
                if not image_path.exists():
                    raise
            return (time.monotonic() - download_start) * 1000
        finally:
            _remove_file_or_defer(
                temp_image,
                defer_cleanup=ctx.defer_cleanup,
            )

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
        if registry_artifact_mounts.is_mount(target_dir):
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
            if ctx.admission is not None:
                extracted_size = await self._squashfs_extracted_size(
                    image_path,
                    allocation_unit=ctx.admission.allocation_unit,
                )
                await ctx.admission.ensure_capacity(extracted_size)
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
            await _remove_tree_rejoin_on_cancel(
                temp_dir,
                defer_cleanup=ctx.defer_cleanup,
            )

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
        if registry_artifact_mounts.is_mount(target_dir):
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
            start_new_session=True,
        )
        stdout, stderr = await communicate_process_group(proc)

        if proc.returncode == 0 or registry_artifact_mounts.is_mount(target_dir):
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
            start_new_session=True,
        )
        stdout, stderr = await communicate_process_group(proc)

        if proc.returncode == 0:
            return

        output = (stderr or stdout).decode(errors="replace").strip()
        raise RuntimeError(output or "unsquashfs command failed")

    async def _squashfs_extracted_size(
        self,
        image_path: Path,
        *,
        allocation_unit: int = 1,
    ) -> int:
        """Return a conservative allocated size for a SquashFS extraction."""
        unsquashfs = shutil.which("unsquashfs")
        if unsquashfs is None:
            raise RuntimeError("unsquashfs command is not installed")

        proc = await asyncio.create_subprocess_exec(
            unsquashfs,
            "-lln",
            str(image_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = await communicate_process_group(proc)

        if proc.returncode != 0:
            output = (stderr or stdout).decode(errors="replace").strip()
            raise RuntimeError(output or "unsquashfs listing failed")
        return _squashfs_listing_size(stdout, allocation_unit=allocation_unit)


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

            admission = ctx.admission
            if admission is not None:
                extracted_size = await _run_blocking_rejoin_on_cancel(
                    lambda: _tarball_extracted_size(
                        temp_tarball,
                        allocation_unit=admission.allocation_unit,
                    )
                )
                await admission.ensure_capacity(extracted_size)

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
            try:
                await _remove_tree_rejoin_on_cancel(
                    temp_dir,
                    defer_cleanup=ctx.defer_cleanup,
                )
            finally:
                _remove_file_or_defer(
                    temp_tarball,
                    defer_cleanup=ctx.defer_cleanup,
                )

        return [target_dir]

    async def download(
        self,
        ctx: RegistryArtifactMaterializationContext,
        output_path: Path,
    ) -> None:
        await _download_s3_artifact(
            self.uri,
            output_path,
            admission=ctx.admission,
            defer_cleanup=ctx.defer_cleanup,
        )

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

        await _run_blocking_rejoin_on_cancel(_do_extract)

        logger.debug(
            "Tarball extracted",
            target=str(target_dir),
            artifact_format=_artifact_format(str(tarball_path)).value,
        )


async def _download_s3_artifact(
    artifact_uri: str,
    output_path: Path,
    *,
    admission: RegistryArtifactAdmission | None = None,
    defer_cleanup: Callable[[Path], None],
) -> None:
    """Download an S3 registry artifact to a local path."""
    bucket, key = parse_s3_uri(artifact_uri)
    try:
        if admission is None:
            await blob.download_file_to_path(
                key=key,
                bucket=bucket,
                output_path=output_path,
                defer_cleanup=defer_cleanup,
            )
        else:
            await blob.download_file_to_path(
                key=key,
                bucket=bucket,
                output_path=output_path,
                max_bytes=admission.max_bytes,
                ensure_capacity=admission.ensure_capacity,
                defer_cleanup=defer_cleanup,
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


def _allocated_size_bound(size_bytes: int, *, allocation_unit: int) -> int:
    """Round one filesystem object up to its minimum allocated footprint."""
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if allocation_unit <= 0:
        raise ValueError("allocation_unit must be positive")
    return (
        max(1, (size_bytes + allocation_unit - 1) // allocation_unit) * allocation_unit
    )


def _tarball_extracted_size(
    tarball_path: Path,
    *,
    allocation_unit: int = 1,
) -> int:
    """Return a conservative allocated size bound for a tarball extraction."""
    total_bytes = 0
    root_path = PurePosixPath(".")
    has_explicit_root_directory = False
    required_parent_dirs: set[PurePosixPath] = set()
    explicit_dirs: set[PurePosixPath] = set()
    with tarfile.open(tarball_path, "r:gz") as tar:
        for member in tar:
            if member.size < 0:
                raise ValueError(
                    f"Registry tarball member has a negative size: {member.name}"
                )
            total_bytes += _allocated_size_bound(
                member.size,
                allocation_unit=allocation_unit,
            )
            member_path = PurePosixPath(member.name)
            if member.isdir():
                explicit_dirs.add(member_path)
                has_explicit_root_directory |= member_path == root_path
            for parent in member_path.parents:
                if parent == root_path:
                    break
                required_parent_dirs.add(parent)
    # Extraction creates a target root even when the tar manifest omits it.
    if not has_explicit_root_directory:
        total_bytes += _allocated_size_bound(0, allocation_unit=allocation_unit)
    implicit_parent_dirs = required_parent_dirs - explicit_dirs
    total_bytes += len(implicit_parent_dirs) * allocation_unit
    return total_bytes


def _squashfs_listing_size(output: bytes, *, allocation_unit: int = 1) -> int:
    """Bound allocated bytes from ``unsquashfs -lln`` output, failing closed."""
    total_bytes = 0
    for raw_line in output.decode(errors="strict").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(maxsplit=4)
        mode = fields[0]
        if len(mode) != 10 or mode[0] not in "bcdlps-":
            continue
        if len(fields) < 5 or "/" not in fields[1] or not fields[2].isdigit():
            raise ValueError(f"Could not parse SquashFS listing line: {line}")
        total_bytes += _allocated_size_bound(
            int(fields[2]),
            allocation_unit=allocation_unit,
        )
    return total_bytes
