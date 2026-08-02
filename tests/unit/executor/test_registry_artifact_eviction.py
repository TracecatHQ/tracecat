"""Registry artifact retirement and unmount tests."""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    RegistryArtifactEviction,
    TarballArtifact,
    _delete_cache_path,
    compute_registry_artifact_cache_key,
)

from .registry_artifact_test_helpers import (
    MAX_BYTES_CONFIG,
    MAX_ENTRIES_CONFIG,
    BlockingSubprocess,
    tarball_payload,
    write_image_entry,
    write_tarball_entry,
)


class TestRegistryArtifactCacheEviction:
    """Retire idle cache entries without disrupting live leases."""

    @pytest.mark.anyio
    async def test_eviction_surfaces_unknown_mount_state(self, temp_cache_dir):
        """Inspection failures cannot be mistaken for an unmounted entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        paths = cache._paths_for("unknown-mount-state")
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        real_lstat = Path.lstat

        def fail_mount_lstat(path: Path):
            if path == paths.squashfs_mount_dir:
                raise PermissionError("mount inspection denied")
            return real_lstat(path)

        with patch.object(Path, "lstat", fail_mount_lstat):
            with pytest.raises(PermissionError, match="mount inspection denied"):
                await cache._evict_entry("unknown-mount-state")

        assert paths.entry_dir.is_dir()
        assert paths.squashfs_image_path.is_file()
        assert paths.squashfs_mount_dir.is_dir()

    @pytest.mark.anyio
    async def test_eviction_unmounts_before_deleting_the_image(self, temp_cache_dir):
        """Unlinking a mounted image would strand an open-file zombie."""
        cache = RegistryArtifactCache(temp_cache_dir)
        paths = cache._paths_for("mounted")
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}
        image_present_at_umount: list[bool] = []

        process = AsyncMock()
        process.pid = 999_999_999
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        async def mock_umount(*args, **kwargs):
            assert kwargs["start_new_session"] is True
            image_present_at_umount.append(paths.squashfs_image_path.exists())
            mounted.discard(paths.squashfs_mount_dir)
            return process

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in mounted,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ) as create_subprocess_exec,
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
        ):
            evicted = await cache._evict_entry("mounted")

        assert evicted == RegistryArtifactEviction(retired=True, reclaimed=True)
        assert image_present_at_umount == [True]
        assert not paths.squashfs_image_path.exists()
        assert not paths.squashfs_mount_dir.exists()
        create_subprocess_exec.assert_called_once()
        assert create_subprocess_exec.call_args.args == (
            "/sbin/umount",
            str(paths.squashfs_mount_dir),
        )
        kill_group.assert_called_once_with(process.pid, signal.SIGKILL)

    @pytest.mark.anyio
    async def test_repeatedly_cancelled_unmount_reaps_before_releasing_key_lock(
        self, temp_cache_dir
    ):
        """Repeated cancellation leaves a consistent entry for next admission."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/cancelled-unmount.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        (paths.squashfs_mount_dir / "module.py").write_text("VALUE = 1")
        mounted = {paths.squashfs_mount_dir}
        blocked_process = BlockingSubprocess(block_wait=True)
        released_process = AsyncMock()
        released_process.communicate.return_value = (b"", b"")
        released_process.returncode = 0
        unmount_attempts = 0

        async def mock_umount(*args, **kwargs):
            nonlocal unmount_attempts
            assert kwargs["start_new_session"] is True
            unmount_attempts += 1
            if unmount_attempts == 1:
                return blocked_process
            mounted.discard(paths.squashfs_mount_dir)
            return released_process

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in mounted,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ),
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            await blocked_process.communicate_started.wait()
            eviction.cancel()
            await blocked_process.wait_started.wait()

            eviction.cancel()
            done, _ = await asyncio.wait({eviction}, timeout=0.05)
            assert not done
            blocked_process.release_wait.set()
            with pytest.raises(asyncio.CancelledError):
                await eviction

            assert blocked_process.cleanup_calls == ["kill", "wait"]
            kill_group.assert_called_once_with(
                blocked_process.pid,
                signal.SIGKILL,
            )
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [paths.squashfs_mount_dir]
                assert registry_paths[0].is_dir()
                assert (registry_paths[0] / "module.py").read_text() == "VALUE = 1"

        assert paths.squashfs_image_path.is_file()
        assert paths.squashfs_mount_dir.is_dir()
        assert paths.squashfs_mount_dir not in mounted

    @pytest.mark.anyio
    async def test_cancelled_background_deletion_leaves_a_clean_miss(
        self, temp_cache_dir
    ):
        """Cancellation cannot expose live paths that deletion still owns."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/cancelled-eviction.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        original_target = write_tarball_entry(temp_cache_dir, cache_key)
        delete_started = threading.Event()
        finish_delete = threading.Event()
        delete_finished = threading.Event()
        doomed: list[Path] = []

        def blocked_delete(path: Path) -> bool:
            doomed.append(path)
            delete_started.set()
            finish_delete.wait(timeout=5)
            deleted = _delete_cache_path(path)
            delete_finished.set()
            return deleted

        async def mock_download(self, ctx, path):
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                side_effect=blocked_delete,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization._tarball_extracted_size",
                return_value=9,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            assert await asyncio.to_thread(delete_started.wait, 1)
            eviction.cancel()
            try:
                await asyncio.sleep(0)
                eviction.cancel()
                await asyncio.sleep(0)
                assert not eviction.done()
                assert not original_target.exists()
                assert (doomed[0] / "tarball").is_dir()
                assert cache._discover_cache_keys() == set()
            finally:
                finish_delete.set()

            with pytest.raises(asyncio.CancelledError):
                await eviction
            assert await asyncio.to_thread(delete_finished.wait, 1)
            assert not doomed[0].exists()

            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [original_target]
                assert (original_target / "module.py").read_text() == "VALUE = 2"

        assert original_target.is_dir()

    @pytest.mark.anyio
    async def test_doomed_eviction_names_are_startup_scratch(self, temp_cache_dir):
        """Every renamed entry root is invisible and reclaimed on startup."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "squashfs-doomed"
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        paths.squashfs_extract_dir.mkdir()
        paths.tarball_target_dir.mkdir()

        with patch(
            "tracecat.executor.registry_artifact_storage._delete_cache_path",
            return_value=True,
        ) as delete_cache_path:
            assert await cache._evict_entry(cache_key) == RegistryArtifactEviction(
                retired=True, reclaimed=True
            )

        delete_cache_path.assert_called_once()
        trash_path = delete_cache_path.call_args.args[0]
        assert trash_path.parent == cache.trash_dir
        assert trash_path.exists()
        assert cache._discover_cache_keys() == set()

        cache._sweep_startup_state()

        assert not trash_path.exists()

    @pytest.mark.anyio
    async def test_eviction_skips_entry_when_unmount_fails(self, temp_cache_dir):
        """A failed unmount skips the key instead of forcing a lazy detach."""
        cache = RegistryArtifactCache(temp_cache_dir)
        stuck = cache._paths_for("stuck")
        stuck.entry_dir.mkdir(parents=True)
        stuck.squashfs_image_path.write_bytes(b"squashfs")
        stuck.squashfs_mount_dir.mkdir()
        os.utime(stuck.squashfs_image_path, (100.0, 100.0))
        os.utime(stuck.entry_dir, (100.0, 100.0))
        idle = write_image_entry(temp_cache_dir, "idle", size=16, mtime=300.0)
        mounted = {stuck.squashfs_mount_dir}

        process = AsyncMock()
        process.pid = 999_999_999
        process.communicate.return_value = (b"", b"target is busy")
        process.returncode = 32

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in mounted,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as create_subprocess_exec,
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            await cache._enforce_cache_budget(protected_key="pending")

        assert stuck.squashfs_image_path.exists()
        assert stuck.squashfs_mount_dir.exists()
        assert not idle.exists()
        create_subprocess_exec.assert_awaited_once()
        process.communicate.assert_awaited_once()
        kill_group.assert_called_once_with(process.pid, signal.SIGKILL)

    @pytest.mark.anyio
    async def test_eviction_discards_idle_runtime_state(self, temp_cache_dir):
        """An evicted key releases runtime state after every lock user exits."""
        cache = RegistryArtifactCache(temp_cache_dir)
        write_tarball_entry(temp_cache_dir, "bookkeeping")
        cache._acquire_lease("bookkeeping")
        cache._release_lease("bookkeeping")

        assert await cache._evict_entry("bookkeeping") == RegistryArtifactEviction(
            retired=True, reclaimed=True
        )
        assert "bookkeeping" not in cache._runtime

    @pytest.mark.anyio
    async def test_eviction_skips_busy_key(self, temp_cache_dir):
        """A key another task is materializing is never evicted underneath it."""
        cache = RegistryArtifactCache(temp_cache_dir)
        target_dir = write_tarball_entry(temp_cache_dir, "busy")
        lock = cache._runtime_for("busy").lock

        async with lock:
            assert await cache._evict_entry("busy") == RegistryArtifactEviction(
                retired=False, reclaimed=False
            )

        assert target_dir.is_dir()
