"""Registry artifact resolution and local materialization for executors."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sysconfig
import tarfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx
import tracecat_registry

from tracecat import config
from tracecat.logger import logger
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
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

BUNDLED_BUILTIN_REGISTRY_URI_PREFIX = f"tracecat-builtin://{DEFAULT_REGISTRY_ORIGIN}/"
"""Pseudo-URI for the builtin registry already installed in the executor image."""


@dataclass(frozen=True, slots=True)
class RegistryArtifact:
    """A concrete downloadable registry artifact."""

    uri: str
    format: RegistryArtifactFormat


@dataclass(frozen=True, slots=True)
class RegistryArtifactPaths:
    """Executor-local cache paths for one registry artifact key."""

    squashfs_mount_dir: Path
    squashfs_extract_dir: Path
    tarball_target_dir: Path


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


def bundled_builtin_registry_uri(version: str) -> str:
    """Return the pseudo-URI for the installed builtin registry package."""
    return f"{BUNDLED_BUILTIN_REGISTRY_URI_PREFIX}{version}"


def _bundled_builtin_registry_version(artifact_uri: str) -> str | None:
    """Return the builtin registry version encoded in a bundled pseudo-URI."""
    if not artifact_uri.startswith(BUNDLED_BUILTIN_REGISTRY_URI_PREFIX):
        return None
    version = artifact_uri.removeprefix(BUNDLED_BUILTIN_REGISTRY_URI_PREFIX)
    return version or None


def _symlink_once(source: Path, dest: Path) -> None:
    """Create a symlink unless another process already created it."""
    try:
        if dest.exists() or dest.is_symlink():
            return
        dest.symlink_to(source, target_is_directory=source.is_dir())
    except FileExistsError:
        return


def _bundled_builtin_registry_import_path(version: str, cache_dir: Path) -> Path:
    """Return an import path for the current builtin registry and its dependencies."""
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
        return site_packages

    cache_key = compute_registry_artifact_cache_key(
        bundled_builtin_registry_uri(version)
    )
    overlay_dir = cache_dir / f"bundled-{cache_key}"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    for child in site_packages.iterdir():
        _symlink_once(child, overlay_dir / child.name)
    _symlink_once(package_dir, overlay_dir / DEFAULT_REGISTRY_ORIGIN)
    logger.info(
        "Prepared bundled builtin registry overlay",
        registry_version=version,
        package_dir=str(package_dir),
        site_packages=str(site_packages),
        overlay_dir=str(overlay_dir),
    )
    return overlay_dir


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
        if version := _bundled_builtin_registry_version(artifact_uri):
            import_path = _bundled_builtin_registry_import_path(version, self.cache_dir)
            logger.info(
                "Using bundled builtin registry environment",
                registry_version=version,
                path=str(import_path),
            )
            return import_path
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        return await self.materialize(cache_key, artifact_uri)

    async def materialize(self, cache_key: str, artifact_uri: str) -> Path:
        """Materialize a registry artifact as a local importable directory."""
        paths = self._paths_for(cache_key)

        if cached_path := self._cached_path(paths):
            return cached_path

        lock = await self._lock_for(cache_key)
        async with lock:
            if cached_path := self._cached_path(paths):
                return cached_path

            candidates = await self._artifact_candidates(cache_key, artifact_uri)
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
                    return await self._materialize_artifact(
                        cache_key=cache_key,
                        artifact=artifact,
                        paths=paths,
                    )
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

    def _squashfs_image_path(self, cache_key: str) -> Path:
        """Return the expected local SquashFS image cache path."""
        return self.cache_dir / f"squashfs-{cache_key}.squashfs"

    def _paths_for(self, cache_key: str) -> RegistryArtifactPaths:
        """Return local cache paths for a registry artifact key."""
        return RegistryArtifactPaths(
            squashfs_mount_dir=self.cache_dir / f"squashfs-{cache_key}",
            squashfs_extract_dir=self.cache_dir / f"unsquashfs-{cache_key}",
            tarball_target_dir=self.cache_dir / f"tarball-{cache_key}",
        )

    def _cached_path(self, paths: RegistryArtifactPaths) -> Path | None:
        """Return an already-materialized path if one exists."""
        if paths.squashfs_mount_dir.is_mount():
            logger.debug(
                "Using cached SquashFS registry mount",
                cache_key=paths.squashfs_mount_dir.name.removeprefix("squashfs-"),
            )
            return paths.squashfs_mount_dir
        if paths.squashfs_extract_dir.exists():
            logger.debug(
                "Using cached SquashFS registry extraction",
                cache_key=paths.squashfs_extract_dir.name.removeprefix("unsquashfs-"),
            )
            return paths.squashfs_extract_dir
        if paths.tarball_target_dir.exists():
            logger.debug(
                "Using cached tarball extraction",
                cache_key=paths.tarball_target_dir.name.removeprefix("tarball-"),
            )
            return paths.tarball_target_dir
        return None

    async def _artifact_candidates(
        self, cache_key: str, artifact_uri: str
    ) -> list[RegistryArtifact]:
        """Return artifact candidates in executor preference order."""
        artifact_format = _artifact_format(artifact_uri)
        if artifact_format == RegistryArtifactFormat.SQUASHFS:
            candidates = [
                RegistryArtifact(
                    uri=artifact_uri,
                    format=RegistryArtifactFormat.SQUASHFS,
                )
            ]
            if tarball_uri := _tarball_uri_for_squashfs(artifact_uri):
                candidates.append(
                    RegistryArtifact(
                        uri=tarball_uri,
                        format=RegistryArtifactFormat.TAR_GZ,
                    )
                )
            return candidates

        candidates: list[RegistryArtifact] = []
        if self._can_try_squashfs():
            squashfs_uri = _squashfs_sidecar_uri(artifact_uri)
            if squashfs_uri:
                if self._squashfs_image_path(cache_key).exists():
                    candidates.append(
                        RegistryArtifact(
                            uri=squashfs_uri,
                            format=RegistryArtifactFormat.SQUASHFS,
                        )
                    )
                elif await self._sidecar_exists(
                    base_uri=artifact_uri,
                    sidecar_uri=squashfs_uri,
                    artifact_format=RegistryArtifactFormat.SQUASHFS,
                ):
                    candidates.append(
                        RegistryArtifact(
                            uri=squashfs_uri,
                            format=RegistryArtifactFormat.SQUASHFS,
                        )
                    )

        candidates.append(
            RegistryArtifact(
                uri=artifact_uri,
                format=RegistryArtifactFormat.TAR_GZ,
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

    def _can_try_squashfs_mount(self) -> bool:
        """Return whether this process should attempt SquashFS mounts."""
        return (
            self._can_try_squashfs()
            and not self._squashfs_mount_disabled
            and (shutil.which("mount") is not None)
        )

    async def _materialize_artifact(
        self,
        *,
        cache_key: str,
        artifact: RegistryArtifact,
        paths: RegistryArtifactPaths,
    ) -> Path:
        """Materialize one registry artifact candidate."""
        if artifact.format == RegistryArtifactFormat.SQUASHFS:
            return await self._materialize_squashfs_artifact(
                cache_key=cache_key,
                artifact=artifact,
                paths=paths,
            )
        if artifact.format == RegistryArtifactFormat.TAR_GZ:
            return await self._materialize_tarball(
                cache_key=cache_key,
                artifact=artifact,
                target_dir=paths.tarball_target_dir,
            )
        raise ValueError(f"Unsupported registry artifact format: {artifact.format}")

    async def _materialize_squashfs_artifact(
        self,
        *,
        cache_key: str,
        artifact: RegistryArtifact,
        paths: RegistryArtifactPaths,
    ) -> Path:
        """Materialize a SquashFS candidate by mounting or extracting it."""
        image_path = self._squashfs_image_path(cache_key)
        if self._can_try_squashfs_mount():
            try:
                return await self._materialize_squashfs(
                    cache_key=cache_key,
                    artifact=artifact,
                    image_path=image_path,
                    target_dir=paths.squashfs_mount_dir,
                )
            except Exception as e:
                self._squashfs_mount_disabled = True
                logger.warning(
                    "Failed to mount SquashFS registry artifact, trying extraction",
                    cache_key=cache_key,
                    artifact_uri=artifact.uri,
                    artifact_format=artifact.format.value,
                    error=str(e),
                )

        return await self._materialize_squashfs_extract(
            cache_key=cache_key,
            artifact=artifact,
            image_path=image_path,
            target_dir=paths.squashfs_extract_dir,
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
            "Materializing SquashFS registry artifact",
            cache_key=cache_key,
            artifact_uri=artifact.uri,
            artifact_format=artifact.format.value,
        )
        start_time = time.monotonic()
        download_elapsed = await self._ensure_squashfs_image(
            cache_key=cache_key,
            artifact=artifact,
            image_path=image_path,
        )

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

    async def _materialize_squashfs_extract(
        self,
        *,
        cache_key: str,
        artifact: RegistryArtifact,
        image_path: Path,
        target_dir: Path,
    ) -> Path:
        """Download and extract a SquashFS registry artifact with unsquashfs."""
        if target_dir.exists():
            return target_dir

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Extracting SquashFS registry artifact",
            cache_key=cache_key,
            artifact_uri=artifact.uri,
            artifact_format=artifact.format.value,
        )
        start_time = time.monotonic()
        download_elapsed = await self._ensure_squashfs_image(
            cache_key=cache_key,
            artifact=artifact,
            image_path=image_path,
        )

        unique_id = id(asyncio.current_task())
        temp_dir = self.cache_dir / f"{cache_key}.{os.getpid()}.{unique_id}.unsquashfs"
        try:
            extract_start = time.monotonic()
            temp_dir.mkdir(parents=True, exist_ok=True)
            await self._extract_squashfs(image_path, temp_dir)
            extract_elapsed = (time.monotonic() - extract_start) * 1000

            try:
                temp_dir.rename(target_dir)
                total_elapsed = (time.monotonic() - start_time) * 1000
                logger.info(
                    "SquashFS registry artifact extracted",
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
                        "SquashFS already extracted by another process",
                        cache_key=cache_key,
                        artifact_uri=artifact.uri,
                        artifact_format=artifact.format.value,
                    )
                else:
                    raise
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

        return target_dir

    async def _ensure_squashfs_image(
        self,
        *,
        cache_key: str,
        artifact: RegistryArtifact,
        image_path: Path,
    ) -> float:
        """Ensure a SquashFS artifact is downloaded locally."""
        if image_path.exists():
            return 0.0

        unique_id = id(asyncio.current_task())
        temp_image = self.cache_dir / f"{cache_key}.{os.getpid()}.{unique_id}.squashfs"
        try:
            download_start = time.monotonic()
            await self._download_artifact(artifact.uri, temp_image)
            try:
                temp_image.rename(image_path)
            except OSError:
                if not image_path.exists():
                    raise
            return (time.monotonic() - download_start) * 1000
        finally:
            temp_image.unlink(missing_ok=True)

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
            "Materializing tarball registry artifact",
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

    async def _extract_squashfs(self, image_path: Path, target_dir: Path) -> None:
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

    async def _download_artifact(self, artifact_uri: str, output_path: Path) -> None:
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
