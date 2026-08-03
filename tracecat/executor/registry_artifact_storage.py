"""Filesystem ownership and budget enforcement for registry artifacts."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import re
import shutil
import stat
import threading
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from tracecat import config
from tracecat.concurrency import (
    drain_future_through_cancellation,
    rejoin_future_on_cancel,
    run_blocking_rejoin_on_cancel,
)
from tracecat.executor import registry_artifact_mounts
from tracecat.executor.registry_artifact_budget import (
    RegistryArtifactCacheBudget,
    RegistryArtifactCacheEntry,
    RegistryArtifactCacheSnapshot,
    plan_registry_artifact_evictions,
)
from tracecat.logger import logger

__all__ = (
    "BASE_PYTHONPATH_DIR_NAME",
    "CACHE_ENTRIES_DIR_NAME",
    "CACHE_STAGING_DIR_NAME",
    "CACHE_TRASH_DIR_NAME",
    "RegistryArtifactAdmission",
    "RegistryArtifactCacheCapacityError",
    "RegistryArtifactCacheLoopError",
    "RegistryArtifactCacheStorage",
    "RegistryArtifactEviction",
    "RegistryArtifactEvictionPass",
    "RegistryArtifactMaterializationContext",
    "RegistryArtifactPaths",
    "RegistryArtifactRuntimeState",
    "allocated_size_bound",
    "communicate_rejoin_on_cancel",
    "ensure_cache_entry_directory",
    "ensure_real_directory",
    "is_reusable_cache_directory",
    "is_reusable_cache_file",
    "remove_file_or_defer",
    "remove_tree_rejoin_on_cancel",
    "unique_work_path",
    "validate_cache_entry_path",
)

BASE_PYTHONPATH_DIR_NAME = "base"
"""Cache subdirectory used when no registry artifact is requested."""

CACHE_ENTRIES_DIR_NAME = "entries"
"""Directory containing one atomic subdirectory per cache key."""

CACHE_STAGING_DIR_NAME = "staging"
"""Directory containing in-progress materialization scratch."""

CACHE_TRASH_DIR_NAME = "trash"
"""Directory containing retired entries pending physical deletion."""

_LEGACY_CACHE_PATH_PATTERN = re.compile(
    r"(?:squashfs|unsquashfs|tarball)-[0-9a-f]{16}(?:\.squashfs)?"
    r"|[0-9a-f]{16}\.\d+\.\d+\.(?:squashfs|unsquashfs|tar\.gz|tmp)"
)
"""Exact pre-entries-layout cache names that are safe to reclaim."""


class RegistryArtifactCacheLoopError(RuntimeError):
    """A registry artifact cache was used outside its owning event loop."""


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
    lock_users: int = 0
    retire_when_idle: bool = False


@dataclass(frozen=True, slots=True)
class RegistryArtifactEviction:
    """Outcome of atomically retiring and physically deleting one entry."""

    retired: bool
    reclaimed: bool


@dataclass(frozen=True, slots=True)
class RegistryArtifactEvictionPass:
    """Result of applying one budget policy to evictable entries."""

    total_bytes: int
    fits: bool
    exhausted_candidates: bool


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


async def _kill_and_reap_subprocess(process: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and wait until its child state is reaped."""
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    await process.wait()


async def communicate_rejoin_on_cancel(
    process: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    """Communicate without allowing cancellation to abandon child cleanup."""
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        reaper = asyncio.ensure_future(_kill_and_reap_subprocess(process))
        await drain_future_through_cancellation(reaper)
        raise

    if stdout is None or stderr is None:
        raise RuntimeError("Captured subprocess output is required")
    return stdout, stderr


def is_reusable_cache_directory(path: Path) -> bool:
    """Return whether a cache path is a real directory, never a symlink."""
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def is_reusable_cache_file(path: Path) -> bool:
    """Return whether a cache path is a regular file, never a symlink."""
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def ensure_real_directory(path: Path) -> None:
    """Create a cache directory without accepting a symlink redirect."""
    if os.path.lexists(path):
        if not is_reusable_cache_directory(path):
            raise OSError(f"Unsafe registry artifact cache directory: {path}")
        return
    path.mkdir(parents=True, exist_ok=True)
    if not is_reusable_cache_directory(path):
        raise OSError(f"Unsafe registry artifact cache directory: {path}")


def _validate_cache_root(cache_dir: Path) -> None:
    """Reject a configured cache root redirected through its final component."""
    if os.path.lexists(cache_dir) and not is_reusable_cache_directory(cache_dir):
        raise OSError(f"Unsafe registry artifact cache directory: {cache_dir}")


def _validate_cache_child_directory(path: Path) -> None:
    """Reject a fixed cache child redirected outside its real cache root."""
    _validate_cache_root(path.parent)
    if os.path.lexists(path) and not is_reusable_cache_directory(path):
        raise OSError(f"Unsafe registry artifact cache directory: {path}")


def validate_cache_entry_path(paths: RegistryArtifactPaths) -> None:
    """Reject an entry root redirected through cache-controlled symlinks."""
    entries_dir = paths.entry_dir.parent
    _validate_cache_child_directory(entries_dir)
    if os.path.lexists(paths.entry_dir) and not is_reusable_cache_directory(
        paths.entry_dir
    ):
        raise OSError(f"Unsafe registry artifact cache directory: {paths.entry_dir}")


def ensure_cache_entry_directory(paths: RegistryArtifactPaths) -> None:
    """Create a canonical entry only below real cache and entries roots."""
    entries_dir = paths.entry_dir.parent
    cache_dir = entries_dir.parent
    validate_cache_entry_path(paths)
    ensure_real_directory(cache_dir)
    ensure_real_directory(entries_dir)
    ensure_real_directory(paths.entry_dir)
    validate_cache_entry_path(paths)


def allocated_size_bound(size_bytes: int, *, allocation_unit: int) -> int:
    """Round one filesystem object up to its minimum allocated footprint."""
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if allocation_unit <= 0:
        raise ValueError("allocation_unit must be positive")
    return (
        max(1, (size_bytes + allocation_unit - 1) // allocation_unit) * allocation_unit
    )


def _filesystem_allocation_unit(path: Path) -> int:
    """Return the allocation unit for a path or nearest existing parent."""
    candidate = path
    while True:
        try:
            filesystem = os.statvfs(candidate)
            return filesystem.f_frsize or filesystem.f_bsize or 1
        except FileNotFoundError:
            parent = candidate.parent
            if parent == candidate:
                raise
            candidate = parent


def _allocated_stat_size(
    file_stat: os.stat_result,
    *,
    allocation_unit: int,
) -> int:
    """Return allocated bytes while charging at least one unit per inode."""
    if allocation_unit <= 0:
        raise ValueError("allocation_unit must be positive")
    return max(allocation_unit, file_stat.st_blocks * 512)


def _directory_footprint(
    directory: Path,
    *,
    allocation_unit: int | None = None,
    pruned_directories: Iterable[Path] = (),
    include_root: bool = True,
) -> int:
    """Return allocated bytes for unique inodes without following symlinks."""

    def raise_walk_error(error: OSError) -> None:
        raise error

    if allocation_unit is None:
        allocation_unit = _filesystem_allocation_unit(directory)

    total_bytes = 0
    seen_inodes: set[tuple[int, int]] = set()
    pruned_paths = frozenset(pruned_directories)

    def allocated_inode_size(file_stat: os.stat_result) -> int:
        inode_key = (file_stat.st_dev, file_stat.st_ino)
        if inode_key in seen_inodes:
            return 0
        seen_inodes.add(inode_key)
        return _allocated_stat_size(file_stat, allocation_unit=allocation_unit)

    try:
        root_stat = os.lstat(directory)
        if not stat.S_ISDIR(root_stat.st_mode):
            return allocated_inode_size(root_stat)

        for root, dirs, files in os.walk(directory, onerror=raise_walk_error):
            root_path = Path(root)
            if include_root or root_path != directory:
                try:
                    total_bytes += allocated_inode_size(os.lstat(root))
                except FileNotFoundError:
                    continue
            for file_name in files:
                try:
                    total_bytes += allocated_inode_size(
                        os.lstat(os.path.join(root, file_name))
                    )
                except FileNotFoundError:
                    continue
            traversed_directories: list[str] = []
            for directory_name in dirs:
                child_path = root_path / directory_name
                try:
                    directory_stat = child_path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(directory_stat.st_mode) or child_path in pruned_paths:
                    total_bytes += allocated_inode_size(directory_stat)
                    continue
                traversed_directories.append(directory_name)
            dirs[:] = traversed_directories
    except FileNotFoundError:
        return 0
    return total_bytes


def _delete_cache_path(path: Path) -> bool:
    """Best-effort delete one path without following a root symlink."""
    try:
        path_stat = path.lstat()
        if stat.S_ISDIR(path_stat.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        return True
    except OSError as e:
        logger.warning(
            "Failed to delete registry artifact cache path",
            path=str(path),
            error=str(e),
        )
        return False
    return True


async def _delete_cache_path_off_loop(path: Path) -> bool:
    """Delete one path and rejoin its worker through repeated cancellation."""
    return await run_blocking_rejoin_on_cancel(
        functools.partial(_delete_cache_path, path)
    )


async def remove_tree_rejoin_on_cancel(
    path: Path,
    *,
    defer_cleanup: Callable[[Path], None],
) -> None:
    """Remove a tree off-loop and retain failed paths for a later retry."""
    if not os.path.lexists(path):
        return
    try:
        removed = await _delete_cache_path_off_loop(path)
    except asyncio.CancelledError:
        if os.path.lexists(path):
            defer_cleanup(path)
        raise
    if not removed:
        defer_cleanup(path)


def remove_file_or_defer(
    path: Path,
    *,
    defer_cleanup: Callable[[Path], None],
) -> None:
    """Remove one staging file without masking materialization outcomes."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        defer_cleanup(path)
        logger.warning(
            "Deferred failed registry artifact file cleanup",
            path=str(path),
            error_type=type(e).__name__,
        )


def unique_work_path(root: Path, cache_key: str, *, suffix: str = "") -> Path:
    """Return a unique path beneath a validated cache work directory."""
    _validate_cache_child_directory(root)
    ensure_real_directory(root.parent)
    ensure_real_directory(root)
    _validate_cache_child_directory(root)
    unique_id = time.time_ns()
    while True:
        path = root / f"{cache_key}.{os.getpid()}.{unique_id}{suffix}"
        if not path.exists():
            return path
        unique_id += 1


def _move_entry_to_trash(entry_dir: Path, trash_dir: Path, cache_key: str) -> Path:
    """Atomically retire one cache entry and return its trash path."""
    trash_path = unique_work_path(trash_dir, cache_key)
    entry_dir.rename(trash_path)
    return trash_path


class RegistryArtifactCacheStorage:
    """Own cache state, filesystem lifecycle, and one shared budget policy."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.entries_dir = cache_dir / CACHE_ENTRIES_DIR_NAME
        self.staging_dir = cache_dir / CACHE_STAGING_DIR_NAME
        self.trash_dir = cache_dir / CACHE_TRASH_DIR_NAME
        self._runtime: dict[str, RegistryArtifactRuntimeState] = {}
        self._owner_binding_lock = threading.Lock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_thread_id: int | None = None
        self._admission_lock = asyncio.Lock()
        self._swept = False
        self._sweep_task: asyncio.Task[None] | None = None
        self._sweep_lock = asyncio.Lock()
        self._failed_startup_cleanup: set[Path] = set()
        self._budget_dirty = True

    async def ensure_swept(self) -> None:
        """Run the cancellation-safe startup sweep exactly once."""
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
        """Bind to the current loop or reject another loop/thread."""
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

    def _runtime_for(self, cache_key: str) -> RegistryArtifactRuntimeState:
        """Return stable process-local state for one cache key."""
        if runtime := self._runtime.get(cache_key):
            return runtime
        runtime = RegistryArtifactRuntimeState()
        self._runtime[cache_key] = runtime
        return runtime

    @asynccontextmanager
    async def _runtime_lock(
        self,
        cache_key: str,
        *,
        wait: bool = True,
    ) -> AsyncGenerator[RegistryArtifactRuntimeState | None]:
        """Track holders and waiters while serializing one cache key.

        Tracking begins before waiting on the lock, so an eviction cannot
        retire the state and let a later caller create a second lock while a
        waiter still references the first one.
        """
        runtime = self._runtime_for(cache_key)
        runtime.lock_users += 1
        try:
            if not wait and runtime.lock.locked():
                yield None
                return
            async with runtime.lock:
                yield runtime
        finally:
            runtime.lock_users -= 1
            self._retire_runtime_if_idle(cache_key, runtime)

    def _request_runtime_retirement(
        self,
        cache_key: str,
        runtime: RegistryArtifactRuntimeState | None = None,
    ) -> None:
        """Retire evicted key state after every holder and waiter is gone."""
        if runtime is None:
            runtime = self._runtime.get(cache_key)
        if runtime is None:
            return
        runtime.retire_when_idle = True
        self._retire_runtime_if_idle(cache_key, runtime)

    def _retire_runtime_if_idle(
        self,
        cache_key: str,
        runtime: RegistryArtifactRuntimeState,
    ) -> None:
        """Drop one retired state only when no task can still use its lock."""
        if (
            runtime.retire_when_idle
            and runtime.refcount == 0
            and runtime.lock_users == 0
            and self._runtime.get(cache_key) is runtime
        ):
            del self._runtime[cache_key]

    def _request_runtime_retirement_if_entry_missing(self, cache_key: str) -> None:
        """Retire failed-admission state once its empty entry shell is gone."""
        if not os.path.lexists(self._paths_for(cache_key).entry_dir):
            self._request_runtime_retirement(cache_key)

    def _context_for(
        self,
        cache_key: str,
        *,
        admission: RegistryArtifactAdmission | None = None,
    ) -> RegistryArtifactMaterializationContext:
        """Return materialization state for one cache key."""
        return RegistryArtifactMaterializationContext(
            cache_key=cache_key,
            staging_dir=self.staging_dir,
            paths=self._paths_for(cache_key),
            defer_cleanup=self._failed_startup_cleanup.add,
            admission=admission,
        )

    def _admission_for(self, cache_key: str) -> RegistryArtifactAdmission | None:
        """Return byte-bound admission controls for one cold cache key."""
        max_bytes = config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES
        if max_bytes <= 0:
            return None
        allocation_unit = _filesystem_allocation_unit(self.cache_dir)

        async def ensure_capacity(additional_bytes: int) -> None:
            await self._ensure_cache_capacity(
                additional_bytes=allocated_size_bound(
                    additional_bytes,
                    allocation_unit=allocation_unit,
                ),
                protected_key=cache_key,
                max_bytes=max_bytes,
            )

        return RegistryArtifactAdmission(
            max_bytes=max_bytes,
            allocation_unit=allocation_unit,
            ensure_capacity=ensure_capacity,
        )

    def _base_pythonpath_dir(self) -> Path:
        """Return the base PYTHONPATH directory for an artifact-free action."""
        base_dir = self.cache_dir / BASE_PYTHONPATH_DIR_NAME
        _validate_cache_root(self.cache_dir)
        ensure_real_directory(self.cache_dir)
        _validate_cache_child_directory(base_dir)
        ensure_real_directory(base_dir)
        return base_dir

    def _acquire_lease(self, cache_key: str) -> None:
        """Pin a cache entry against eviction and mark it recently used."""
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
        """Best-effort unmount an idle entry while retaining its image."""
        async with self._runtime_lock(cache_key, wait=False) as runtime:
            if runtime is None:
                return
            if self._refcount(cache_key) > 0:
                return
            mount_dir = self._paths_for(cache_key).squashfs_mount_dir
            try:
                mounted = registry_artifact_mounts.is_mount(mount_dir)
            except OSError as e:
                logger.warning(
                    "Failed to inspect registry artifact mount state",
                    cache_key=cache_key,
                    mount_dir=str(mount_dir),
                    error=str(e),
                )
                return
            if not mounted:
                return
            if not await self._unmount(mount_dir):
                logger.warning(
                    "Failed to unmount registry artifact for loop-device reclamation",
                    cache_key=cache_key,
                    mount_dir=str(mount_dir),
                )
                return
            logger.info(
                "Unmounted idle registry artifact",
                cache_key=cache_key,
                mount_dir=str(mount_dir),
            )

    def _refcount(self, cache_key: str) -> int:
        """Return the number of live leases on an entry."""
        runtime = self._runtime.get(cache_key)
        return 0 if runtime is None else runtime.refcount

    def _touch_entry(self, cache_key: str) -> None:
        """Best-effort refresh of the entry-root mtime for restart-safe LRU."""
        try:
            os.utime(self._paths_for(cache_key).entry_dir)
        except OSError:
            logger.debug(
                "Could not refresh registry artifact entry mtime",
                cache_key=cache_key,
            )

    def _paths_for(self, cache_key: str) -> RegistryArtifactPaths:
        """Return local paths for a registry artifact key."""
        entry_dir = self.entries_dir / cache_key
        return RegistryArtifactPaths(
            entry_dir=entry_dir,
            squashfs_image_path=entry_dir / "image.squashfs",
            squashfs_mount_dir=entry_dir / "mount",
            squashfs_extract_dir=entry_dir / "extracted",
            tarball_target_dir=entry_dir / "tarball",
        )

    async def _converge_cache_budget(self) -> None:
        """Bring an idle cache back under budget after a lease release."""
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
                self._budget_dirty = True
                raise
            if not within_budget:
                self._budget_dirty = True
                break

    async def _enforce_cache_budget(
        self,
        *,
        protected_key: str | None = None,
    ) -> bool:
        """Evict idle LRU entries until the measured cache fits."""
        async with self._admission_lock:
            return await self._enforce_cache_budget_locked(protected_key=protected_key)

    async def _reclaim_pending_work(self) -> tuple[bool, bool]:
        """Retry pending trash and deferred cleanup off the event loop."""
        return await rejoin_future_on_cancel(
            asyncio.gather(
                asyncio.to_thread(self._clear_work_dir, self.trash_dir),
                asyncio.to_thread(self._retry_failed_startup_cleanup),
            )
        )

    async def _enforce_cache_budget_locked(
        self,
        *,
        protected_key: str | None,
    ) -> bool:
        """Enforce entry and byte limits while admission is serialized."""
        trash_clean, startup_clean = await self._reclaim_pending_work()
        if not (trash_clean and startup_clean):
            return False

        budget = RegistryArtifactCacheBudget(
            max_entries=config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES,
            max_bytes=config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES,
        )
        if budget.max_entries <= 0 and budget.max_bytes <= 0:
            return True

        snapshot = await asyncio.to_thread(self._scan_cache_snapshot)
        eviction_pass = await self._evict_until_fits(
            snapshot.entries,
            total_bytes=snapshot.total_bytes,
            excluded=set() if protected_key is None else {protected_key},
            budget=budget,
        )
        if not eviction_pass.fits and eviction_pass.exhausted_candidates:
            logger.warning(
                "Registry artifact cache is over budget but every entry is in use",
                cache_dir=str(self.cache_dir),
                entries=len(snapshot.entries),
                max_entries=budget.max_entries,
                total_bytes=eviction_pass.total_bytes,
                max_bytes=budget.max_bytes,
            )
        return eviction_pass.fits

    async def _ensure_cache_capacity(
        self,
        *,
        additional_bytes: int,
        protected_key: str,
        max_bytes: int,
    ) -> None:
        """Reserve peak bytes for one serialized cold writer."""
        if additional_bytes < 0:
            raise ValueError("additional_bytes must be non-negative")

        def capacity_error(current_bytes: int) -> RegistryArtifactCacheCapacityError:
            return RegistryArtifactCacheCapacityError(
                current_bytes=current_bytes,
                additional_bytes=additional_bytes,
                max_bytes=max_bytes,
            )

        trash_clean, startup_clean = await self._reclaim_pending_work()
        snapshot = await asyncio.to_thread(self._scan_cache_snapshot)
        entries = snapshot.entries
        total_bytes = snapshot.total_bytes
        non_evictable_bytes = (
            snapshot.structural_bytes
            + snapshot.staging_bytes
            + snapshot.trash_bytes
            + sum(
                entry.size_bytes
                for entry in entries.values()
                if entry.cache_key == protected_key
                or self._refcount(entry.cache_key) > 0
                or (
                    (runtime := self._runtime.get(entry.cache_key)) is not None
                    and runtime.lock.locked()
                )
            )
        )
        if non_evictable_bytes + additional_bytes > max_bytes:
            raise capacity_error(non_evictable_bytes)
        if (
            not (trash_clean and startup_clean)
            and total_bytes + additional_bytes > max_bytes
        ):
            raise capacity_error(total_bytes)
        eviction_pass = await self._evict_until_fits(
            entries,
            total_bytes=total_bytes,
            excluded={protected_key},
            budget=RegistryArtifactCacheBudget(
                max_entries=0,
                max_bytes=max_bytes,
                additional_bytes=additional_bytes,
            ),
        )
        if not eviction_pass.fits:
            raise capacity_error(eviction_pass.total_bytes)

    async def _evict_until_fits(
        self,
        entries: dict[str, RegistryArtifactCacheEntry],
        *,
        total_bytes: int,
        excluded: set[str],
        budget: RegistryArtifactCacheBudget,
    ) -> RegistryArtifactEvictionPass:
        """Apply the shared LRU policy until a measured cache fits."""
        skipped = set(excluded)
        while not budget.fits(entry_count=len(entries), total_bytes=total_bytes):
            plan = plan_registry_artifact_evictions(
                entries,
                total_bytes=total_bytes,
                budget=budget,
                excluded=skipped
                | {
                    entry.cache_key
                    for entry in entries.values()
                    if self._refcount(entry.cache_key) > 0
                },
                effective_last_used={
                    entry.cache_key: max(
                        entry.last_used,
                        runtime.last_used,
                    )
                    for entry in entries.values()
                    if (runtime := self._runtime.get(entry.cache_key)) is not None
                },
            )
            if not plan.candidates:
                return RegistryArtifactEvictionPass(
                    total_bytes=total_bytes,
                    fits=False,
                    exhausted_candidates=True,
                )
            candidate = plan.candidates[0]
            eviction = await self._evict_entry(candidate.cache_key)
            if not eviction.retired:
                skipped.add(candidate.cache_key)
                continue
            del entries[candidate.cache_key]
            if not eviction.reclaimed:
                return RegistryArtifactEvictionPass(
                    total_bytes=total_bytes,
                    fits=False,
                    exhausted_candidates=False,
                )
            total_bytes -= candidate.size_bytes

        return RegistryArtifactEvictionPass(
            total_bytes=total_bytes,
            fits=True,
            exhausted_candidates=False,
        )

    async def _evict_entry(self, cache_key: str) -> RegistryArtifactEviction:
        """Atomically retire and physically delete one idle cache entry."""
        async with self._runtime_lock(cache_key, wait=False) as runtime:
            if runtime is None:
                logger.debug(
                    "Skipping eviction of busy registry artifact",
                    cache_key=cache_key,
                )
                return RegistryArtifactEviction(retired=False, reclaimed=False)

            if self._refcount(cache_key) > 0:
                return RegistryArtifactEviction(retired=False, reclaimed=False)
            paths = self._paths_for(cache_key)
            validate_cache_entry_path(paths)
            if not paths.entry_dir.exists():
                self._request_runtime_retirement(cache_key, runtime)
                return RegistryArtifactEviction(retired=True, reclaimed=True)
            if registry_artifact_mounts.is_mount(
                paths.squashfs_mount_dir
            ) and not await self._unmount(paths.squashfs_mount_dir):
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
            self._request_runtime_retirement(cache_key, runtime)

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
        """Unmount a SquashFS artifact directory, releasing its loop device."""
        umount = shutil.which("umount")
        if umount is None:
            return False
        proc = await asyncio.create_subprocess_exec(
            umount,
            str(mount_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_rejoin_on_cancel(proc)
        if proc.returncode == 0 or not registry_artifact_mounts.is_mount(mount_dir):
            return True
        logger.warning(
            "umount command failed",
            mount_dir=str(mount_dir),
            output=(stderr or stdout).decode(errors="replace").strip(),
        )
        return False

    def _cache_structural_footprint(self, *, allocation_unit: int) -> int:
        """Measure cache roots and non-entry data exactly once."""
        return _directory_footprint(
            self.cache_dir,
            allocation_unit=allocation_unit,
            pruned_directories=(
                self.entries_dir,
                self.staging_dir,
                self.trash_dir,
            ),
        )

    def _prepare_budget_roots(self) -> None:
        """Create fixed roots before measuring or retiring an entry."""
        ensure_real_directory(self.cache_dir)
        for root in (self.entries_dir, self.staging_dir, self.trash_dir):
            _validate_cache_child_directory(root)
            ensure_real_directory(root)
            _validate_cache_child_directory(root)

    def _scan_cache_snapshot(self) -> RegistryArtifactCacheSnapshot:
        """Measure entries, work directories, and structure together."""
        self._prepare_budget_roots()
        allocation_unit = _filesystem_allocation_unit(self.cache_dir)
        return RegistryArtifactCacheSnapshot(
            entries=self._scan_cache_entries(allocation_unit=allocation_unit),
            structural_bytes=self._cache_structural_footprint(
                allocation_unit=allocation_unit
            ),
            staging_bytes=_directory_footprint(
                self.staging_dir,
                allocation_unit=allocation_unit,
                include_root=False,
            ),
            trash_bytes=_directory_footprint(
                self.trash_dir,
                allocation_unit=allocation_unit,
                include_root=False,
            ),
        )

    def _scan_cache_entries(
        self,
        *,
        allocation_unit: int | None = None,
    ) -> dict[str, RegistryArtifactCacheEntry]:
        """Measure every registry artifact entry currently on disk."""
        if allocation_unit is None:
            allocation_unit = _filesystem_allocation_unit(self.cache_dir)
        return {
            cache_key: self._measure_entry(
                cache_key,
                allocation_unit=allocation_unit,
            )
            for cache_key in self._discover_cache_keys()
        }

    def _discover_cache_keys(self) -> set[str]:
        """Return cache keys represented by real atomic entry directories."""
        _validate_cache_child_directory(self.entries_dir)
        try:
            entries = list(os.scandir(self.entries_dir))
        except FileNotFoundError:
            return set()
        return {
            entry.name
            for entry in entries
            if entry.name and entry.is_dir(follow_symlinks=False)
        }

    def _measure_entry(
        self,
        cache_key: str,
        *,
        allocation_unit: int | None = None,
    ) -> RegistryArtifactCacheEntry:
        """Measure all entry-owned inodes, pruning active mount contents."""
        paths = self._paths_for(cache_key)
        validate_cache_entry_path(paths)
        if allocation_unit is None:
            allocation_unit = _filesystem_allocation_unit(self.cache_dir)
        try:
            mount_is_active = registry_artifact_mounts.is_mount(
                paths.squashfs_mount_dir
            )
        except FileNotFoundError:
            mount_is_active = False
        pruned_directories = (paths.squashfs_mount_dir,) if mount_is_active else ()
        size_bytes = _directory_footprint(
            paths.entry_dir,
            allocation_unit=allocation_unit,
            pruned_directories=pruned_directories,
        )
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
        """Reclaim orphaned work, legacy layout, and over-budget entries."""
        _validate_cache_root(self.cache_dir)
        if not self.cache_dir.is_dir():
            self._budget_dirty = False
            return
        try:
            staging_clean = self._clear_work_dir(
                self.staging_dir,
                remember_failures=True,
            )
            trash_clean = self._clear_work_dir(self.trash_dir)
            legacy_clean = self._clear_legacy_cache_layout()
            cleanup_complete = staging_clean and trash_clean and legacy_clean
            within_budget = cleanup_complete and self._trim_startup_cache()
            self._budget_dirty = not (cleanup_complete and within_budget)
        except OSError as e:
            logger.warning(
                "Failed to sweep registry artifact cache",
                cache_dir=str(self.cache_dir),
                error=str(e),
            )
            raise

    def _clear_legacy_cache_layout(self) -> bool:
        """Reclaim exact top-level artifacts from the pre-entries layout."""
        _validate_cache_root(self.cache_dir)
        try:
            paths = list(self.cache_dir.iterdir())
        except FileNotFoundError:
            return True
        deleted = True
        for path in paths:
            if _LEGACY_CACHE_PATH_PATTERN.fullmatch(path.name) is None:
                continue
            try:
                mounted = is_reusable_cache_directory(
                    path
                ) and registry_artifact_mounts.is_mount(path)
            except OSError:
                mounted = True
            if mounted or not _delete_cache_path(path):
                deleted = False
                self._failed_startup_cleanup.add(path)
                continue
            self._failed_startup_cleanup.discard(path)
            logger.info("Removed legacy registry artifact cache path", path=str(path))
        return deleted

    def _clear_work_dir(
        self,
        work_dir: Path,
        *,
        remember_failures: bool = False,
    ) -> bool:
        """Best-effort remove every child of a staging or trash directory."""
        _validate_cache_child_directory(work_dir)
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
                logger.info("Removed registry artifact work path", path=str(path))
            else:
                deleted = False
                if remember_failures:
                    self._failed_startup_cleanup.add(path)
        return deleted

    def _retry_failed_startup_cleanup(self) -> bool:
        """Retry exact deferred paths without sweeping live staging work."""
        for path in tuple(self._failed_startup_cleanup):
            if _delete_cache_path(path):
                self._failed_startup_cleanup.discard(path)
        return not self._failed_startup_cleanup

    def _trim_startup_cache(self) -> bool:
        """Apply the shared measured budget during startup."""
        budget = RegistryArtifactCacheBudget(
            max_entries=config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES,
            max_bytes=config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES,
        )
        if budget.max_entries <= 0 and budget.max_bytes <= 0:
            return True

        snapshot = self._scan_cache_snapshot()
        entries = snapshot.entries
        total_bytes = snapshot.total_bytes
        mounted_keys = {
            entry.cache_key
            for entry in entries.values()
            if registry_artifact_mounts.is_mount(
                self._paths_for(entry.cache_key).squashfs_mount_dir
            )
        }
        plan = plan_registry_artifact_evictions(
            entries,
            total_bytes=total_bytes,
            budget=budget,
            excluded=mounted_keys,
        )
        for entry in plan.candidates:
            if budget.fits(entry_count=len(entries), total_bytes=total_bytes):
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
        return budget.fits(entry_count=len(entries), total_bytes=total_bytes)
