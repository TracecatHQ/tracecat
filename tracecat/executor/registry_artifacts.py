"""Registry artifact resolution and local materialization for executors."""

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
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import TracebackType
from urllib.parse import urlsplit, urlunsplit

import httpx
import tracecat_registry

from tracecat import config
from tracecat.concurrency import (
    rejoin_future_through_cancellation,
    run_blocking_rejoin_on_cancel,
)
from tracecat.executor import registry_artifact_mounts
from tracecat.executor.registry_artifact_storage import (
    RegistryArtifactAdmission,
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheLoopError,
    RegistryArtifactCacheStorage,
    RegistryArtifactEviction,
    RegistryArtifactMaterializationContext,
    allocated_size_bound,
    ensure_cache_entry_directory,
    ensure_real_directory,
    is_reusable_cache_directory,
    is_reusable_cache_file,
    remove_file_or_defer,
    remove_tree_rejoin_on_cancel,
    unique_work_path,
    validate_cache_entry_path,
)
from tracecat.logger import logger
from tracecat.registry.artifact_keys import parse_s3_uri
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.sandbox.utils import communicate_process_group
from tracecat.storage import blob

__all__ = (
    "BUNDLED_BUILTIN_REGISTRY_URI_PREFIX",
    "SQUASHFS_MOUNT_OPTIONS",
    "BuiltinArtifact",
    "RegistryArtifact",
    "RegistryArtifactAdmission",
    "RegistryArtifactCache",
    "RegistryArtifactCacheCapacityError",
    "RegistryArtifactExtractionError",
    "RegistryArtifactCacheLoopError",
    "RegistryArtifactEviction",
    "RegistryArtifactFormat",
    "RegistryArtifactMaterializationContext",
    "RegistryArtifactUriError",
    "SquashfsArtifact",
    "SquashfsMountCommandError",
    "TarballArtifact",
    "bundled_builtin_registry_uri",
    "compute_registry_artifact_cache_key",
)


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


class SquashfsMountCommandError(RuntimeError):
    """The ``mount`` command itself failed for a SquashFS registry artifact.

    Only this error drives SquashFS mount policy. Download, mkdir, and other
    preparation failures must not be mistaken for a missing mount capability or
    for loop-device exhaustion.
    """


class RegistryArtifactUriError(ValueError):
    """A registry artifact URI is malformed, with identifiers suppressed."""


class RegistryArtifactExtractionError(RuntimeError):
    """A registry archive could not be inspected or extracted safely."""

    def __init__(self) -> None:
        super().__init__("Registry artifact extraction failed")


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
        return unique_work_path(ctx.staging_dir, self.cache_key, suffix=suffix)


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
        validate_cache_entry_path(ctx.paths)
        mount_dir = ctx.paths.squashfs_mount_dir
        if os.path.lexists(mount_dir) and not is_reusable_cache_directory(mount_dir):
            remove_file_or_defer(
                mount_dir,
                defer_cleanup=ctx.defer_cleanup,
            )
        if is_reusable_cache_directory(mount_dir) and registry_artifact_mounts.is_mount(
            mount_dir
        ):
            logger.debug(
                "Using cached SquashFS registry mount",
                cache_key=ctx.cache_key,
            )
            return [mount_dir]
        if _is_reusable_extraction_dir(
            ctx.paths.squashfs_extract_dir,
            defer_cleanup=ctx.defer_cleanup,
        ):
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
                    artifact_uri=_artifact_uri_for_logging(self.uri),
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
        validate_cache_entry_path(ctx.paths)
        if await _reuse_or_reclaim_cache_file(
            image_path,
            defer_cleanup=ctx.defer_cleanup,
        ):
            return 0.0

        ensure_cache_entry_directory(ctx.paths)
        temp_image = self._temp_path(ctx, ".squashfs")
        try:
            download_start = time.monotonic()
            await _download_s3_artifact(
                self.uri,
                temp_image,
                admission=ctx.admission,
                defer_cleanup=ctx.defer_cleanup,
                published_path=image_path,
            )
            try:
                temp_image.rename(image_path)
            except OSError:
                if not image_path.exists():
                    raise
            return (time.monotonic() - download_start) * 1000
        finally:
            remove_file_or_defer(
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
        validate_cache_entry_path(ctx.paths)
        target_dir = ctx.paths.squashfs_mount_dir
        if os.path.lexists(target_dir) and not is_reusable_cache_directory(target_dir):
            remove_file_or_defer(
                target_dir,
                defer_cleanup=ctx.defer_cleanup,
            )
        if os.path.lexists(target_dir) and not is_reusable_cache_directory(target_dir):
            raise OSError("Failed to reclaim malformed SquashFS mount target")
        if is_reusable_cache_directory(
            target_dir
        ) and registry_artifact_mounts.is_mount(target_dir):
            return target_dir

        ensure_cache_entry_directory(ctx.paths)
        ensure_real_directory(target_dir)

        logger.info(
            "Materializing SquashFS registry artifact",
            cache_key=ctx.cache_key,
            artifact_uri=_artifact_uri_for_logging(self.uri),
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
            artifact_uri=_artifact_uri_for_logging(self.uri),
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
        validate_cache_entry_path(ctx.paths)
        target_dir = ctx.paths.squashfs_extract_dir
        if _is_reusable_extraction_dir(
            target_dir,
            defer_cleanup=ctx.defer_cleanup,
        ):
            return target_dir

        ensure_cache_entry_directory(ctx.paths)

        logger.info(
            "Extracting SquashFS registry artifact",
            cache_key=ctx.cache_key,
            artifact_uri=_artifact_uri_for_logging(self.uri),
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
                extracted_size += _directory_records_size_bound(
                    (temp_dir.name, target_dir.name),
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
                    artifact_uri=_artifact_uri_for_logging(self.uri),
                    artifact_format=self.format.value,
                    download_ms=f"{download_elapsed:.1f}",
                    extract_ms=f"{extract_elapsed:.1f}",
                    total_ms=f"{total_elapsed:.1f}",
                )
            except OSError:
                if _is_reusable_extraction_dir(
                    target_dir,
                    defer_cleanup=ctx.defer_cleanup,
                ):
                    logger.debug(
                        "SquashFS already extracted by another process",
                        cache_key=ctx.cache_key,
                        artifact_uri=_artifact_uri_for_logging(self.uri),
                        artifact_format=self.format.value,
                    )
                else:
                    raise
        finally:
            await remove_tree_rejoin_on_cancel(
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
        await communicate_process_group(proc)

        if proc.returncode == 0:
            return

        raise RegistryArtifactExtractionError()

    async def _squashfs_extracted_size(
        self,
        image_path: Path,
        *,
        allocation_unit: int,
    ) -> int:
        """Return an allocated-size bound for an extracted SquashFS image."""
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
        stdout, _ = await communicate_process_group(proc)

        if proc.returncode != 0:
            raise RegistryArtifactExtractionError()
        try:
            return _squashfs_listing_size(stdout, allocation_unit=allocation_unit)
        except Exception:
            raise RegistryArtifactExtractionError() from None


@dataclass(frozen=True, slots=True)
class TarballArtifact(RegistryArtifact):
    """Legacy gzip tarball registry environment."""

    @property
    def format(self) -> RegistryArtifactFormat:
        return RegistryArtifactFormat.TAR_GZ

    def cached_path(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path] | None:
        validate_cache_entry_path(ctx.paths)
        if _is_reusable_extraction_dir(
            ctx.paths.tarball_target_dir,
            defer_cleanup=ctx.defer_cleanup,
        ):
            logger.debug(
                "Using cached tarball extraction",
                cache_key=ctx.cache_key,
            )
            return [ctx.paths.tarball_target_dir]
        return None

    async def materialize(
        self, ctx: RegistryArtifactMaterializationContext
    ) -> list[Path]:
        validate_cache_entry_path(ctx.paths)
        target_dir = ctx.paths.tarball_target_dir
        logger.info(
            "Materializing tarball registry artifact",
            cache_key=ctx.cache_key,
            artifact_uri=_artifact_uri_for_logging(self.uri),
            artifact_format=self.format.value,
        )
        start_time = time.monotonic()

        temp_tarball = self._temp_path(ctx, ".tar.gz")
        temp_dir = self._temp_path(ctx, ".tmp")

        try:
            ensure_cache_entry_directory(ctx.paths)

            download_start = time.monotonic()
            await self.download(ctx, temp_tarball)
            download_elapsed = (time.monotonic() - download_start) * 1000

            if (admission := ctx.admission) is not None:
                try:
                    extracted_size = await run_blocking_rejoin_on_cancel(
                        lambda: _tarball_extracted_size(
                            temp_tarball,
                            allocation_unit=admission.allocation_unit,
                        )
                    )
                except Exception:
                    raise RegistryArtifactExtractionError() from None
                extracted_size += _directory_records_size_bound(
                    (temp_dir.name, target_dir.name),
                    allocation_unit=admission.allocation_unit,
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
                    artifact_uri=_artifact_uri_for_logging(self.uri),
                    artifact_format=self.format.value,
                    download_ms=f"{download_elapsed:.1f}",
                    extract_ms=f"{extract_elapsed:.1f}",
                    total_ms=f"{total_elapsed:.1f}",
                )
            except OSError:
                if _is_reusable_extraction_dir(
                    target_dir,
                    defer_cleanup=ctx.defer_cleanup,
                ):
                    logger.debug(
                        "Tarball already extracted by another process",
                        cache_key=ctx.cache_key,
                        artifact_uri=_artifact_uri_for_logging(self.uri),
                        artifact_format=self.format.value,
                    )
                else:
                    raise
        finally:
            try:
                await remove_tree_rejoin_on_cancel(
                    temp_dir,
                    defer_cleanup=ctx.defer_cleanup,
                )
            finally:
                remove_file_or_defer(
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

        try:
            await run_blocking_rejoin_on_cancel(_do_extract)
        except Exception:
            raise RegistryArtifactExtractionError() from None

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
    defer_cleanup: Callable[[Path], None] | None = None,
    published_path: Path | None = None,
) -> None:
    """Download an S3 registry artifact to a local path."""
    try:
        bucket, key = parse_s3_uri(artifact_uri)
    except ValueError:
        raise RegistryArtifactUriError("Invalid registry artifact URI") from None

    ensure_capacity: Callable[[int], Awaitable[None]] | None = None
    if admission is not None:
        metadata_bytes = _directory_entry_size_bound(
            f"{output_path.name}.part",
            allocation_unit=admission.allocation_unit,
        )
        if published_path is not None:
            metadata_bytes += _directory_entry_size_bound(
                published_path.name,
                allocation_unit=admission.allocation_unit,
            )

        async def ensure_download_capacity(content_bytes: int) -> None:
            await admission.ensure_capacity(content_bytes + metadata_bytes)

        ensure_capacity = ensure_download_capacity

    try:
        await blob.download_file_to_path(
            key=key,
            bucket=bucket,
            output_path=output_path,
            max_bytes=None if admission is None else admission.max_bytes,
            ensure_capacity=ensure_capacity,
            defer_cleanup=defer_cleanup,
            redact_log_identifiers=True,
        )
    except FileNotFoundError as e:
        safe_uri = _artifact_uri_for_logging(artifact_uri)
        request = httpx.Request("GET", safe_uri)
        response = httpx.Response(status_code=404, request=request)
        raise httpx.HTTPStatusError(
            f"Registry artifact not found: {safe_uri}",
            request=request,
            response=response,
        ) from e


def compute_registry_artifact_cache_key(artifact_uri: str) -> str:
    """Compute the local cache key for a registry artifact URI."""
    if not artifact_uri:
        return "base"
    # S3 keys are byte-sensitive, so hash the exact URI used for retrieval.
    return hashlib.sha256(artifact_uri.encode()).hexdigest()[:16]


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


def _artifact_uri_for_logging(artifact_uri: str) -> str:
    """Retain only a non-sensitive artifact URI scheme for diagnostics."""
    try:
        parsed = urlsplit(artifact_uri)
    except ValueError:
        return "<redacted-artifact-uri>"
    if not parsed.scheme:
        return "<redacted-artifact-uri>"
    return urlunsplit((parsed.scheme, "<redacted>", "", "", ""))


def _is_reusable_extraction_dir(
    path: Path,
    *,
    defer_cleanup: Callable[[Path], None],
) -> bool:
    """Accept canonical directories and reclaim file or symlink targets."""
    if is_reusable_cache_directory(path):
        return True
    if os.path.lexists(path):
        remove_file_or_defer(path, defer_cleanup=defer_cleanup)
    return False


async def _reuse_or_reclaim_cache_file(
    path: Path,
    *,
    defer_cleanup: Callable[[Path], None],
) -> bool:
    """Reuse a regular file or reclaim a malformed canonical target."""
    if is_reusable_cache_file(path):
        return True
    if not os.path.lexists(path):
        return False
    if is_reusable_cache_directory(path):
        await remove_tree_rejoin_on_cancel(path, defer_cleanup=defer_cleanup)
    else:
        remove_file_or_defer(path, defer_cleanup=defer_cleanup)
    if os.path.lexists(path):
        raise OSError("Failed to reclaim malformed registry artifact cache target")
    return False


_DIRECTORY_ENTRY_OVERHEAD_BYTES = 32
"""Conservative per-child filesystem directory-record overhead."""


def _directory_entry_size_bound(
    child_name: str,
    *,
    allocation_unit: int,
) -> int:
    """Reserve directory storage for one unique child name."""
    return allocated_size_bound(
        _DIRECTORY_ENTRY_OVERHEAD_BYTES + len(os.fsencode(child_name)),
        allocation_unit=allocation_unit,
    )


def _directory_records_size_bound(
    child_names: tuple[str, ...],
    *,
    allocation_unit: int,
) -> int:
    """Reserve parent-directory storage retained across staged publication."""
    return sum(
        _directory_entry_size_bound(name, allocation_unit=allocation_unit)
        for name in child_names
    )


def _tarball_extracted_size(
    tarball_path: Path,
    *,
    allocation_unit: int = 1,
) -> int:
    """Return a conservative allocated-size bound for a tarball extraction."""
    total_bytes = 0
    root_path = PurePosixPath(".")
    required_parent_dirs: set[PurePosixPath] = set()
    explicit_dirs: set[PurePosixPath] = set()
    directory_children: dict[PurePosixPath, set[str]] = {}

    def record_directory_child(path: PurePosixPath) -> None:
        if path != root_path:
            directory_children.setdefault(path.parent, set()).add(path.name)

    with tarfile.open(tarball_path, "r:gz") as tar:
        for member in tar:
            if member.size < 0:
                raise ValueError(
                    f"Registry tarball member has a negative size: {member.name}"
                )
            total_bytes += allocated_size_bound(
                member.size,
                allocation_unit=allocation_unit,
            )
            member_path = PurePosixPath(member.name)
            record_directory_child(member_path)
            if member.isdir():
                explicit_dirs.add(member_path)
            for parent in member_path.parents:
                if parent == root_path:
                    break
                required_parent_dirs.add(parent)
                record_directory_child(parent)

    # Extraction creates a target root even when the archive omits it.
    total_bytes += allocated_size_bound(0, allocation_unit=allocation_unit)
    total_bytes += len(required_parent_dirs - explicit_dirs) * allocation_unit
    total_bytes += sum(
        _directory_entry_size_bound(child_name, allocation_unit=allocation_unit)
        for child_names in directory_children.values()
        for child_name in child_names
    )
    return total_bytes


def _squashfs_listing_size(output: bytes, *, allocation_unit: int = 1) -> int:
    """Bound allocated bytes from ``unsquashfs -lln`` output, failing closed."""
    total_bytes = 0
    parsed_entries = 0
    root_path = PurePosixPath(".")
    listing_root: str | None = None
    required_dirs: set[PurePosixPath] = {root_path}
    listed_dirs: set[PurePosixPath] = set()
    directory_children: dict[PurePosixPath, set[str]] = {}

    def record_directory_child(path: PurePosixPath) -> None:
        if path != root_path:
            directory_children.setdefault(path.parent, set()).add(path.name)

    for raw_line in output.decode(errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(maxsplit=5)
        mode = fields[0]
        if len(mode) != 10 or mode[0] not in "bcdlps-":
            continue
        if len(fields) < 6 or "/" not in fields[1] or not fields[2].isdigit():
            raise ValueError("Could not parse SquashFS listing line")

        listed_path_text = fields[5]
        if mode[0] == "l":
            listed_path_text = listed_path_text.split(" -> ", maxsplit=1)[0]
        listed_path = PurePosixPath(listed_path_text)
        if listed_path.is_absolute() or not listed_path.parts:
            raise ValueError("Could not parse SquashFS listing path")
        if listing_root is None:
            listing_root = listed_path.parts[0]
        elif listed_path.parts[0] != listing_root:
            raise ValueError("Inconsistent SquashFS listing root")

        relative_parts = listed_path.parts[1:]
        entry_path = PurePosixPath(*relative_parts) if relative_parts else root_path
        if entry_path == root_path and mode[0] != "d":
            raise ValueError("Could not parse SquashFS listing root")

        parsed_entries += 1
        total_bytes += allocated_size_bound(
            int(fields[2]),
            allocation_unit=allocation_unit,
        )
        record_directory_child(entry_path)
        if mode[0] == "d":
            listed_dirs.add(entry_path)
        for parent in entry_path.parents:
            required_dirs.add(parent)
            if parent == root_path:
                break
            record_directory_child(parent)

    if parsed_entries == 0:
        raise ValueError("Could not parse any SquashFS listing entries")
    total_bytes += len(required_dirs - listed_dirs) * allocation_unit
    total_bytes += sum(
        _directory_entry_size_bound(child_name, allocation_unit=allocation_unit)
        for child_names in directory_children.values()
        for child_name in child_names
    )
    return total_bytes


@dataclass(slots=True)
class _RegistryArtifactLease:
    """Own exactly one artifact pin and its cancellation-safe release."""

    cache: RegistryArtifactCache
    artifact_uri: str
    cache_key: str | None
    paths: list[Path] | None = None
    paths_may_be_modified: bool = False
    _acquired: bool = False
    _closed: bool = False

    def mark_acquired(self) -> None:
        """Record the point after which this handle must release a pin."""
        if self.cache_key is None or self._acquired:
            raise RuntimeError("Invalid registry artifact lease acquisition")
        self._acquired = True

    async def __aenter__(self) -> list[Path]:
        try:
            self.paths = await self.cache._acquire_artifact(self)
        except BaseException as operation_error:
            try:
                await self.aclose()
            except BaseException as cleanup_error:
                raise operation_error from cleanup_error
            raise
        return self.paths

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type
        try:
            await self.aclose()
        except BaseException as cleanup_error:
            if exc_value is not None:
                raise exc_value.with_traceback(traceback) from cleanup_error
            if not isinstance(cleanup_error, Exception):
                raise
            logger.error(
                "Registry artifact lease cleanup failed; preserving caller outcome",
                cache_dir=str(self.cache.cache_dir),
                error_type=type(cleanup_error).__name__,
            )

    async def aclose(self) -> None:
        """Release the pin exactly once and finish all resulting maintenance."""
        if self._closed:
            return
        self._closed = True
        cache_key = self.cache_key
        if cache_key is None or not self._acquired:
            return
        self._acquired = False

        if self.paths_may_be_modified:
            self.cache._budget_dirty = True
        idle = self.cache._release_lease(cache_key)
        try:
            if idle or self.cache._budget_dirty:
                cleanup_task = asyncio.ensure_future(
                    self.cache._finish_lease_cleanup(
                        [cache_key] if idle else [],
                    )
                )
                await rejoin_future_through_cancellation(cleanup_task)
        finally:
            self.cache._request_runtime_retirement_if_entry_missing(cache_key)


class RegistryArtifactCache(RegistryArtifactCacheStorage):
    """Materializes registry artifacts into executor-local Python paths."""

    @asynccontextmanager
    async def lease(
        self,
        artifact_uris: list[str] | None,
        *,
        paths_may_be_modified: bool = False,
    ) -> AsyncGenerator[list[Path]]:
        """Materialize registry artifacts and pin them for the life of the context.

        Leased cache entries are never evicted, so callers may keep importing
        from the returned paths until the context exits.

        Args:
            artifact_uris: Registry artifact URIs in deterministic PYTHONPATH
                order. Empty input requests no additional import paths.
            paths_may_be_modified: Whether the consumer can write to returned
                paths. Mutable leases re-arm budget convergence after use.

        Yields:
            Importable Python paths for the requested artifacts.
        """
        if not artifact_uris:
            logger.info("No registry artifact URIs provided")
            yield []
            return

        if any(_is_cache_entry_uri(uri) for uri in artifact_uris):
            await self.ensure_swept()

        async with contextlib.AsyncExitStack() as leases:
            handles: list[_RegistryArtifactLease] = []
            registry_paths: list[Path] = []
            for artifact_uri in artifact_uris:
                cache_key = (
                    compute_registry_artifact_cache_key(artifact_uri)
                    if _is_cache_entry_uri(artifact_uri)
                    else None
                )
                handle = _RegistryArtifactLease(
                    cache=self,
                    artifact_uri=artifact_uri,
                    cache_key=cache_key,
                )
                registry_paths.extend(await leases.enter_async_context(handle))
                handles.append(handle)
            logger.info(
                "Using registry artifact environments",
                count=len(registry_paths),
            )
            if paths_may_be_modified:
                for handle in handles:
                    handle.paths_may_be_modified = True
            yield registry_paths

    async def _finish_lease_cleanup(self, idle_keys: list[str]) -> None:
        """Unmount every newly idle entry and converge the cache budget."""
        for cache_key in idle_keys:
            await self._unmount_idle_entry(cache_key)
        await self._converge_cache_budget()

    async def _acquire_artifact(
        self,
        lease: _RegistryArtifactLease,
    ) -> list[Path]:
        """Materialize the artifact and transfer its pin to ``lease``."""
        artifact_uri = lease.artifact_uri
        cache_key = lease.cache_key
        if cache_key is None:
            builtin_key = compute_registry_artifact_cache_key(artifact_uri)
            ctx = self._context_for(builtin_key)
            candidates = await self._artifact_candidates(ctx, artifact_uri)
            return await self._materialize_candidates(ctx, candidates)

        ctx = self._context_for(cache_key)
        async with self._runtime_lock(cache_key):
            self._acquire_lease(cache_key)
            lease.mark_acquired()
            if cached_paths := self._locally_cached_path(ctx, artifact_uri):
                return cached_paths

        async with self._admission_lock:
            async with self._runtime_lock(cache_key):
                if cached_paths := self._locally_cached_path(ctx, artifact_uri):
                    return cached_paths
                ctx = self._context_for(
                    cache_key,
                    admission=self._admission_for(cache_key),
                )
                candidates = await self._artifact_candidates(ctx, artifact_uri)
                # Recheck after acquiring the lock.
                if cached_paths := self._first_cached_path(candidates, ctx):
                    return cached_paths
                paths = await self._materialize_candidates(ctx, candidates)
                self._touch_entry(cache_key)

        # Enforce entry count and final measured size after publication,
        # outside the per-key lock. Peak-byte reservations may already
        # have reclaimed idle LRU entries when that was required to keep
        # staging and extraction within the hard byte cap.
        try:
            await self._enforce_cache_budget(protected_key=cache_key)
        except OSError as e:
            # Cache maintenance must never block artifact admission.
            logger.warning(
                "Failed to enforce registry artifact cache budget",
                cache_dir=str(self.cache_dir),
                error=str(e),
            )
        return paths

    async def _materialize_candidates(
        self,
        ctx: RegistryArtifactMaterializationContext,
        candidates: list[RegistryArtifact],
    ) -> list[Path]:
        """Materialize the first viable artifact candidate.

        Callers hold the cache key's lock for evictable entries.
        """
        cache_key = ctx.cache_key
        for index, artifact in enumerate(candidates):
            try:
                logger.info(
                    "Trying registry artifact candidate",
                    cache_key=cache_key,
                    artifact_uri=_artifact_uri_for_logging(artifact.uri),
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
                    artifact_uri=_artifact_uri_for_logging(artifact.uri),
                    artifact_format=artifact.format.value,
                    error_type=type(e).__name__,
                )

        raise RuntimeError(f"No registry artifact candidates for {ctx.cache_key}")

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

    def _locally_cached_path(
        self,
        ctx: RegistryArtifactMaterializationContext,
        artifact_uri: str,
    ) -> list[Path] | None:
        """Return a reusable local candidate without probing remote sidecars."""
        include_squashfs_sidecar = (
            _bundled_builtin_registry_version(artifact_uri) is None
            and _artifact_format(artifact_uri) == RegistryArtifactFormat.TAR_GZ
            and self._can_try_squashfs()
        )
        candidates = self._candidate_artifacts(
            ctx,
            artifact_uri,
            include_squashfs_sidecar=include_squashfs_sidecar,
        )
        return self._first_cached_path(candidates, ctx)

    def _candidate_artifacts(
        self,
        ctx: RegistryArtifactMaterializationContext,
        artifact_uri: str,
        *,
        include_squashfs_sidecar: bool,
    ) -> list[RegistryArtifact]:
        """Build artifact candidates in executor preference order."""
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
            candidates: list[RegistryArtifact] = [
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

        candidates = []
        if include_squashfs_sidecar and (
            squashfs_uri := _squashfs_sidecar_uri(artifact_uri)
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

    def _remove_unpublished_entry(
        self,
        ctx: RegistryArtifactMaterializationContext,
    ) -> None:
        """Remove an entry shell when an attempt published no reusable artifact.

        Callers hold the cache key lock. ``rmdir`` only removes empty
        directories, so canonical artifacts and unknown contents are preserved.
        """
        paths = ctx.paths
        validate_cache_entry_path(paths)
        try:
            if registry_artifact_mounts.is_mount(paths.squashfs_mount_dir):
                return
        except OSError:
            return

        for directory in (paths.squashfs_mount_dir, paths.entry_dir):
            with contextlib.suppress(OSError):
                directory.rmdir()
        self._request_runtime_retirement_if_entry_missing(ctx.cache_key)

    async def _artifact_candidates(
        self,
        ctx: RegistryArtifactMaterializationContext,
        artifact_uri: str,
    ) -> list[RegistryArtifact]:
        """Return artifact candidates in executor preference order."""
        if _bundled_builtin_registry_version(artifact_uri) is not None:
            return self._candidate_artifacts(
                ctx,
                artifact_uri,
                include_squashfs_sidecar=False,
            )

        artifact_format = _artifact_format(artifact_uri)
        include_squashfs_sidecar = False
        if (
            artifact_format == RegistryArtifactFormat.TAR_GZ
            and self._can_try_squashfs()
            and (squashfs_uri := _squashfs_sidecar_uri(artifact_uri))
        ):
            include_squashfs_sidecar = (
                ctx.paths.squashfs_image_path.exists()
                or await self._sidecar_exists(
                    base_uri=artifact_uri,
                    sidecar_uri=squashfs_uri,
                    artifact_format=RegistryArtifactFormat.SQUASHFS,
                )
            )

        return self._candidate_artifacts(
            ctx,
            artifact_uri,
            include_squashfs_sidecar=include_squashfs_sidecar,
        )

    async def _sidecar_exists(
        self,
        *,
        base_uri: str,
        sidecar_uri: str,
        artifact_format: RegistryArtifactFormat,
    ) -> bool:
        """Return whether a registry sidecar exists, logging lookup failures."""
        try:
            bucket, key = parse_s3_uri(sidecar_uri)
            if await blob.file_exists(key=key, bucket=bucket):
                logger.debug(
                    "Using registry artifact sidecar",
                    artifact_uri=_artifact_uri_for_logging(base_uri),
                    sidecar_uri=_artifact_uri_for_logging(sidecar_uri),
                    artifact_format=artifact_format.value,
                )
                return True
        except Exception as e:
            logger.warning(
                "Failed to check for registry artifact sidecar, falling back",
                artifact_uri=_artifact_uri_for_logging(base_uri),
                sidecar_uri=_artifact_uri_for_logging(sidecar_uri),
                artifact_format=artifact_format.value,
                error_type=type(e).__name__,
            )

        return False

    def _can_try_squashfs(self) -> bool:
        """Return whether this process should prefer SquashFS artifacts."""
        return config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED
