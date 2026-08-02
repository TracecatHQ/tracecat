"""Registry artifact lease lifetime and concurrency tests."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import tracecat_registry

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    RegistryArtifactCacheLoopError,
    RegistryArtifactEviction,
    RegistryArtifactMaterializationContext,
    SquashfsArtifact,
    TarballArtifact,
    bundled_builtin_registry_uri,
    compute_registry_artifact_cache_key,
)

from .registry_artifact_test_helpers import (
    MAX_BYTES_CONFIG,
    MAX_ENTRIES_CONFIG,
    SQUASHFS_ENABLED_CONFIG,
    SquashfsMountHarness,
    write_image_entry,
    write_tarball_entry,
)


class TestRegistryArtifactCacheLease:
    """Tests for lease-based pinning of registry artifact cache entries."""

    @pytest.mark.anyio
    async def test_cache_rejects_use_from_a_second_event_loop(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect the process-wide cache from thread-local Temporal loops.

        The cache owns asyncio locks, tasks, and refcounts as one unit. A typed
        ownership failure prevents a future synchronous activity from silently
        sharing only part of that state across thread-local event loops, which
        previously caused intermittent cross-loop failures in storage caches.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()

        def use_cache_from_another_thread() -> None:
            asyncio.run(cache.ensure_swept())

        with pytest.raises(RegistryArtifactCacheLoopError):
            await asyncio.to_thread(use_cache_from_another_thread)

        # A rejected caller must not poison the owning loop's cache.
        async with cache.lease(None) as registry_paths:
            assert registry_paths == [temp_cache_dir / "base"]

    def test_touch_entry_refreshes_tarball_root_mtime(self, temp_cache_dir):
        """Touching a tarball-only entry persists its restart-safe recency."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "tarball-only"
        write_tarball_entry(temp_cache_dir, cache_key)
        entry_dir = cache._paths_for(cache_key).entry_dir
        os.utime(entry_dir, (100.0, 100.0))

        cache._touch_entry(cache_key)

        assert entry_dir.stat().st_mtime > 100.0

    @pytest.mark.anyio
    async def test_lease_refcounts_and_touches_image_mtime(self, temp_cache_dir):
        """Acquire and final release persist the restart-safe LRU timestamp."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/leased.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = write_tarball_entry(temp_cache_dir, cache_key)
        image_path = write_image_entry(temp_cache_dir, cache_key, size=16, mtime=100.0)
        entry_dir = cache._paths_for(cache_key).entry_dir

        async with cache.lease([artifact_uri]) as registry_paths:
            assert registry_paths == [target_dir]
            assert cache._refcount(cache_key) == 1
            assert entry_dir.stat().st_mtime > 100.0
            os.utime(entry_dir, (100.0, 100.0))

        assert cache._refcount(cache_key) == 0
        assert entry_dir.stat().st_mtime > 100.0
        assert image_path.is_file()

    @pytest.mark.anyio
    async def test_lease_releases_refcount_when_materialization_fails(
        self, temp_cache_dir
    ):
        """A failed materialization must not leak a pin or empty cache entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/broken.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            raise RuntimeError("download failed")

        with patch.object(TarballArtifact, "download", mock_download):
            with pytest.raises(RuntimeError, match="download failed"):
                async with cache.lease([artifact_uri]):
                    pass

        assert cache._refcount(cache_key) == 0
        assert not cache._paths_for(cache_key).entry_dir.exists()
        assert cache_key not in cache._discover_cache_keys()
        assert cache_key not in cache._runtime

    @pytest.mark.anyio
    async def test_distinct_failed_keys_do_not_accumulate_runtime_states(
        self, temp_cache_dir: Path
    ) -> None:
        """Failed cold keys release lock state after their last waiter exits."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def fail_download(self, ctx, path):
            del self, ctx, path
            raise RuntimeError("download failed")

        with patch.object(TarballArtifact, "download", fail_download):
            for index in range(100):
                with pytest.raises(RuntimeError, match="download failed"):
                    async with cache.lease([f"s3://bucket/broken-{index}.tar.gz"]):
                        pass

        assert cache._runtime == {}

    @pytest.mark.anyio
    async def test_failed_first_admission_converges_deposited_image(
        self, temp_cache_dir: Path
    ) -> None:
        """A failed first artifact must not strand an over-budget image."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/failed-first.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)

        async def fail_after_deposit(
            artifact: SquashfsArtifact,
            ctx: RegistryArtifactMaterializationContext,
        ) -> list[Path]:
            del artifact
            ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
            ctx.paths.squashfs_image_path.write_bytes(b"reusable image")
            raise RuntimeError("mount failed")

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 1),
            patch.object(
                cache,
                "_artifact_candidates",
                new_callable=AsyncMock,
                return_value=[artifact],
            ),
            patch.object(SquashfsArtifact, "materialize", fail_after_deposit),
        ):
            with pytest.raises(RuntimeError, match="mount failed"):
                async with cache.lease([artifact_uri]):
                    pass

        assert cache._refcount(cache_key) == 0
        assert not cache._paths_for(cache_key).entry_dir.exists()
        assert cache._discover_cache_keys() == set()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_cancelled_lease_admission_releases_refcount(self, temp_cache_dir):
        """Cancellation during candidate lookup must not leak a permanent pin."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        lookup_started = asyncio.Event()
        finish_lookup = asyncio.Event()

        async def blocked_sidecar_lookup(**kwargs):
            lookup_started.set()
            await finish_lookup.wait()
            return False

        async def take_lease() -> None:
            async with cache.lease([artifact_uri]):
                pass

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch.object(cache, "_sidecar_exists", blocked_sidecar_lookup),
        ):
            acquisition = asyncio.create_task(take_lease())
            await lookup_started.wait()
            assert cache._refcount(cache_key) == 1
            acquisition.cancel()
            with pytest.raises(asyncio.CancelledError):
                await acquisition

        assert cache._refcount(cache_key) == 0
        assert not cache._paths_for(cache_key).entry_dir.exists()

    @pytest.mark.anyio
    async def test_repeated_cancellation_finishes_acquisition_rollback(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """A second cancellation cannot abandon the final rollback unmount."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        lookup_started = asyncio.Event()
        rollback_started = asyncio.Event()
        finish_rollback = asyncio.Event()

        async def blocked_sidecar_lookup(**kwargs):
            del kwargs
            lookup_started.set()
            await asyncio.Event().wait()

        async def blocked_unmount(requested_key: str) -> None:
            assert requested_key == cache_key
            rollback_started.set()
            await finish_rollback.wait()

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch.object(cache, "_sidecar_exists", blocked_sidecar_lookup),
            patch.object(cache, "_unmount_idle_entry", blocked_unmount),
        ):
            acquisition = asyncio.create_task(cache._lease_artifact(artifact_uri))
            await lookup_started.wait()
            acquisition.cancel()
            await rollback_started.wait()

            acquisition.cancel()
            await asyncio.sleep(0)
            assert not acquisition.done()

            finish_rollback.set()
            with pytest.raises(asyncio.CancelledError):
                await acquisition

        assert cache._refcount(cache_key) == 0

    @pytest.mark.anyio
    async def test_cancelled_waiter_preserves_existing_same_key_lease(
        self, temp_cache_dir: Path
    ) -> None:
        """A waiter cancelled before admission cannot release another holder."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/shared.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        lock = cache._runtime_for(cache_key).lock

        async def take_lease() -> None:
            async with cache.lease([artifact_uri]):
                pytest.fail("cancelled waiter must not enter the lease context")

        unmount_idle_entry = AsyncMock()
        with patch.object(cache, "_unmount_idle_entry", unmount_idle_entry):
            async with lock:
                cache._acquire_lease(cache_key)
                try:
                    waiter = asyncio.create_task(take_lease())
                    await asyncio.sleep(0)
                    assert not waiter.done()

                    waiter.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await waiter

                    assert cache._refcount(cache_key) == 1
                    unmount_idle_entry.assert_not_awaited()
                finally:
                    cache._release_lease(cache_key)

    @pytest.mark.anyio
    async def test_lease_without_uris_returns_base_pythonpath_dir(self, temp_cache_dir):
        """No artifact URIs still yields the base PYTHONPATH directory."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async with cache.lease(None) as registry_paths:
            assert registry_paths == [temp_cache_dir / "base"]
            assert registry_paths[0].is_dir()

        assert cache._runtime == {}

    @pytest.mark.anyio
    async def test_lease_preserves_uri_order(self, temp_cache_dir):
        """Multiple artifacts keep their deterministic PYTHONPATH order."""
        cache = RegistryArtifactCache(temp_cache_dir)
        uris = ["s3://bucket/first.tar.gz", "s3://bucket/second.tar.gz"]
        expected = [
            write_tarball_entry(
                temp_cache_dir, compute_registry_artifact_cache_key(uri)
            )
            for uri in uris
        ]

        async with cache.lease(uris) as registry_paths:
            assert registry_paths == expected

    @pytest.mark.anyio
    async def test_overlapping_same_key_leases_share_one_mount_until_final_release(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect refcount and loop-device lifetime under heavy fan-in.

        Every holder must observe one published mount, intermediate releases
        must leave that mount usable, and exactly the final release may reclaim
        its loop device while retaining the downloaded image for a remount.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/shared.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = SquashfsMountHarness(cache)
        holder_count = 32
        entered = 0
        all_entered = asyncio.Event()
        releases = [asyncio.Event() for _ in range(holder_count)]

        async def hold_lease(index: int) -> None:
            nonlocal entered
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [
                    cache._paths_for(cache_key).squashfs_mount_dir
                ]
                entered += 1
                if entered == holder_count:
                    all_entered.set()
                await releases[index].wait()

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in harness.mounted,
            ),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            holders = [
                asyncio.create_task(hold_lease(index)) for index in range(holder_count)
            ]
            await asyncio.wait_for(all_entered.wait(), timeout=5)

            mount_dir = cache._paths_for(cache_key).squashfs_mount_dir
            assert cache._refcount(cache_key) == holder_count
            assert harness.mount_attempts == [cache_key]
            assert harness.extraction_attempts == []
            assert mount_dir in harness.mounted

            for release in releases[:-1]:
                release.set()
            await asyncio.gather(*holders[:-1])

            assert cache._refcount(cache_key) == 1
            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            releases[-1].set()
            await holders[-1]

        assert cache._refcount(cache_key) == 0
        assert harness.unmounts == [mount_dir]
        assert mount_dir not in harness.mounted
        assert cache._paths_for(cache_key).squashfs_image_path.is_file()
        assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())
        assert not cache.trash_dir.exists() or not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_cancelling_one_holder_preserves_sibling_leases(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect sibling actions from another holder's cancellation.

        Cancellation must release exactly one pin without unmounting the shared
        artifact beneath surviving actions; the last surviving holder remains
        solely responsible for loop-device reclamation.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/cancel-one.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = SquashfsMountHarness(cache)
        entered = [asyncio.Event() for _ in range(3)]
        releases = [asyncio.Event() for _ in range(3)]

        async def hold_lease(index: int) -> None:
            async with cache.lease([artifact_uri]):
                entered[index].set()
                await releases[index].wait()

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in harness.mounted,
            ),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            holders = [asyncio.create_task(hold_lease(index)) for index in range(3)]
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in entered)),
                timeout=5,
            )

            holders[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await holders[0]

            mount_dir = cache._paths_for(cache_key).squashfs_mount_dir
            assert cache._refcount(cache_key) == 2
            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            releases[1].set()
            await holders[1]
            assert cache._refcount(cache_key) == 1
            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            releases[2].set()
            await holders[2]

        assert cache._refcount(cache_key) == 0
        assert harness.mount_attempts == [cache_key]
        assert harness.unmounts == [mount_dir]

    @pytest.mark.anyio
    async def test_repeated_cancellation_finishes_all_lease_cleanup(
        self, temp_cache_dir: Path
    ) -> None:
        """Every unmount and budget pass finishes before cancellation propagates."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uris = [
            "s3://bucket/first.tar.gz",
            "s3://bucket/second.tar.gz",
        ]
        cache_keys = [compute_registry_artifact_cache_key(uri) for uri in artifact_uris]
        for cache_key in cache_keys:
            write_tarball_entry(temp_cache_dir, cache_key)

        lease_entered = asyncio.Event()
        first_unmount_started = asyncio.Event()
        finish_first_unmount = asyncio.Event()
        cleanup_calls: list[str] = []

        async def mock_unmount_idle_entry(cache_key: str) -> None:
            cleanup_calls.append(cache_key)
            if cache_key == cache_keys[0]:
                first_unmount_started.set()
                await finish_first_unmount.wait()

        async def mock_converge_cache_budget() -> None:
            cleanup_calls.append("converge")

        async def hold_lease() -> None:
            async with cache.lease(artifact_uris):
                lease_entered.set()
                await asyncio.Event().wait()

        with (
            patch.object(cache, "_unmount_idle_entry", mock_unmount_idle_entry),
            patch.object(cache, "_converge_cache_budget", mock_converge_cache_budget),
        ):
            holder = asyncio.create_task(hold_lease())
            await lease_entered.wait()
            holder.cancel()
            await first_unmount_started.wait()

            holder.cancel()
            await asyncio.sleep(0)
            assert not holder.done()

            finish_first_unmount.set()
            with pytest.raises(asyncio.CancelledError):
                await holder

        assert cleanup_calls == [*cache_keys, "converge"]
        assert all(cache._refcount(cache_key) == 0 for cache_key in cache_keys)

    @pytest.mark.anyio
    async def test_cleanup_failure_preserves_successful_lease_outcome(
        self, temp_cache_dir: Path
    ) -> None:
        """Post-lease maintenance cannot replace a successful caller result."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/cleanup-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = write_tarball_entry(temp_cache_dir, cache_key)

        async def fail_cleanup(
            idle_keys: list[str],
            *,
            converge: bool,
        ) -> None:
            del idle_keys, converge
            raise RuntimeError("cleanup failed")

        with (
            patch.object(cache, "_finish_lease_cleanup", fail_cleanup),
            patch(
                "tracecat.executor.registry_artifacts.logger.exception"
            ) as log_exception,
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                result = registry_paths

        assert result == [target_dir]
        assert cache._refcount(cache_key) == 0
        log_exception.assert_called_once_with(
            "Registry artifact lease cleanup failed; preserving caller outcome",
            cache_dir=str(temp_cache_dir),
            error="cleanup failed",
        )

    @pytest.mark.anyio
    async def test_new_lease_racing_final_release_prevents_stale_unmount(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect the zero-refcount-to-unmount handoff from a new acquisition.

        A lease can arrive after the old holder decrements to zero but before
        unmount takes the key lock. The lock-time refcount recheck must preserve
        the mount for that newcomer instead of returning a path being torn down.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/release-race.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = SquashfsMountHarness(cache)
        mount_dir = harness.seed_mount(cache_key)
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        release_reached_unmount = asyncio.Event()
        allow_unmount_recheck = asyncio.Event()
        newcomer_entered = asyncio.Event()
        release_newcomer = asyncio.Event()
        original_unmount_idle_entry = cache._unmount_idle_entry
        unmount_requests = 0

        async def pause_first_unmount_request(requested_key: str) -> None:
            nonlocal unmount_requests
            unmount_requests += 1
            if unmount_requests == 1:
                release_reached_unmount.set()
                await allow_unmount_recheck.wait()
            await original_unmount_idle_entry(requested_key)

        async def old_holder() -> None:
            async with cache.lease([artifact_uri]):
                first_entered.set()
                await release_first.wait()

        async def new_holder() -> None:
            async with cache.lease([artifact_uri]):
                newcomer_entered.set()
                await release_newcomer.wait()

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in harness.mounted,
            ),
            patch.object(cache, "_unmount", harness.unmount),
            patch.object(
                cache,
                "_unmount_idle_entry",
                pause_first_unmount_request,
            ),
        ):
            old_task = asyncio.create_task(old_holder())
            await first_entered.wait()
            release_first.set()
            await release_reached_unmount.wait()
            assert cache._refcount(cache_key) == 0

            new_task = asyncio.create_task(new_holder())
            await newcomer_entered.wait()
            assert cache._refcount(cache_key) == 1

            allow_unmount_recheck.set()
            await old_task

            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            release_newcomer.set()
            await new_task

        assert cache._refcount(cache_key) == 0
        assert harness.unmounts == [mount_dir]

    @pytest.mark.anyio
    async def test_partial_multi_artifact_failure_rolls_back_prior_leases(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect sequential multi-artifact admission from partial pin leaks.

        If a middle artifact fails, every earlier acquisition must be released
        and unmounted, the failed key must leave no shell, later artifacts must
        never be acquired, and the release path must still request convergence.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        first_uri = "s3://bucket/first.squashfs"
        failed_uri = "s3://bucket/failed.tar.gz"
        untouched_uri = "s3://bucket/untouched.tar.gz"
        first_key = compute_registry_artifact_cache_key(first_uri)
        failed_key = compute_registry_artifact_cache_key(failed_uri)
        untouched_key = compute_registry_artifact_cache_key(untouched_uri)
        harness = SquashfsMountHarness(cache)
        first_mount = harness.seed_mount(first_key)
        untouched_path = write_tarball_entry(temp_cache_dir, untouched_key)

        async def fail_download(
            artifact: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
            output_path: Path,
        ) -> None:
            del artifact, ctx, output_path
            raise RuntimeError("download failed")

        tracked_lease_artifact = AsyncMock(wraps=cache._lease_artifact)
        converge_cache_budget = AsyncMock()

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in harness.mounted,
            ),
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", fail_download),
            patch.object(cache, "_unmount", harness.unmount),
            patch.object(cache, "_lease_artifact", tracked_lease_artifact),
            patch.object(
                cache,
                "_converge_cache_budget",
                converge_cache_budget,
            ),
        ):
            with pytest.raises(RuntimeError, match="download failed"):
                async with cache.lease([first_uri, failed_uri, untouched_uri]):
                    pass

        requested_uris = [
            await_call.args[0] for await_call in tracked_lease_artifact.await_args_list
        ]
        assert requested_uris == [first_uri, failed_uri]
        assert cache._refcount(first_key) == 0
        assert cache._refcount(failed_key) == 0
        assert cache._refcount(untouched_key) == 0
        assert harness.unmounts == [first_mount]
        assert cache._paths_for(first_key).squashfs_image_path.is_file()
        assert not cache._paths_for(failed_key).entry_dir.exists()
        assert untouched_path.is_dir()
        converge_cache_budget.assert_awaited_once_with()
        assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())
        assert not cache.trash_dir.exists() or not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_waiter_retries_after_first_same_key_materializer_fails(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect same-key waiters from a failed cold-cache publisher.

        The first materializer may fail after writing scratch. Its waiter must
        retry against a clean staging area, publish the sole valid entry, and
        leave neither a leaked pin nor an empty LRU-visible cache shell.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/retry-after-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        first_download_started = asyncio.Event()
        fail_first_download = asyncio.Event()
        retry_download_started = asyncio.Event()
        allow_retry_download = asyncio.Event()
        download_attempts = 0
        retry_saw_clean_staging = False

        async def controlled_download(
            artifact: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
            output_path: Path,
        ) -> None:
            del artifact, ctx
            nonlocal download_attempts, retry_saw_clean_staging
            download_attempts += 1
            if download_attempts == 1:
                output_path.write_bytes(b"partial")
                first_download_started.set()
                await fail_first_download.wait()
                raise RuntimeError("first publisher failed")

            retry_saw_clean_staging = not any(cache.staging_dir.iterdir())
            retry_download_started.set()
            await allow_retry_download.wait()
            output_path.write_bytes(b"complete")

        async def mock_extract(
            artifact: TarballArtifact,
            tarball_path: Path,
            target_dir: Path,
        ) -> None:
            del artifact
            assert tarball_path.read_bytes() == b"complete"
            (target_dir / "module.py").write_text("VALUE = 1")

        async def take_lease() -> list[Path]:
            async with cache.lease([artifact_uri]) as registry_paths:
                return registry_paths

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch(
                "tracecat.executor.registry_artifact_materialization._tarball_extracted_size",
                return_value=1,
            ),
            patch.object(TarballArtifact, "download", controlled_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            first = asyncio.create_task(take_lease())
            await first_download_started.wait()
            waiter = asyncio.create_task(take_lease())
            await asyncio.sleep(0)
            assert download_attempts == 1

            fail_first_download.set()
            await retry_download_started.wait()
            allow_retry_download.set()
            registry_paths = await waiter
            with pytest.raises(RuntimeError, match="first publisher failed"):
                await first

        target_dir = cache._paths_for(cache_key).tarball_target_dir
        assert registry_paths == [target_dir]
        assert (target_dir / "module.py").read_text() == "VALUE = 1"
        assert download_attempts == 2
        assert retry_saw_clean_staging is True
        assert cache._discover_cache_keys() == {cache_key}
        assert cache._refcount(cache_key) == 0
        assert not any(cache.staging_dir.iterdir())
        assert not cache.trash_dir.exists() or not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_duplicate_uri_balances_each_acquisition_and_release(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect list bookkeeping when one lease requests the same URI twice.

        Duplicate PYTHONPATH entries intentionally acquire two pins. Both must
        be released, while materialization and final unmount still occur once;
        accidental deduplication on only one side would leak or underflow pins.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/duplicate.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = SquashfsMountHarness(cache)

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in harness.mounted,
            ),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            async with cache.lease([artifact_uri, artifact_uri]) as registry_paths:
                mount_dir = cache._paths_for(cache_key).squashfs_mount_dir
                assert registry_paths == [mount_dir, mount_dir]
                assert cache._refcount(cache_key) == 2
                assert harness.mount_attempts == [cache_key]

        assert cache._refcount(cache_key) == 0
        assert harness.unmounts == [mount_dir]
        assert cache._paths_for(cache_key).squashfs_image_path.is_file()

    @pytest.mark.anyio
    async def test_lease_is_never_admitted_across_an_in_flight_eviction(
        self, temp_cache_dir
    ):
        """A lease must not return a mount an in-flight eviction is deleting."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}
        umount_started = asyncio.Event()
        finish_umount = asyncio.Event()
        lease_waiting_for_key = asyncio.Event()
        remounts: list[str] = []
        original_runtime_for = cache._runtime_for

        def observed_runtime_for(requested_key: str):
            runtime = original_runtime_for(requested_key)
            if requested_key == cache_key and umount_started.is_set():
                lease_waiting_for_key.set()
            return runtime

        umount_process = AsyncMock()
        umount_process.pid = 999_999_999
        umount_process.communicate.return_value = (b"", b"")
        umount_process.returncode = 0

        async def mock_umount(*args, **kwargs):
            umount_started.set()
            await finish_umount.wait()
            mounted.discard(paths.squashfs_mount_dir)
            return umount_process

        async def mock_mount(self, ctx, image_path):
            remounts.append(ctx.cache_key)
            target_dir = ctx.paths.squashfs_mount_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            mounted.add(target_dir)
            return target_dir

        leased_paths: list[Path] = []
        leased_path_exists: list[bool] = []

        async def take_lease() -> None:
            async with cache.lease([artifact_uri]) as registry_paths:
                leased_paths.extend(registry_paths)
                leased_path_exists.append(registry_paths[0].is_dir())

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in mounted,
            ),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ),
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
            patch.object(cache, "_runtime_for", side_effect=observed_runtime_for),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            # This test targets the per-key eviction/lease handoff. Keep the
            # lease's budget pass from concurrently sweeping the same trash
            # path and turning physical reclamation into a two-deleter race.
            patch.object(
                cache,
                "_enforce_cache_budget",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            await umount_started.wait()
            lease = asyncio.create_task(take_lease())
            await lease_waiting_for_key.wait()
            assert cache._runtime[cache_key].users == 2
            finish_umount.set()
            evicted, _ = await asyncio.gather(eviction, lease)

        assert evicted == RegistryArtifactEviction(retired=True, reclaimed=True)
        # The lease waited for the eviction and re-materialized the entry.
        assert remounts == [cache_key]
        assert leased_paths == [paths.squashfs_mount_dir]
        assert leased_path_exists == [True]
        assert (paths.squashfs_mount_dir / "module.py").read_text() == "VALUE = 1"
        assert kill_group.call_count == 2
        kill_group.assert_called_with(umount_process.pid, signal.SIGKILL)

    @pytest.mark.anyio
    async def test_builtin_artifact_is_exempt_from_cache_accounting(
        self, temp_cache_dir, monkeypatch: pytest.MonkeyPatch
    ):
        """The bundled builtin registry is never a cache entry."""
        version = "1.2.3"
        site_packages = temp_cache_dir / "venv" / "site-packages"
        package_dir = site_packages / "tracecat_registry"
        package_dir.mkdir(parents=True)
        package_file = package_dir / "__init__.py"
        package_file.write_text("")

        monkeypatch.setattr(tracecat_registry, "__version__", version)
        monkeypatch.setattr(tracecat_registry, "__file__", str(package_file))
        monkeypatch.setattr(
            "tracecat.executor.registry_artifact_materialization.sysconfig.get_path",
            lambda name: str(site_packages) if name == "purelib" else None,
        )

        cache = RegistryArtifactCache(temp_cache_dir)

        with patch.object(
            cache,
            "_enforce_cache_budget",
            new_callable=AsyncMock,
        ) as enforce_cache_budget:
            async with cache.lease([bundled_builtin_registry_uri(version)]) as paths:
                assert paths == [site_packages.resolve()]
                assert cache._runtime == {}

        enforce_cache_budget.assert_not_awaited()
