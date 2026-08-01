"""Registry artifact byte admission and budget convergence tests."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import pytest

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    RegistryArtifactCacheCapacityError,
    RegistryArtifactEviction,
    RegistryArtifactMaterializationContext,
    SquashfsArtifact,
    TarballArtifact,
    _delete_cache_path,
    compute_registry_artifact_cache_key,
)

from .registry_artifact_test_helpers import (
    MAX_BYTES_CONFIG,
    MAX_ENTRIES_CONFIG,
    SQUASHFS_ENABLED_CONFIG,
    lease_paths,
    tarball_payload,
    write_image_entry,
    write_tarball_entry,
)


class TestRegistryArtifactCacheBudget:
    """Enforce peak and steady-state cache capacity."""

    def test_delete_cache_path_reports_directory_failure(self, temp_cache_dir):
        """Physical deletion failures are observable instead of suppressed."""
        entry_dir = temp_cache_dir / "entry"
        entry_dir.mkdir()

        with (
            patch(
                "tracecat.executor.registry_artifact_storage.shutil.rmtree",
                side_effect=OSError("permission denied"),
            ),
            patch(
                "tracecat.executor.registry_artifact_storage.logger.warning"
            ) as warning,
        ):
            deleted = _delete_cache_path(entry_dir)

        assert deleted is False
        assert entry_dir.is_dir()
        warning.assert_called_once_with(
            "Failed to delete registry artifact cache path",
            path=str(entry_dir),
            error="permission denied",
        )

    @pytest.mark.anyio
    async def test_leased_entry_survives_eviction_of_idle_entry(self, temp_cache_dir):
        """Eviction must never remove an entry a live action is importing from."""
        cache = RegistryArtifactCache(temp_cache_dir)
        leased_uri = "s3://bucket/leased.tar.gz"
        idle_uri = "s3://bucket/idle.tar.gz"
        new_uri = "s3://bucket/new.tar.gz"
        leased_dir = write_tarball_entry(
            temp_cache_dir, compute_registry_artifact_cache_key(leased_uri)
        )
        idle_dir = write_tarball_entry(
            temp_cache_dir, compute_registry_artifact_cache_key(idle_uri)
        )

        async def mock_download(self, ctx, path):
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(MAX_ENTRIES_CONFIG, 2),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([leased_uri]):
                await lease_paths(cache, new_uri)

        assert leased_dir.is_dir()
        assert not idle_dir.exists()

    @pytest.mark.anyio
    async def test_cold_download_reserves_space_before_writing(
        self, temp_cache_dir: Path
    ) -> None:
        """Admission evicts idle bytes before a new download enters staging."""
        cache = RegistryArtifactCache(temp_cache_dir)
        idle = write_image_entry(temp_cache_dir, "idle", size=80, mtime=100.0)
        artifact_uri = "s3://bucket/new.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        payload = tarball_payload(size=32)
        max_bytes = len(payload) + 32
        capacity_checked = False

        async def download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            max_bytes: int,
            ensure_capacity: Callable[[int], Awaitable[None]],
        ) -> int:
            del key, bucket
            nonlocal capacity_checked
            assert max_bytes == len(payload) + 32
            await ensure_capacity(len(payload))
            capacity_checked = True
            assert not idle.exists()
            output_path.write_bytes(payload)
            return len(payload)

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch(
                "tracecat.executor.registry_artifacts.blob.download_file_to_path",
                side_effect=download_file_to_path,
            ),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [
                    cache._paths_for(cache_key).tarball_target_dir
                ]

        assert capacity_checked is True
        assert not idle.exists()
        assert (registry_paths[0] / "module.py").read_bytes() == b"x" * 32

    @pytest.mark.anyio
    async def test_compression_heavy_tarball_is_rejected_before_extraction(
        self, temp_cache_dir: Path
    ) -> None:
        """Compressed bytes plus declared extraction cannot exceed the cache cap."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/compression-heavy.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        payload = tarball_payload(size=4096)
        max_bytes = len(payload) + 256

        async def download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            max_bytes: int,
            ensure_capacity: Callable[[int], Awaitable[None]],
        ) -> int:
            del key, bucket
            assert max_bytes == len(payload) + 256
            await ensure_capacity(len(payload))
            output_path.write_bytes(payload)
            return len(payload)

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch(
                "tracecat.executor.registry_artifacts.blob.download_file_to_path",
                side_effect=download_file_to_path,
            ),
            patch.object(
                TarballArtifact,
                "extract",
                new_callable=AsyncMock,
            ) as extract,
        ):
            with pytest.raises(RegistryArtifactCacheCapacityError) as raised:
                async with cache.lease([artifact_uri]):
                    pass

        assert raised.value.additional_bytes == 4096
        assert raised.value.max_bytes == max_bytes
        extract.assert_not_awaited()
        assert not cache._paths_for(cache_key).entry_dir.exists()
        assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())

    @pytest.mark.anyio
    async def test_squashfs_expansion_is_rejected_before_extraction(
        self, temp_cache_dir: Path
    ) -> None:
        """SquashFS metadata is accounted before unsquashfs writes scratch."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/oversized.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def download(
            self: SquashfsArtifact,
            ctx: RegistryArtifactMaterializationContext,
            image_path: Path,
        ) -> float:
            del self, ctx
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"image")
            return 0.0

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 100),
            patch.object(
                RegistryArtifactMaterializationContext,
                "can_mount_squashfs",
                return_value=False,
            ),
            patch.object(SquashfsArtifact, "download", download),
            patch.object(
                SquashfsArtifact,
                "_squashfs_extracted_size",
                new_callable=AsyncMock,
                return_value=101,
            ),
            patch.object(
                SquashfsArtifact,
                "_extract_image",
                new_callable=AsyncMock,
            ) as extract_image,
        ):
            with pytest.raises(RegistryArtifactCacheCapacityError) as raised:
                async with cache.lease([artifact_uri]):
                    pass

        assert raised.value.additional_bytes == 101
        extract_image.assert_not_awaited()
        paths = cache._paths_for(cache_key)
        assert paths.squashfs_image_path.read_bytes() == b"image"
        assert not paths.squashfs_extract_dir.exists()

    @pytest.mark.anyio
    async def test_successful_admission_enforces_actual_size_before_yield(
        self, temp_cache_dir
    ):
        """Post-publication enforcement sees the new entry's actual size."""
        cache = RegistryArtifactCache(temp_cache_dir)
        idle = write_image_entry(temp_cache_dir, "idle", size=4096, mtime=100.0)
        new_uri = "s3://bucket/new.tar.gz"
        new_key = compute_registry_artifact_cache_key(new_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_bytes(b"x" * 4096)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 6000),
            patch(
                "tracecat.executor.registry_artifact_materialization._tarball_extracted_size",
                return_value=4096,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([new_uri]) as registry_paths:
                assert registry_paths == [cache._paths_for(new_key).tarball_target_dir]
                assert not idle.exists()

        assert not idle.exists()
        assert cache._paths_for(new_key).tarball_target_dir.is_dir()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_deletion_failure_does_not_block_materialization(
        self, temp_cache_dir
    ):
        """Failed cleanup is reported while cache admission remains fail-open."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        write_image_entry(temp_cache_dir, "idle", size=4096, mtime=100.0)
        artifact_uri = "s3://bucket/new.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                return_value=False,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
            patch(
                "tracecat.executor.registry_artifact_storage.logger.warning"
            ) as warning,
        ):
            registry_paths = await lease_paths(cache, artifact_uri)

        assert registry_paths == [cache._paths_for(cache_key).tarball_target_dir]
        assert registry_paths[0].is_dir()
        assert cache._budget_dirty is True
        warning.assert_any_call(
            "Registry artifact eviction remains pending physical deletion",
            cache_key="idle",
            trash_path=ANY,
        )

    @pytest.mark.anyio
    async def test_failed_physical_delete_retries_without_extra_eviction(
        self, temp_cache_dir
    ):
        """Failed byte reclamation stops eviction until trash cleanup succeeds."""
        cache = RegistryArtifactCache(temp_cache_dir)
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
        real_delete = _delete_cache_path
        failed_once = False

        def fail_once(path: Path) -> bool:
            nonlocal failed_once
            if not failed_once:
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
            assert await cache._enforce_cache_budget() is False
            assert not oldest.exists()
            assert older.exists()
            assert newest.exists()
            assert cache._discover_cache_keys() == {"older", "newest"}
            assert len(tuple(cache.trash_dir.iterdir())) == 1

            assert await cache._enforce_cache_budget() is True

        assert not older.exists()
        assert newest.exists()
        assert not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_rename_failure_does_not_block_materialization(self, temp_cache_dir):
        """A failed atomic retirement keeps the old entry and admits new work."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        idle = write_image_entry(temp_cache_dir, "idle", size=16, mtime=100.0)
        artifact_uri = "s3://bucket/new-after-rename-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._move_entry_to_trash",
                side_effect=OSError("rename failed"),
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            registry_paths = await lease_paths(cache, artifact_uri)

        assert registry_paths == [cache._paths_for(cache_key).tarball_target_dir]
        assert registry_paths[0].is_dir()
        assert idle.is_file()
        assert cache._budget_dirty is True

    @pytest.mark.anyio
    async def test_cache_scan_failure_does_not_block_materialization(
        self, temp_cache_dir
    ):
        """Maintenance errors are observable while artifact admission stays open."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/new-after-scan-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch.object(
                cache,
                "_enforce_cache_budget",
                new_callable=AsyncMock,
                side_effect=PermissionError("denied"),
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization._tarball_extracted_size",
                return_value=9,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [
                    cache._paths_for(cache_key).tarball_target_dir
                ]

    @pytest.mark.anyio
    async def test_releasing_a_lease_skips_the_scan_for_a_cache_hit(
        self, temp_cache_dir
    ):
        """Steady-state cache hits must not pay for a full cache scan."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/cached.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        write_tarball_entry(temp_cache_dir, cache_key)
        cache._budget_dirty = False

        with patch.object(
            cache,
            "_scan_cache_entries",
            side_effect=AssertionError("cache hits must not scan the cache dir"),
        ):
            async with cache.lease([artifact_uri]):
                pass

        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_failed_cold_admission_keeps_warm_lru(self, temp_cache_dir):
        """A missing cold artifact must not evict an existing warm entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        warm_uri = "s3://bucket/warm.tar.gz"
        warm_key = compute_registry_artifact_cache_key(warm_uri)
        warm_dir = write_tarball_entry(temp_cache_dir, warm_key)
        missing_uri = "s3://bucket/missing.tar.gz"
        missing_key = compute_registry_artifact_cache_key(missing_uri)

        async def mock_download(self, ctx, path):
            raise FileNotFoundError("missing artifact")

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", mock_download),
        ):
            with pytest.raises(FileNotFoundError, match="missing artifact"):
                async with cache.lease([missing_uri]):
                    pytest.fail("failed admission must not yield a lease")

        assert warm_dir.is_dir()
        assert not cache._paths_for(missing_key).entry_dir.exists()

    @pytest.mark.anyio
    async def test_materialization_rearms_budget_dirty_consumed_mid_flight(
        self, temp_cache_dir
    ):
        """A convergence pass consuming the signal mid-download cannot unarm it."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        assert cache._budget_dirty is False

        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)

        async def mock_materialize(self, ctx):
            # A concurrent lease release consumes the dirty signal and finishes
            # its scan before this attempt lands its canonical bytes.
            cache._budget_dirty = False
            ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
            ctx.paths.squashfs_image_path.write_bytes(b"late image")
            return [ctx.paths.squashfs_mount_dir]

        with (
            patch.object(
                cache,
                "_artifact_candidates",
                new_callable=AsyncMock,
                return_value=[artifact],
            ),
            patch.object(SquashfsArtifact, "materialize", mock_materialize),
        ):
            async with cache.lease([artifact_uri]):
                assert cache._budget_dirty is True

        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_release_keeps_retrying_while_the_cache_stays_over_budget(
        self, temp_cache_dir
    ):
        """A cache that cannot shrink yet must stay marked for re-enforcement."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/pinned.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        write_image_entry(temp_cache_dir, cache_key, size=4096, mtime=100.0)
        write_tarball_entry(temp_cache_dir, cache_key)
        cache._budget_dirty = True
        # A second holder keeps the entry pinned past the inner lease.
        cache._acquire_lease(cache_key)

        with patch(MAX_ENTRIES_CONFIG, 0), patch(MAX_BYTES_CONFIG, 1):
            async with cache.lease([artifact_uri]):
                pass

        assert cache._budget_dirty is True

    @pytest.mark.anyio
    async def test_convergence_rescans_after_concurrent_materialization(
        self, temp_cache_dir
    ):
        """A materialization during a budget scan must schedule another scan."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/concurrent.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        scan_started = asyncio.Event()
        materialized = asyncio.Event()
        convergence_scans = 0

        async def mock_enforce_cache_budget(
            *, protected_key: str | None = None
        ) -> bool:
            nonlocal convergence_scans
            if protected_key is not None:
                return True

            convergence_scans += 1
            if convergence_scans == 1:
                scan_started.set()
                await materialized.wait()
            return True

        async def mock_download(self, ctx, path):
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 1")

        cache._budget_dirty = True
        with (
            patch.object(
                cache,
                "_enforce_cache_budget",
                side_effect=mock_enforce_cache_budget,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            convergence = asyncio.create_task(cache._converge_cache_budget())
            await scan_started.wait()
            registry_paths = await lease_paths(cache, artifact_uri)
            materialized.set()
            await convergence

        assert registry_paths == [cache._paths_for(cache_key).tarball_target_dir]
        assert convergence_scans == 2
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_cancelled_convergence_rearms_budget_dirty(
        self, temp_cache_dir
    ) -> None:
        """Cancellation must preserve the need for a later budget pass."""
        cache = RegistryArtifactCache(temp_cache_dir)
        enforcement_started = asyncio.Event()
        finish_enforcement = asyncio.Event()

        async def mock_enforce_cache_budget(
            *, protected_key: str | None = None
        ) -> bool:
            del protected_key
            enforcement_started.set()
            await finish_enforcement.wait()
            return True

        cache._budget_dirty = True
        with patch.object(
            cache,
            "_enforce_cache_budget",
            side_effect=mock_enforce_cache_budget,
        ):
            convergence = asyncio.create_task(cache._converge_cache_budget())
            await enforcement_started.wait()
            convergence.cancel()

            with pytest.raises(asyncio.CancelledError):
                await convergence

        assert cache._budget_dirty is True

    @pytest.mark.parametrize(
        "oldest_has_tarball",
        [False, True],
        ids=["squashfs-only", "tarball-bearing"],
    )
    @pytest.mark.anyio
    async def test_enforce_budget_evicts_least_recently_used_until_under_max_bytes(
        self, temp_cache_dir, oldest_has_tarball: bool
    ):
        """Size eviction stops once the cache is within budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        oldest = write_image_entry(temp_cache_dir, "oldest", size=4096, mtime=100.0)
        if oldest_has_tarball:
            write_tarball_entry(temp_cache_dir, "oldest")
            oldest_entry = cache._paths_for("oldest").entry_dir
            os.utime(oldest_entry, (100.0, 100.0))
        older = write_image_entry(temp_cache_dir, "older", size=4096, mtime=200.0)
        newest = write_image_entry(temp_cache_dir, "newest", size=4096, mtime=300.0)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 9000),
        ):
            within_budget = await cache._enforce_cache_budget(protected_key="pending")

        assert within_budget is True
        assert not oldest.exists()
        assert not cache._paths_for("oldest").tarball_target_dir.exists()
        assert older.exists()
        assert newest.exists()

    @pytest.mark.anyio
    async def test_final_lease_release_unmounts_and_retains_image(self, temp_cache_dir):
        """An idle entry releases its loop device without deleting its image."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}

        process = AsyncMock()
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        async def mock_umount(*args, **kwargs):
            mounted.discard(paths.squashfs_mount_dir)
            return process

        with (
            patch.object(Path, "is_mount", lambda self: self in mounted),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/umount",
            ),
            patch.object(
                asyncio,
                "create_subprocess_exec",
                side_effect=mock_umount,
            ),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [paths.squashfs_mount_dir]
                assert paths.squashfs_mount_dir in mounted

            assert paths.squashfs_mount_dir not in mounted

        assert paths.squashfs_image_path.read_bytes() == b"squashfs"
        assert paths.squashfs_mount_dir.is_dir()

    @pytest.mark.anyio
    async def test_concurrent_budget_passes_only_evict_once(self, temp_cache_dir):
        """A waiting budget pass must re-scan after the active pass evicts."""
        cache = RegistryArtifactCache(temp_cache_dir)
        oldest = write_image_entry(temp_cache_dir, "oldest", size=16, mtime=100.0)
        retained = write_image_entry(temp_cache_dir, "retained", size=16, mtime=200.0)
        original_scan = cache._scan_cache_entries
        first_scan_started = threading.Event()
        second_scan_started = threading.Event()
        release_first_scan = threading.Event()
        release_second_scan = threading.Event()
        scan_count_lock = threading.Lock()
        scan_count = 0

        def controlled_scan():
            nonlocal scan_count
            entries = original_scan()
            with scan_count_lock:
                scan_index = scan_count
                scan_count += 1
            if scan_index == 0:
                first_scan_started.set()
                release_first_scan.wait(timeout=5)
            elif scan_index == 1:
                second_scan_started.set()
                release_second_scan.wait(timeout=5)
            return entries

        eviction_started = asyncio.Event()
        finish_eviction = asyncio.Event()
        extra_eviction_finished = asyncio.Event()
        evicted_keys: list[str] = []

        async def controlled_evict(
            cache_key: str,
        ) -> RegistryArtifactEviction:
            if cache_key == "oldest":
                if eviction_started.is_set():
                    return RegistryArtifactEviction(
                        retired=False,
                        reclaimed=False,
                    )
                eviction_started.set()
                await finish_eviction.wait()
                _delete_cache_path(cache._paths_for("oldest").entry_dir)
            else:
                _delete_cache_path(cache._paths_for("retained").entry_dir)
                extra_eviction_finished.set()
            evicted_keys.append(cache_key)
            return RegistryArtifactEviction(retired=True, reclaimed=True)

        with (
            patch.object(cache, "_scan_cache_entries", side_effect=controlled_scan),
            patch.object(cache, "_evict_entry", side_effect=controlled_evict),
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            first_pass = asyncio.create_task(cache._enforce_cache_budget())
            assert await asyncio.to_thread(first_scan_started.wait, 1)
            second_pass = asyncio.create_task(cache._enforce_cache_budget())
            second_scan_overlapped = await asyncio.to_thread(
                second_scan_started.wait, 0.5
            )

            release_first_scan.set()
            await asyncio.wait_for(eviction_started.wait(), timeout=1)
            release_second_scan.set()
            try:
                await asyncio.wait_for(extra_eviction_finished.wait(), timeout=0.2)
            except TimeoutError:
                pass
            finish_eviction.set()
            await asyncio.gather(first_pass, second_pass)

        assert second_scan_overlapped is False
        assert scan_count == 2
        assert evicted_keys == ["oldest"]
        assert not oldest.exists()
        assert retained.exists()

    @pytest.mark.anyio
    async def test_enforce_budget_ignores_missing_protected_key(self, temp_cache_dir):
        """Only successfully published entries count against the entry budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        existing = write_image_entry(temp_cache_dir, "existing", size=16, mtime=100.0)

        with patch(MAX_ENTRIES_CONFIG, 1), patch(MAX_BYTES_CONFIG, 0):
            within_budget = await cache._enforce_cache_budget(protected_key="missing")

        assert within_budget is True
        assert existing.exists()

    @pytest.mark.anyio
    async def test_enforce_budget_never_evicts_the_protected_key(self, temp_cache_dir):
        """The newly materialized key is exempt even when it is the LRU entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        protected = write_image_entry(
            temp_cache_dir, "protected", size=4096, mtime=100.0
        )
        other = write_image_entry(temp_cache_dir, "other", size=4096, mtime=300.0)

        with patch(MAX_ENTRIES_CONFIG, 1), patch(MAX_BYTES_CONFIG, 0):
            await cache._enforce_cache_budget(protected_key="protected")

        assert protected.exists()
        assert not other.exists()

    @pytest.mark.anyio
    async def test_enforce_budget_proceeds_over_budget_when_everything_is_leased(
        self, temp_cache_dir
    ):
        """An over-budget cache must degrade, not fail the action."""
        cache = RegistryArtifactCache(temp_cache_dir)
        leased = write_image_entry(temp_cache_dir, "leased", size=4096, mtime=100.0)
        cache._acquire_lease("leased")

        with patch(MAX_ENTRIES_CONFIG, 0), patch(MAX_BYTES_CONFIG, 1):
            within_budget = await cache._enforce_cache_budget(protected_key="missing")

        assert within_budget is False
        assert leased.exists()
