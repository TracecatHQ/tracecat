"""Disk-budget enforcement and cleanup for registry artifact caches."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tracecat import config
from tracecat.executor.registry_artifact_cache_state import (
    _RegistryArtifactCacheState,
)
from tracecat.executor.registry_artifact_materialization import (
    RegistryArtifactAdmission,
    _communicate_rejoin_on_cancel,
)
from tracecat.logger import logger


class RegistryArtifactCacheCapacityError(RuntimeError):
    """A cold artifact cannot fit within the configured cache byte budget."""

    def __init__(
        self,
        *,
        current_bytes: int,
        additional_bytes: int,
        max_bytes: int,
    ) -> None:
        super().__init__(
            "Registry artifact admission exceeds the cache byte budget: "
            f"current_bytes={current_bytes}, additional_bytes={additional_bytes}, "
            f"max_bytes={max_bytes}"
        )
        self.current_bytes = current_bytes
        self.additional_bytes = additional_bytes
        self.max_bytes = max_bytes


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


class _RegistryArtifactCacheStorage(_RegistryArtifactCacheState):
    """Adds startup recovery, eviction, and byte-budget enforcement."""

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

    def _admission_for(self, cache_key: str) -> RegistryArtifactAdmission | None:
        """Return byte-bound admission controls for one cold cache key."""
        max_bytes = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES
        if max_bytes <= 0:
            return None

        async def ensure_capacity(additional_bytes: int) -> None:
            await self._ensure_cache_capacity(
                additional_bytes=additional_bytes,
                protected_key=cache_key,
                max_bytes=max_bytes,
            )

        return RegistryArtifactAdmission(
            max_bytes=max_bytes,
            ensure_capacity=ensure_capacity,
        )

    async def _unmount_idle_entry(self, cache_key: str) -> None:
        """Best-effort unmount an entry after its final lease is released."""
        try:
            unmounted = await self._unmount_entry(cache_key)
        except OSError as e:
            self._failed_unmounts.add(cache_key)
            logger.warning(
                "Failed to release idle registry artifact mount",
                cache_key=cache_key,
                error=str(e),
            )
            return

        if unmounted:
            self._failed_unmounts.discard(cache_key)
            return

        mount_dir = self._paths_for(cache_key).squashfs_mount_dir
        try:
            retry = self._refcount(cache_key) == 0 and mount_dir.is_mount()
        except OSError as e:
            retry = True
            logger.warning(
                "Failed to inspect idle registry artifact mount",
                cache_key=cache_key,
                mount_dir=str(mount_dir),
                error=str(e),
            )

        if retry:
            self._failed_unmounts.add(cache_key)
        else:
            self._failed_unmounts.discard(cache_key)

    async def _retry_failed_unmounts(self, *, excluded: set[str]) -> None:
        """Retry prior unmount failures once on a later lease cleanup."""
        for cache_key in sorted(self._failed_unmounts - excluded):
            await self._unmount_idle_entry(cache_key)

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

        The admission lock excludes cold writers before the budget lock begins
        a scan/select/evict pass. Callers invoke enforcement without holding a
        per-key lock. Cold writers already hold the admission lock and use
        ``_ensure_cache_capacity`` for their staged reservations instead.

        Args:
            protected_key: Newly materialized cache key. It is counted against
                the budget when present but never evicted. None when enforcing
                against idle entries after leases are released.

        Returns:
            Whether the cache is within budget once eviction has finished.
        """
        async with self._admission_lock:
            async with self._budget_lock:
                within_budget = await self._enforce_cache_budget_locked(
                    protected_key=protected_key
                )
                if within_budget:
                    # Clear while cold writers remain excluded so a later
                    # materialization cannot have its dirty signal erased.
                    self._budget_dirty = False
                return within_budget

    async def _enforce_cache_budget_locked(
        self,
        *,
        protected_key: str | None,
    ) -> bool:
        """Enforce entry and byte limits while both cache-wide locks are held."""
        trash_clean, startup_clean = await asyncio.gather(
            asyncio.to_thread(self._clear_work_dir, self.trash_dir),
            asyncio.to_thread(self._retry_deferred_staging_cleanup),
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

    async def _ensure_cache_capacity(
        self,
        *,
        additional_bytes: int,
        protected_key: str,
        max_bytes: int,
    ) -> None:
        """Reserve peak bytes for a cold writer without exceeding the cap.

        The caller holds the admission lock and its key lock. Every normal
        budget pass takes the admission lock first, so acquiring the budget
        lock here cannot deadlock with eviction of the protected key.
        """
        if additional_bytes < 0:
            raise ValueError("additional_bytes must be non-negative")

        async with self._budget_lock:
            trash_clean, startup_clean = await asyncio.gather(
                asyncio.to_thread(self._clear_work_dir, self.trash_dir),
                asyncio.to_thread(self._retry_deferred_staging_cleanup),
            )
            entries = await asyncio.to_thread(self._scan_cache_entries)
            staging_bytes, trash_bytes = await asyncio.gather(
                asyncio.to_thread(_directory_footprint, self.staging_dir),
                asyncio.to_thread(_directory_footprint, self.trash_dir),
            )
            total_bytes = (
                sum(entry.size_bytes for entry in entries.values())
                + staging_bytes
                + trash_bytes
            )
            if (
                not (trash_clean and startup_clean)
                and total_bytes + additional_bytes > max_bytes
            ):
                raise RegistryArtifactCacheCapacityError(
                    current_bytes=total_bytes,
                    additional_bytes=additional_bytes,
                    max_bytes=max_bytes,
                )
            skipped = {protected_key}

            while total_bytes + additional_bytes > max_bytes:
                candidate = self._least_recently_used(
                    entries.values(),
                    excluded=skipped,
                )
                if candidate is None:
                    raise RegistryArtifactCacheCapacityError(
                        current_bytes=total_bytes,
                        additional_bytes=additional_bytes,
                        max_bytes=max_bytes,
                    )

                eviction = await self._evict_entry(candidate.cache_key)
                if eviction.retired:
                    del entries[candidate.cache_key]
                    if not eviction.reclaimed:
                        raise RegistryArtifactCacheCapacityError(
                            current_bytes=total_bytes,
                            additional_bytes=additional_bytes,
                            max_bytes=max_bytes,
                        )
                    total_bytes -= candidate.size_bytes
                else:
                    skipped.add(candidate.cache_key)

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
        runtime = self._runtime.get(cache_key)
        if runtime is not None and runtime.lock.locked():
            logger.debug(
                "Skipping unmount of busy registry artifact",
                cache_key=cache_key,
            )
            return False

        async with self._runtime_lock(cache_key):
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
        runtime = self._runtime.get(cache_key)
        if runtime is not None and runtime.lock.locked():
            logger.debug(
                "Skipping eviction of busy registry artifact",
                cache_key=cache_key,
            )
            return RegistryArtifactEviction(retired=False, reclaimed=False)

        async with self._runtime_lock(cache_key):
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
        stdout, stderr = await _communicate_rejoin_on_cancel(proc)
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
                    self._deferred_staging_cleanup.discard(path)
                logger.info(
                    "Removed registry artifact work path",
                    path=str(path),
                )
            else:
                deleted = False
                if remember_failures:
                    self._deferred_staging_cleanup.add(path)
        return deleted

    def _retry_deferred_staging_cleanup(self) -> bool:
        """Retry exact failed paths without sweeping live staging work."""
        for path in tuple(self._deferred_staging_cleanup):
            if _delete_cache_path(path):
                self._deferred_staging_cleanup.discard(path)
        return not self._deferred_staging_cleanup

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
