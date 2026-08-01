"""Registry artifact startup recovery tests."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    _delete_cache_path,
)

from .registry_artifact_test_helpers import (
    MAX_BYTES_CONFIG,
    MAX_ENTRIES_CONFIG,
    write_image_entry,
    write_tarball_entry,
)


class TestRegistryArtifactCacheStartupSweep:
    """Tests for the startup sweep that reclaims state from a dead process."""

    @pytest.mark.anyio
    async def test_sweep_uses_entry_root_mtime_for_restart_safe_lru(
        self, temp_cache_dir
    ):
        """Startup trimming preserves a touched older tarball-only entry."""
        previous_cache = RegistryArtifactCache(temp_cache_dir)
        old = write_tarball_entry(temp_cache_dir, "old")
        previous_cache._touch_entry("old")

        old_mtime = previous_cache._paths_for("old").entry_dir.stat().st_mtime
        new = write_tarball_entry(temp_cache_dir, "new")
        new_entry_dir = previous_cache._paths_for("new").entry_dir
        os.utime(new_entry_dir, (old_mtime - 1, old_mtime - 1))

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            cache = RegistryArtifactCache(temp_cache_dir)
            await cache.ensure_swept()

        assert old.is_dir()
        assert not new.exists()

    @pytest.mark.anyio
    async def test_sweep_tolerates_missing_cache_dir(self, temp_cache_dir):
        """A cache directory that does not exist yet is a no-op."""
        cache_dir = temp_cache_dir / "missing"

        cache = RegistryArtifactCache(cache_dir)
        await cache.ensure_swept()

        assert cache.cache_dir == cache_dir
        assert not cache_dir.exists()

    @pytest.mark.anyio
    async def test_sweep_removes_orphaned_work(self, temp_cache_dir):
        """Interrupted work is reclaimed without touching unrelated paths."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned = cache.staging_dir / "abc123.999999.4321.squashfs"
        orphaned.parent.mkdir()
        orphaned.write_bytes(b"partial")
        orphaned_dir = cache.trash_dir / "abc123.999999.4321"
        orphaned_dir.mkdir(parents=True)
        unrelated = temp_cache_dir / "unrelated"
        unrelated.mkdir()
        unrelated_file = unrelated / "keep.txt"
        unrelated_file.write_text("keep")
        entry_dir = write_tarball_entry(temp_cache_dir, "abc123")

        await cache.ensure_swept()

        assert not orphaned.exists()
        assert not orphaned_dir.exists()
        assert unrelated_file.read_text() == "keep"
        assert entry_dir.is_dir()

    @pytest.mark.anyio
    async def test_sweep_keeps_mounted_dirs(self, temp_cache_dir):
        """A live mountpoint belongs to a running process and must survive."""
        cache = RegistryArtifactCache(temp_cache_dir)
        paths = cache._paths_for("mounted")
        paths.entry_dir.mkdir(parents=True)
        mount_dir = paths.squashfs_mount_dir
        mount_dir.mkdir()
        idle_dir = write_tarball_entry(temp_cache_dir, "idle")

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch.object(Path, "is_mount", lambda self: self == mount_dir),
        ):
            await cache.ensure_swept()

        assert mount_dir.is_dir()
        assert not idle_dir.exists()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_ensure_swept_runs_once(self, temp_cache_dir):
        """Repeated startup-sweep requests invoke the sweep once."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch.object(
            cache,
            "_sweep_startup_state",
            wraps=cache._sweep_startup_state,
        ) as sweep:
            await cache.ensure_swept()
            await cache.ensure_swept()

        assert sweep.call_count == 1

    @pytest.mark.anyio
    async def test_concurrent_ensure_swept_runs_once(self, temp_cache_dir):
        """Concurrent startup-sweep requests invoke the sweep once."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch.object(
            cache,
            "_sweep_startup_state",
            wraps=cache._sweep_startup_state,
        ) as sweep:
            await asyncio.gather(*(cache.ensure_swept() for _ in range(4)))

        assert sweep.call_count == 1

    @pytest.mark.anyio
    async def test_cancelled_waiter_joins_same_startup_sweep(self, temp_cache_dir):
        """A cancelled waiter cannot start a second concurrent startup sweep."""
        cache = RegistryArtifactCache(temp_cache_dir)
        sweep_started = threading.Event()
        sweep_release = threading.Event()
        invocation_count = 0

        def blocking_sweep() -> None:
            nonlocal invocation_count
            invocation_count += 1
            sweep_started.set()
            sweep_release.wait()

        with patch.object(
            cache,
            "_sweep_startup_state",
            side_effect=blocking_sweep,
        ):
            first_waiter = asyncio.create_task(cache.ensure_swept())
            assert await asyncio.to_thread(sweep_started.wait, 1)
            first_waiter.cancel()

            with pytest.raises(asyncio.CancelledError):
                await first_waiter

            second_waiter = asyncio.create_task(cache.ensure_swept())
            await asyncio.sleep(0)
            sweep_release.set()
            await second_waiter

        assert invocation_count == 1
        assert cache._swept is True

    @pytest.mark.anyio
    async def test_lease_triggers_startup_sweep(self, temp_cache_dir):
        """Lease admission reclaims startup scratch before yielding paths."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned_dir = cache.staging_dir / "abc123.999999.4321"
        orphaned_dir.mkdir(parents=True)

        async with cache.lease(None):
            assert not orphaned_dir.exists()

    @pytest.mark.anyio
    async def test_failed_ensure_swept_retries(self, temp_cache_dir):
        """A failed startup sweep is retried by the next caller."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned_dir = cache.staging_dir / "abc123.999999.4321"
        orphaned_dir.mkdir(parents=True)

        with (
            patch.object(
                cache,
                "_clear_work_dir",
                side_effect=OSError("simulated sweep failure"),
            ),
            pytest.raises(OSError),
        ):
            await cache.ensure_swept()

        assert orphaned_dir.is_dir()

        await cache.ensure_swept()

        assert not orphaned_dir.exists()

    @pytest.mark.parametrize("work_dir_name", ["staging", "trash"])
    def test_work_directory_inspection_errors_propagate(
        self,
        temp_cache_dir,
        work_dir_name: str,
    ):
        """Unreadable work directories must trigger a later cleanup retry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        work_dir = getattr(cache, f"{work_dir_name}_dir")
        work_dir.mkdir()

        with (
            patch.object(Path, "iterdir", side_effect=PermissionError("denied")),
            pytest.raises(PermissionError, match="denied"),
        ):
            cache._clear_work_dir(work_dir)

    @pytest.mark.anyio
    async def test_active_entry_scan_errors_propagate(self, temp_cache_dir):
        """Unreadable active entries cannot be treated as an empty cache."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(
                "tracecat.executor.registry_artifact_storage.os.scandir",
                side_effect=PermissionError("denied"),
            ),
            pytest.raises(PermissionError, match="denied"),
        ):
            await cache._enforce_cache_budget()

    @pytest.mark.anyio
    async def test_failed_trash_cleanup_skips_startup_trimming(self, temp_cache_dir):
        """A failed orphan deletion must not cascade into active retirements."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphan = cache.trash_dir / "orphan"
        orphan.mkdir(parents=True)
        oldest = write_image_entry(
            temp_cache_dir,
            "oldest",
            size=16,
            mtime=100.0,
        )
        newest = write_image_entry(
            temp_cache_dir,
            "newest",
            size=16,
            mtime=200.0,
        )
        real_delete = _delete_cache_path

        def fail_orphan(path: Path) -> bool:
            if path == orphan:
                return False
            return real_delete(path)

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                side_effect=fail_orphan,
            ),
        ):
            await cache.ensure_swept()

        assert orphan.is_dir()
        assert oldest.is_file()
        assert newest.is_file()
        assert cache._budget_dirty is True

    @pytest.mark.anyio
    async def test_failed_startup_cleanup_retries_exact_path(self, temp_cache_dir):
        """Failed startup scratch cleanup retries without sweeping live staging."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned = cache.staging_dir / "orphaned.123.456.tmp"
        orphaned.parent.mkdir(parents=True)
        orphaned.write_bytes(b"partial")
        real_delete = _delete_cache_path
        failed_once = False

        def fail_once(path: Path) -> bool:
            nonlocal failed_once
            if path == orphaned and not failed_once:
                failed_once = True
                return False
            return real_delete(path)

        with patch(
            "tracecat.executor.registry_artifact_storage._delete_cache_path",
            side_effect=fail_once,
        ):
            await cache.ensure_swept()
            assert orphaned.is_file()
            assert cache._deferred_staging_cleanup == {orphaned}
            assert cache._budget_dirty is True

            assert await cache._enforce_cache_budget() is True

        assert not orphaned.exists()
        assert cache._deferred_staging_cleanup == set()

    @pytest.mark.anyio
    async def test_failed_startup_retirement_stays_dirty_and_retries(
        self, temp_cache_dir
    ):
        """A startup rename failure preserves entries for later enforcement."""
        oldest = write_image_entry(
            temp_cache_dir,
            "oldest",
            size=16,
            mtime=100.0,
        )
        newest = write_image_entry(
            temp_cache_dir,
            "newest",
            size=16,
            mtime=200.0,
        )
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._move_entry_to_trash",
                side_effect=OSError("rename failed"),
            ),
        ):
            await cache.ensure_swept()

        assert oldest.is_file()
        assert newest.is_file()
        assert cache._budget_dirty is True

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            await cache._converge_cache_budget()

        assert not oldest.exists()
        assert newest.is_file()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_failed_startup_physical_delete_retries_exact_path(
        self, temp_cache_dir
    ):
        """Startup stops retiring entries when bytes were not reclaimed."""
        oldest = write_image_entry(
            temp_cache_dir,
            "oldest",
            size=16,
            mtime=100.0,
        )
        older = write_image_entry(
            temp_cache_dir,
            "older",
            size=16,
            mtime=200.0,
        )
        newest = write_image_entry(
            temp_cache_dir,
            "newest",
            size=16,
            mtime=300.0,
        )
        cache = RegistryArtifactCache(temp_cache_dir)
        real_delete = _delete_cache_path
        failed_once = False

        def fail_once(path: Path) -> bool:
            nonlocal failed_once
            if path.parent == cache.trash_dir and not failed_once:
                failed_once = True
                return False
            return real_delete(path)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 16),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                side_effect=fail_once,
            ),
        ):
            await cache.ensure_swept()
            assert not oldest.exists()
            assert older.is_file()
            assert newest.is_file()
            assert len(tuple(cache.trash_dir.iterdir())) == 1
            assert cache._budget_dirty is True

            await cache._converge_cache_budget()

        assert not older.exists()
        assert newest.is_file()
        assert not any(cache.trash_dir.iterdir())
        assert cache._budget_dirty is False
