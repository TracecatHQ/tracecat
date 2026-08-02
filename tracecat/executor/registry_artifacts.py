"""Registry artifact resolution and local materialization for executors."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from tracecat import config
from tracecat.executor import registry_artifact_mounts
from tracecat.executor.registry_artifact_cache_state import (
    BASE_PYTHONPATH_DIR_NAME,
    CACHE_ENTRIES_DIR_NAME,
    CACHE_STAGING_DIR_NAME,
    CACHE_TRASH_DIR_NAME,
    RegistryArtifactCacheLoopError,
    RegistryArtifactRuntimeState,
)
from tracecat.executor.registry_artifact_materialization import (
    BUNDLED_BUILTIN_REGISTRY_URI_PREFIX,
    SQUASHFS_MOUNT_OPTIONS,
    BuiltinArtifact,
    RegistryArtifact,
    RegistryArtifactAdmission,
    RegistryArtifactFormat,
    RegistryArtifactMaterializationContext,
    RegistryArtifactPaths,
    SquashfsArtifact,
    SquashfsMountCommandError,
    TarballArtifact,
    _artifact_format,
    _artifact_uri_for_logging,
    _bundled_builtin_registry_import_paths,
    _bundled_builtin_registry_version,
    _download_s3_artifact,
    _is_cache_entry_uri,
    _is_reusable_cache_file,
    _squashfs_listing_size,
    _squashfs_sidecar_uri,
    _tarball_extracted_size,
    _tarball_uri_for_squashfs,
    bundled_builtin_registry_uri,
    compute_registry_artifact_cache_key,
)
from tracecat.executor.registry_artifact_storage import (
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheEntry,
    RegistryArtifactEviction,
    _allocated_stat_size,
    _delete_cache_path,
    _delete_cache_path_off_loop,
    _directory_footprint,
    _move_entry_to_trash,
    _RegistryArtifactCacheStorage,
    _unique_work_path,
)
from tracecat.logger import logger
from tracecat.registry.artifact_keys import parse_s3_uri
from tracecat.storage import blob

__all__ = [
    "BASE_PYTHONPATH_DIR_NAME",
    "BUNDLED_BUILTIN_REGISTRY_URI_PREFIX",
    "CACHE_ENTRIES_DIR_NAME",
    "CACHE_STAGING_DIR_NAME",
    "CACHE_TRASH_DIR_NAME",
    "SQUASHFS_MOUNT_OPTIONS",
    "BuiltinArtifact",
    "RegistryArtifact",
    "RegistryArtifactAdmission",
    "RegistryArtifactCache",
    "RegistryArtifactCacheCapacityError",
    "RegistryArtifactCacheEntry",
    "RegistryArtifactCacheLoopError",
    "RegistryArtifactEviction",
    "RegistryArtifactFormat",
    "RegistryArtifactMaterializationContext",
    "RegistryArtifactPaths",
    "RegistryArtifactRuntimeState",
    "SquashfsArtifact",
    "SquashfsMountCommandError",
    "TarballArtifact",
    "_artifact_format",
    "_artifact_uri_for_logging",
    "_allocated_stat_size",
    "_bundled_builtin_registry_import_paths",
    "_bundled_builtin_registry_version",
    "_delete_cache_path",
    "_delete_cache_path_off_loop",
    "_directory_footprint",
    "_download_s3_artifact",
    "_is_cache_entry_uri",
    "_move_entry_to_trash",
    "_squashfs_listing_size",
    "_squashfs_sidecar_uri",
    "_tarball_extracted_size",
    "_tarball_uri_for_squashfs",
    "_unique_work_path",
    "bundled_builtin_registry_uri",
    "compute_registry_artifact_cache_key",
]


class RegistryArtifactCache(_RegistryArtifactCacheStorage):
    """Materializes and leases executor-local registry artifact paths."""

    @asynccontextmanager
    async def lease(
        self,
        artifact_uris: list[str] | None,
        *,
        paths_may_be_modified: bool = False,
    ) -> AsyncIterator[list[Path]]:
        """Materialize registry artifacts and pin them for the life of the context.

        Leased cache entries are never evicted, so callers may keep importing
        from the returned paths until the context exits.

        Args:
            artifact_uris: Registry artifact URIs in deterministic PYTHONPATH
                order, or None to use the base PYTHONPATH directory.
            paths_may_be_modified: Whether the consumer can write to returned
                cache paths. Mutable leases re-arm byte-budget convergence when
                execution ends so post-admission growth is measured.

        Yields:
            Importable Python paths for the requested artifacts.
        """
        if not artifact_uris:
            logger.info("No registry artifact URIs provided, using base PYTHONPATH")
            yield [self._base_pythonpath_dir()]
            return

        if not any(_is_cache_entry_uri(uri) for uri in artifact_uris):
            cache_free_paths: list[Path] = []
            for artifact_uri in artifact_uris:
                _, artifact_paths = await self._lease_artifact(artifact_uri)
                cache_free_paths.extend(artifact_paths)
            logger.info(
                "Using cache-free registry artifact environments",
                count=len(cache_free_paths),
            )
            yield cache_free_paths
            return

        await self.ensure_swept()

        leased_keys: list[str] = []
        lease_setup_complete = False
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
            lease_setup_complete = True
            yield registry_paths
        finally:
            if paths_may_be_modified and lease_setup_complete and leased_keys:
                self._budget_dirty = True
            idle_keys = [
                cache_key for cache_key in leased_keys if self._release_lease(cache_key)
            ]
            cleanup_task = asyncio.ensure_future(
                self._finish_lease_cleanup(
                    idle_keys,
                    converge=not lease_setup_complete or bool(idle_keys),
                )
            )
            pending_cancellation: asyncio.CancelledError | None = None
            while True:
                try:
                    await asyncio.shield(cleanup_task)
                    break
                except asyncio.CancelledError as e:
                    if cleanup_task.cancelled():
                        raise
                    pending_cancellation = e
                except Exception as e:
                    logger.error(
                        "Registry artifact lease cleanup failed; preserving caller outcome",
                        cache_dir=str(self.cache_dir),
                        error_type=type(e).__name__,
                    )
                    break

            if pending_cancellation is not None:
                raise pending_cancellation

    async def _finish_lease_cleanup(
        self,
        idle_keys: list[str],
        *,
        converge: bool,
    ) -> None:
        """Unmount newly idle entries and converge after meaningful changes."""
        for cache_key in idle_keys:
            await self._unmount_idle_entry(cache_key)
        await self._retry_failed_unmounts(excluded=set(idle_keys))
        if converge:
            await self._converge_cache_budget()

    async def _lease_artifact(self, artifact_uri: str) -> tuple[str | None, list[Path]]:
        """Pin and materialize one artifact, returning its releasable cache key."""
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        ctx = self._context_for(cache_key)
        if not _is_cache_entry_uri(artifact_uri):
            candidates = await self._artifact_candidates(ctx, artifact_uri)
            return None, await self._materialize_candidates(ctx, candidates)

        lease_acquired = False
        try:
            async with self._runtime_lock(cache_key):
                self._acquire_lease(cache_key)
                lease_acquired = True
                if cached_paths := self._locally_cached_path(ctx, artifact_uri):
                    return cache_key, cached_paths

            async with self._admission_lock:
                async with self._runtime_lock(cache_key):
                    if cached_paths := self._locally_cached_path(ctx, artifact_uri):
                        return cache_key, cached_paths
                    ctx = self._context_for(
                        cache_key,
                        admission=self._admission_for(cache_key),
                    )
                    candidates = await self._artifact_candidates(ctx, artifact_uri)
                    if cached_paths := self._first_cached_path(candidates, ctx):
                        return cache_key, cached_paths
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
            return cache_key, paths
        except BaseException:
            if lease_acquired and self._release_lease(cache_key):
                rollback_task = asyncio.ensure_future(
                    self._unmount_idle_entry(cache_key)
                )
                while not rollback_task.done():
                    try:
                        await asyncio.shield(rollback_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not rollback_task.cancelled():
                    with contextlib.suppress(Exception):
                        rollback_task.result()
            raise

    async def _materialize_candidates(
        self,
        ctx: RegistryArtifactMaterializationContext,
        candidates: list[RegistryArtifact],
    ) -> list[Path]:
        """Materialize the first viable artifact candidate.

        Callers hold the cache key's lock for evictable entries.
        """
        if cached_paths := self._first_cached_path(candidates, ctx):
            return cached_paths

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
                artifact.discard_failed_materialization(ctx)
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
        artifact_format = _artifact_format(artifact_uri)
        candidates: list[RegistryArtifact] = []
        if artifact_format == RegistryArtifactFormat.SQUASHFS:
            candidates.append(
                SquashfsArtifact(uri=artifact_uri, cache_key=ctx.cache_key)
            )
            if tarball_uri := _tarball_uri_for_squashfs(artifact_uri):
                candidates.append(
                    TarballArtifact(uri=tarball_uri, cache_key=ctx.cache_key)
                )
        else:
            if self._can_try_squashfs() and (
                squashfs_uri := _squashfs_sidecar_uri(artifact_uri)
            ):
                candidates.append(
                    SquashfsArtifact(uri=squashfs_uri, cache_key=ctx.cache_key)
                )
            candidates.append(
                TarballArtifact(uri=artifact_uri, cache_key=ctx.cache_key)
            )
        return self._first_cached_path(candidates, ctx)

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
            if registry_artifact_mounts.is_mount(paths.squashfs_mount_dir):
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
                if _is_reusable_cache_file(ctx.paths.squashfs_image_path):
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
