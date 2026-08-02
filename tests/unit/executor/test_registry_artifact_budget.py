"""Registry artifact byte admission and budget convergence tests."""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    RegistryArtifactCacheCapacityError,
    RegistryArtifactEviction,
    RegistryArtifactMaterializationContext,
    SquashfsArtifact,
    TarballArtifact,
    _allocated_stat_size,
    _delete_cache_path,
    _directory_footprint,
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

    def test_allocated_stat_size_uses_filesystem_blocks(self) -> None:
        file_stat = MagicMock(spec=os.stat_result)
        file_stat.st_blocks = 7
        file_stat.st_size = 1

        assert _allocated_stat_size(file_stat, allocation_unit=512) == 7 * 512

    def test_allocated_stat_size_charges_zero_block_inode(self) -> None:
        file_stat = MagicMock(spec=os.stat_result)
        file_stat.st_blocks = 0
        file_stat.st_size = 0

        assert _allocated_stat_size(file_stat, allocation_unit=4096) == 4096

    def test_directory_footprint_includes_directory_inodes(
        self,
        temp_cache_dir: Path,
    ) -> None:
        nested = temp_cache_dir / "nested"
        nested.mkdir()
        (nested / "module.py").write_text("x")

        with patch(
            "tracecat.executor.registry_artifact_storage._allocated_stat_size",
            return_value=4096,
        ) as allocated_size:
            assert _directory_footprint(temp_cache_dir) == 3 * 4096

        assert allocated_size.call_count == 3

    def test_directory_footprint_counts_hard_linked_inode_once(
        self,
        temp_cache_dir: Path,
    ) -> None:
        payload = temp_cache_dir / "payload"
        payload.write_text("x")
        os.link(payload, temp_cache_dir / "payload-link")

        with patch(
            "tracecat.executor.registry_artifact_storage._allocated_stat_size",
            return_value=4096,
        ) as allocated_size:
            assert _directory_footprint(temp_cache_dir) == 2 * 4096

        assert allocated_size.call_count == 2

    def test_directory_footprint_prunes_directory_contents(
        self,
        temp_cache_dir: Path,
    ) -> None:
        mounted = temp_cache_dir / "mount"
        mounted.mkdir()
        (mounted / "module.py").write_text("x")

        with patch(
            "tracecat.executor.registry_artifact_storage._allocated_stat_size",
            return_value=4096,
        ) as allocated_size:
            assert (
                _directory_footprint(
                    temp_cache_dir,
                    pruned_directories=(mounted,),
                )
                == 2 * 4096
            )

        assert allocated_size.call_count == 2

    @pytest.mark.anyio
    async def test_admission_rounds_download_reservation_to_allocation_unit(
        self,
        temp_cache_dir: Path,
    ) -> None:
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(MAX_BYTES_CONFIG, 8192),
            patch(
                "tracecat.executor.registry_artifact_storage."
                "_filesystem_allocation_unit",
                return_value=4096,
            ),
            patch.object(
                cache,
                "_ensure_cache_capacity",
                new_callable=AsyncMock,
            ) as ensure_capacity,
        ):
            admission = cache._admission_for("new")
            assert admission is not None
            await admission.ensure_capacity(1)

        ensure_capacity.assert_awaited_once_with(
            additional_bytes=4096,
            protected_key="new",
            max_bytes=8192,
        )

    @pytest.mark.anyio
    async def test_admission_reuses_verified_capacity_headroom(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """Chunked downloads rescan only after consuming known free bytes."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(MAX_BYTES_CONFIG, 100),
            patch(
                "tracecat.executor.registry_artifact_storage."
                "_filesystem_allocation_unit",
                return_value=1,
            ),
            patch.object(
                cache,
                "_ensure_cache_capacity",
                new_callable=AsyncMock,
                side_effect=[10, 6],
            ) as ensure_capacity,
        ):
            admission = cache._admission_for("new")
            assert admission is not None
            await admission.ensure_capacity(4)
            await admission.ensure_capacity(6)
            await admission.ensure_capacity(5)
            await admission.ensure_capacity(2)

        assert ensure_capacity.await_args_list == [
            call(additional_bytes=4, protected_key="new", max_bytes=100),
            call(additional_bytes=5, protected_key="new", max_bytes=100),
        ]

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
        extracted_size = 74  # File, root, and directory entry at unit size 1.
        max_bytes = len(payload) + extracted_size
        capacity_checked = False

        async def download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            max_bytes: int,
            ensure_capacity: Callable[[int], Awaitable[None]],
            defer_cleanup: Callable[[Path], None],
            redact_log_identifiers: bool,
        ) -> int:
            del key, bucket, defer_cleanup
            assert redact_log_identifiers is True
            nonlocal capacity_checked
            assert max_bytes == len(payload) + extracted_size
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
    async def test_impossible_tarball_reservation_preserves_warm_entry(
        self, temp_cache_dir: Path
    ) -> None:
        """Impossible extraction cannot evict warm entries before rejection."""
        cache = RegistryArtifactCache(temp_cache_dir)
        warm = write_image_entry(
            temp_cache_dir,
            "warm",
            size=64,
            mtime=100.0,
        )
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
            defer_cleanup: Callable[[Path], None],
            redact_log_identifiers: bool,
        ) -> int:
            del key, bucket, defer_cleanup
            assert redact_log_identifiers is True
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
            patch.object(
                cache,
                "_evict_entry",
                wraps=cache._evict_entry,
            ) as evict_entry,
        ):
            with pytest.raises(RegistryArtifactCacheCapacityError) as raised:
                async with cache.lease([artifact_uri]):
                    pass

        assert raised.value.additional_bytes == 4138
        assert raised.value.max_bytes == max_bytes
        extract.assert_not_awaited()
        evict_entry.assert_not_awaited()
        assert warm.is_file()
        assert not cache._paths_for(cache_key).entry_dir.exists()
        assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())

    @pytest.mark.anyio
    async def test_failed_squashfs_bytes_do_not_block_tarball_fallback(
        self, temp_cache_dir: Path
    ) -> None:
        """An unusable image cannot consume the tarball fallback's budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        payload = tarball_payload(size=32)
        extracted_size = 74  # File, root, and directory entry at unit size 1.
        max_bytes = len(payload) + extracted_size

        async def fail_after_squashfs_download(
            self: SquashfsArtifact,
            ctx: RegistryArtifactMaterializationContext,
        ) -> list[Path]:
            del self
            assert ctx.admission is not None
            await ctx.admission.ensure_capacity(max_bytes)
            ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
            ctx.paths.squashfs_image_path.write_bytes(b"x" * max_bytes)
            raise RuntimeError("unusable SquashFS image")

        async def download_tarball(
            self: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
            path: Path,
        ) -> None:
            del self
            assert ctx.admission is not None
            await ctx.admission.ensure_capacity(len(payload))
            path.write_bytes(payload)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch.object(
                SquashfsArtifact,
                "materialize",
                fail_after_squashfs_download,
            ),
            patch.object(TarballArtifact, "download", download_tarball),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [
                    cache._paths_for(cache_key).tarball_target_dir
                ]

        paths = cache._paths_for(cache_key)
        assert not paths.squashfs_image_path.exists()
        assert (paths.tarball_target_dir / "module.py").read_bytes() == b"x" * 32

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
    async def test_undeletable_trash_does_not_evict_warm_entries_for_admission(
        self, temp_cache_dir
    ):
        """Unreclaimed trash blocks an oversized write without extra eviction."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        warm = write_image_entry(temp_cache_dir, "warm", size=32, mtime=100.0)
        stale_trash = cache.trash_dir / "stale"
        stale_trash.mkdir(parents=True)
        (stale_trash / "image.squashfs").write_bytes(b"x" * 32)
        real_delete = _delete_cache_path

        def fail_stale_trash(path: Path) -> bool:
            if path == stale_trash:
                return False
            return real_delete(path)

        with patch(
            "tracecat.executor.registry_artifact_storage._delete_cache_path",
            side_effect=fail_stale_trash,
        ):
            with pytest.raises(RegistryArtifactCacheCapacityError) as raised:
                await cache._ensure_cache_capacity(
                    additional_bytes=16,
                    protected_key="new",
                    max_bytes=64,
                )

        assert raised.value.current_bytes == 64
        assert warm.exists()
        assert stale_trash.exists()

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
    async def test_releasing_a_mutable_cache_hit_counts_unknown_entry_growth(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """Direct consumers cannot grow unknown entry paths outside the budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/mutable-cached.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = write_tarball_entry(temp_cache_dir, cache_key)
        entry_dir = target_dir.parent
        initial_size = cache._measure_entry(cache_key).size_bytes
        cache._budget_dirty = False

        with (
            patch(MAX_ENTRIES_CONFIG, 10),
            patch(MAX_BYTES_CONFIG, initial_size),
            patch.object(
                cache,
                "_scan_cache_entries",
                wraps=cache._scan_cache_entries,
            ) as scan_cache_entries,
        ):
            async with cache.lease(
                [artifact_uri],
                paths_may_be_modified=True,
            ) as registry_paths:
                assert registry_paths == [target_dir]
                (entry_dir / "action-output.bin").write_bytes(b"x" * 4096)

        assert scan_cache_entries.call_count == 1
        assert not entry_dir.exists()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_successful_cold_admission_skips_release_rescan(self, temp_cache_dir):
        """A successful protected pass consumes the materialization dirty signal."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/new-with-one-budget-pass.tar.gz"

        async def mock_download(self, ctx, path):
            del self, ctx
            path.write_bytes(tarball_payload(size=1))

        with (
            patch(MAX_ENTRIES_CONFIG, 10),
            patch(MAX_BYTES_CONFIG, 0),
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(
                cache,
                "_scan_cache_entries",
                wraps=cache._scan_cache_entries,
            ) as scan_cache_entries,
        ):
            async with cache.lease([artifact_uri]):
                assert cache._budget_dirty is False

        assert scan_cache_entries.call_count == 1
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
    async def test_non_final_release_defers_retry_while_cache_stays_over_budget(
        self, temp_cache_dir
    ):
        """A non-final release defers rescanning until an entry becomes idle."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/pinned.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        write_image_entry(temp_cache_dir, cache_key, size=4096, mtime=100.0)
        write_tarball_entry(temp_cache_dir, cache_key)
        cache._budget_dirty = True
        # A second holder keeps the entry pinned past the inner lease.
        cache._acquire_lease(cache_key)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 1),
            patch.object(
                cache,
                "_scan_cache_entries",
                side_effect=AssertionError("non-final releases must not rescan"),
            ),
        ):
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

    @pytest.mark.parametrize("operation", ["budget", "admission"])
    @pytest.mark.anyio
    async def test_cancelled_cleanup_rejoins_workers_before_releasing_locks(
        self,
        temp_cache_dir: Path,
        operation: Literal["budget", "admission"],
    ) -> None:
        """Cache-wide locks outlive repeatedly cancelled cleanup workers."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cleanup_started = threading.Event()
        cleanup_release = threading.Event()
        cleanup_finished = threading.Event()

        def blocking_clear(work_dir: Path) -> bool:
            assert work_dir == cache.trash_dir
            cleanup_started.set()
            cleanup_release.wait(timeout=5)
            cleanup_finished.set()
            return True

        async def run_operation() -> object:
            if operation == "budget":
                return await cache._enforce_cache_budget()
            await cache._ensure_cache_capacity(
                additional_bytes=0,
                protected_key="pending",
                max_bytes=1,
            )
            return None

        with (
            patch.object(cache, "_clear_work_dir", side_effect=blocking_clear),
            patch.object(
                cache,
                "_retry_deferred_staging_cleanup",
                return_value=True,
            ),
        ):
            running = asyncio.create_task(run_operation())
            try:
                assert await asyncio.to_thread(cleanup_started.wait, 1)
                running.cancel()
                await asyncio.sleep(0)
                running.cancel()
                await asyncio.sleep(0)

                assert not running.done()
                assert cache._budget_lock.locked()
                assert cache._admission_lock.locked() is (operation == "budget")
            finally:
                cleanup_release.set()

            with pytest.raises(asyncio.CancelledError):
                await running

        assert cleanup_finished.is_set()
        assert not cache._budget_lock.locked()
        assert not cache._admission_lock.locked()

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
        process.pid = 999_999_999
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        async def mock_umount(*args, **kwargs):
            assert kwargs["start_new_session"] is True
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
            patch.object(
                asyncio,
                "create_subprocess_exec",
                side_effect=mock_umount,
            ),
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [paths.squashfs_mount_dir]
                assert paths.squashfs_mount_dir in mounted

            assert paths.squashfs_mount_dir not in mounted

        assert paths.squashfs_image_path.read_bytes() == b"squashfs"
        kill_group.assert_called_once_with(process.pid, signal.SIGKILL)
        assert paths.squashfs_mount_dir.is_dir()

    @pytest.mark.anyio
    async def test_failed_final_release_unmount_retries_on_later_cleanup(
        self, temp_cache_dir
    ):
        """A transient unmount failure is retried after another lease release."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/retry-unmount.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}
        retry_uri = "s3://bucket/path/retry-trigger.tar.gz"
        retry_key = compute_registry_artifact_cache_key(retry_uri)
        write_tarball_entry(temp_cache_dir, retry_key)
        attempts: list[Path] = []

        async def flaky_unmount(mount_dir: Path) -> bool:
            attempts.append(mount_dir)
            if len(attempts) == 1:
                return False
            mounted.discard(mount_dir)
            return True

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in mounted,
            ),
            patch.object(cache, "_unmount", side_effect=flaky_unmount),
        ):
            async with cache.lease([artifact_uri]):
                pass

            assert cache._failed_unmounts == {cache_key}
            assert paths.squashfs_mount_dir in mounted

            async with cache.lease([retry_uri]):
                pass

        assert attempts == [paths.squashfs_mount_dir, paths.squashfs_mount_dir]
        assert cache._failed_unmounts == set()
        assert paths.squashfs_mount_dir not in mounted

    @pytest.mark.anyio
    async def test_concurrent_budget_passes_only_evict_once(self, temp_cache_dir):
        """A waiting budget pass must re-scan after the active pass evicts."""
        cache = RegistryArtifactCache(temp_cache_dir)

        class ObservedLock(asyncio.Lock):
            def __init__(self) -> None:
                super().__init__()
                self.second_acquire_started = asyncio.Event()
                self._acquire_attempts = 0

            async def acquire(self) -> Literal[True]:
                self._acquire_attempts += 1
                if self._acquire_attempts == 2:
                    self.second_acquire_started.set()
                return await super().acquire()

        admission_lock = ObservedLock()
        cache._admission_lock = admission_lock
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
            await admission_lock.second_acquire_started.wait()
            assert not second_scan_started.is_set()

            release_first_scan.set()
            await asyncio.wait_for(eviction_started.wait(), timeout=1)
            assert not second_scan_started.is_set()
            finish_eviction.set()
            assert await asyncio.to_thread(second_scan_started.wait, 1)
            release_second_scan.set()
            await asyncio.gather(first_pass, second_pass)

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
