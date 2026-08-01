"""Process-local state and lease bookkeeping for registry artifact caches."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from tracecat.executor.registry_artifact_materialization import (
    RegistryArtifactAdmission,
    RegistryArtifactMaterializationContext,
    RegistryArtifactPaths,
)
from tracecat.logger import logger

BASE_PYTHONPATH_DIR_NAME = "base"
"""Cache subdirectory used as the PYTHONPATH entry when no artifact is requested."""

CACHE_ENTRIES_DIR_NAME = "entries"
"""Directory containing one atomic subdirectory per cache key."""

CACHE_STAGING_DIR_NAME = "staging"
"""Directory containing in-progress materialization scratch."""

CACHE_TRASH_DIR_NAME = "trash"
"""Directory containing atomically retired entries pending physical deletion."""


class RegistryArtifactCacheLoopError(RuntimeError):
    """A registry artifact cache was used outside its owning event loop."""


@dataclass(slots=True)
class RegistryArtifactRuntimeState:
    """Process-local synchronization and lease state for one cache key."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refcount: int = 0
    last_used: float = 0.0


class _RegistryArtifactCacheState:
    """Owns cache paths, event-loop affinity, and per-key lease state."""

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
        # Cold materializations and budget passes share this outer lock. It
        # keeps byte reservations stable while downloads and extraction write.
        self._admission_lock = asyncio.Lock()
        self._budget_lock = asyncio.Lock()
        # Guard the off-loop startup sweep independently from cache operations.
        self._swept: bool = False
        self._sweep_task: asyncio.Task[None] | None = None
        self._sweep_lock = asyncio.Lock()
        # Startup is the only time the whole staging directory is swept. Exact
        # startup or runtime paths that could not be removed are safe to retry.
        self._deferred_staging_cleanup: set[Path] = set()
        # Final-release unmount failures are retried by later lease cleanup so
        # transient errors cannot accumulate idle loop devices indefinitely.
        self._failed_unmounts: set[str] = set()
        # Whether the on-disk cache may exceed its budget. Set when a new entry
        # is materialized and cleared once enforcement measures a cache that
        # fits, so steady-state cache hits never pay for a disk scan.
        self._budget_dirty = True

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

    def _runtime_for(self, cache_key: str) -> RegistryArtifactRuntimeState:
        """Return the process-local state for one cache key."""
        if runtime := self._runtime.get(cache_key):
            return runtime
        runtime = RegistryArtifactRuntimeState()
        self._runtime[cache_key] = runtime
        return runtime

    def _context_for(
        self,
        cache_key: str,
        *,
        admission: RegistryArtifactAdmission | None = None,
    ) -> RegistryArtifactMaterializationContext:
        """Return a materialization context for a registry artifact key."""
        return RegistryArtifactMaterializationContext(
            cache_key=cache_key,
            staging_dir=self.staging_dir,
            paths=self._paths_for(cache_key),
            defer_cleanup=self._deferred_staging_cleanup.add,
            admission=admission,
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
        became_idle = runtime.refcount == 0
        if became_idle:
            self._touch_entry(cache_key)
        return became_idle

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
