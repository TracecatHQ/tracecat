"""Registry artifact resolution and local materialization for executors."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sysconfig
import tarfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
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


@dataclass(slots=True)
class RegistryArtifactMaterializationContext:
    """Shared runtime state for artifact materialization."""

    cache_key: str
    cache_dir: Path
    paths: RegistryArtifactPaths
    squashfs_mount_state: SquashfsMountState

    def can_mount_squashfs(self) -> bool:
        return (
            config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED
            and not self.squashfs_mount_state.disabled
            and (shutil.which("mount") is not None)
        )

    def disable_squashfs_mount(self) -> None:
        self.squashfs_mount_state.disabled = True


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
            try:
                return [await self.mount(ctx, image_path)]
            except Exception as e:
                ctx.disable_squashfs_mount()
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
        target_dir = ctx.paths.squashfs_mount_dir
        if target_dir.is_mount():
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


class RegistryArtifactCache:
    """Materializes registry artifacts into executor-local Python paths."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()
        self._squashfs_mount_state = SquashfsMountState()

    async def ensure_environment(self, artifact_uri: str | None) -> list[Path]:
        """Materialize an optional registry artifact and return PYTHONPATH entries."""
        if not artifact_uri:
            return []
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        return await self.materialize(cache_key, artifact_uri)

    async def materialize(self, cache_key: str, artifact_uri: str) -> list[Path]:
        """Materialize a registry artifact as local importable directories."""
        ctx = self._context_for(cache_key)
        candidates = await self._artifact_candidates(ctx, artifact_uri)

        if cached_paths := self._first_cached_path(candidates, ctx):
            return cached_paths

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
                    return await artifact.materialize(ctx)
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
