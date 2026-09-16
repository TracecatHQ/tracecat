"""Concurrency regressions for deferred registry cache accounting."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.executor import registry_artifact_storage as storage
from tracecat.executor.registry_artifact_budget import RegistryArtifactCacheSnapshot
from tracecat.executor.registry_artifact_storage import (
    RegistryArtifactMaterializationContext,
)
from tracecat.executor.registry_artifacts import (
    RegistryArtifact,
    RegistryArtifactCache,
    compute_registry_artifact_cache_key,
)

WARM_URI = "s3://test-bucket/warm.tar.gz"


def seed_entry(cache: RegistryArtifactCache, uri: str) -> Path:
    target = cache._paths_for(
        compute_registry_artifact_cache_key(uri)
    ).tarball_target_dir
    target.mkdir(parents=True)
    (target / "module.py").write_text("VALUE = 1")
    return target


@pytest.mark.anyio
async def test_warm_releases_coalesce_without_scanning(tmp_path: Path) -> None:
    cache = RegistryArtifactCache(tmp_path)
    await cache.ensure_swept()
    seed_entry(cache, WARM_URI)
    try:
        with patch.object(cache, "_scan_cache_snapshot") as scan:
            async with cache.lease([WARM_URI], paths_may_be_modified=True):
                pass
            maintenance = cache._budget_maintenance_task
            assert maintenance is not None
            for _ in range(20):
                async with cache.lease([WARM_URI], paths_may_be_modified=True):
                    pass
                assert cache._budget_maintenance_task is maintenance
            scan.assert_not_called()
            assert cache._refcount(compute_registry_artifact_cache_key(WARM_URI)) == 0
            await cache.shutdown()
            scan.assert_not_called()
    finally:
        await cache.shutdown()


@pytest.mark.anyio
async def test_warm_action_finishes_during_blocked_scan(tmp_path: Path) -> None:
    cache = RegistryArtifactCache(tmp_path)
    await cache.ensure_swept()
    seed_entry(cache, WARM_URI)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finish_scan = threading.Event()
    original_scan = cache._scan_cache_snapshot

    def blocked_scan() -> RegistryArtifactCacheSnapshot:
        loop.call_soon_threadsafe(started.set)
        if not finish_scan.wait(timeout=5):
            raise TimeoutError("test did not release scanner")
        return original_scan()

    async def action() -> None:
        async with cache.lease([WARM_URI], paths_may_be_modified=True):
            pass

    try:
        with (
            patch.object(storage, "BUDGET_MAINTENANCE_INTERVAL_SECONDS", 0),
            patch.object(cache, "_scan_cache_snapshot", side_effect=blocked_scan),
        ):
            await action()
            await asyncio.wait_for(started.wait(), timeout=2)
            maintenance = cache._budget_maintenance_task
            assert maintenance is not None
            assert not maintenance.done()
            assert not cache._admission_lock.locked()
            await asyncio.wait_for(action(), timeout=2)
            assert not maintenance.done()
            finish_scan.set()
            await asyncio.wait_for(asyncio.shield(maintenance), timeout=2)
    finally:
        finish_scan.set()
        await cache.shutdown()


@pytest.mark.anyio
async def test_cold_admission_invalidates_background_snapshot(tmp_path: Path) -> None:
    cache = RegistryArtifactCache(tmp_path)
    await cache.ensure_swept()
    seed_entry(cache, WARM_URI)
    snapshot = cache._scan_cache_snapshot()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finish_scan = threading.Event()
    original_scan = cache._scan_cache_snapshot
    first_scan = True

    def blocked_first_scan() -> RegistryArtifactCacheSnapshot:
        nonlocal first_scan
        if not first_scan:
            return original_scan()
        first_scan = False
        loop.call_soon_threadsafe(started.set)
        if not finish_scan.wait(timeout=5):
            raise TimeoutError("test did not release scanner")
        return snapshot

    async def materialize(
        ctx: RegistryArtifactMaterializationContext,
        candidates: list[RegistryArtifact],
    ) -> list[Path]:
        del candidates
        ctx.paths.tarball_target_dir.mkdir(parents=True)
        (ctx.paths.tarball_target_dir / "module.py").write_text("VALUE = 2")
        return [ctx.paths.tarball_target_dir]

    try:
        with (
            patch.object(cache, "_scan_cache_snapshot", side_effect=blocked_first_scan),
            patch.object(cache, "_artifact_candidates", new=AsyncMock(return_value=[])),
            patch.object(cache, "_materialize_candidates", side_effect=materialize),
            patch.object(
                cache, "_enforce_cache_snapshot", wraps=cache._enforce_cache_snapshot
            ) as enforce,
        ):
            background = asyncio.create_task(cache._enforce_background_cache_budget())
            await asyncio.wait_for(started.wait(), timeout=2)
            async with asyncio.timeout(2):
                async with cache.lease(["s3://test-bucket/cold.tar.gz"]):
                    pass
            cold_enforcements = enforce.await_count
            assert cold_enforcements == 1
            finish_scan.set()
            assert await asyncio.wait_for(background, timeout=2) is False
            assert enforce.await_count == cold_enforcements
    finally:
        finish_scan.set()
        await cache.shutdown()


@pytest.mark.anyio
async def test_background_scan_failure_retries_without_another_action(
    tmp_path: Path,
) -> None:
    cache = RegistryArtifactCache(tmp_path)
    await cache.ensure_swept()
    seed_entry(cache, WARM_URI)
    snapshot = cache._scan_cache_snapshot()
    try:
        with (
            patch.object(storage, "BUDGET_MAINTENANCE_INTERVAL_SECONDS", 0),
            patch.object(
                cache, "_scan_cache_snapshot", side_effect=[PermissionError(), snapshot]
            ) as scan,
        ):
            async with cache.lease([WARM_URI], paths_may_be_modified=True):
                pass
            maintenance = cache._budget_maintenance_task
            assert maintenance is not None
            await asyncio.wait_for(asyncio.shield(maintenance), timeout=2)
            assert scan.call_count == 2
            assert not cache._budget_dirty
    finally:
        await cache.shutdown()


@pytest.mark.anyio
async def test_shutdown_joins_inflight_scan(tmp_path: Path) -> None:
    cache = RegistryArtifactCache(tmp_path)
    await cache.ensure_swept()
    seed_entry(cache, WARM_URI)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finish_scan = threading.Event()
    original_scan = cache._scan_cache_snapshot

    def blocked_scan() -> RegistryArtifactCacheSnapshot:
        loop.call_soon_threadsafe(started.set)
        if not finish_scan.wait(timeout=5):
            raise TimeoutError("test did not release scanner")
        return original_scan()

    try:
        with (
            patch.object(storage, "BUDGET_MAINTENANCE_INTERVAL_SECONDS", 0),
            patch.object(cache, "_scan_cache_snapshot", side_effect=blocked_scan),
        ):
            async with cache.lease([WARM_URI], paths_may_be_modified=True):
                pass
            await asyncio.wait_for(started.wait(), timeout=2)
            shutdown = asyncio.create_task(cache.shutdown())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not shutdown.done()
            finish_scan.set()
            await asyncio.wait_for(shutdown, timeout=2)
            assert cache._budget_maintenance_task is None
            assert cache._budget_dirty
    finally:
        finish_scan.set()
        await cache.shutdown()


@pytest.mark.anyio
async def test_background_accounting_detects_growth_in_another_entry(
    tmp_path: Path,
) -> None:
    cache = RegistryArtifactCache(tmp_path)
    await cache.ensure_swept()
    warm = seed_entry(cache, WARM_URI)
    other = seed_entry(cache, "s3://test-bucket/other.tar.gz")
    budget = cache._scan_cache_snapshot().total_bytes
    try:
        with (
            patch.object(storage, "BUDGET_MAINTENANCE_INTERVAL_SECONDS", 0),
            patch.object(
                storage.config, "TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES", budget
            ),
        ):
            async with cache.lease([WARM_URI], paths_may_be_modified=True):
                (other / "runtime-output.bin").write_bytes(b"x" * 8192)
            maintenance = cache._budget_maintenance_task
            assert maintenance is not None
            await asyncio.wait_for(asyncio.shield(maintenance), timeout=2)
            assert warm.exists()
            assert not other.exists()
            assert cache._scan_cache_snapshot().total_bytes <= budget
    finally:
        await cache.shutdown()
