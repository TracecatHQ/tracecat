"""Immutable cache accounting and real socket lease lifecycle regressions."""

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from tracecat.executor import registry_artifact_storage as storage
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    TarballArtifact,
    compute_registry_artifact_cache_key,
)
from tracecat.executor.registry_cache_manager import (
    SOCKET_NAME,
    LeaseRequest,
    LeaseResponse,
    RegistryCacheClient,
    RegistryCacheManager,
)

URI = "s3://test-registry/environments/example.tar.gz"
KEY = compute_registry_artifact_cache_key(URI)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def cache_dir() -> Iterator[Path]:
    # Unix socket paths have a small limit, including macOS pytest's temp root.
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="rc-") as directory:
        root = Path(directory)
        target = root / "entries" / KEY / "tarball"
        target.mkdir(parents=True)
        (target / "example.py").write_text("VALUE = 42\n")
        yield root


@pytest.fixture
def readonly(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.statvfs

    def statvfs(path: str | os.PathLike[str] | int) -> os.statvfs_result:
        fields = list(original(path))
        fields[8] |= os.ST_RDONLY
        return os.statvfs_result(fields)

    monkeypatch.setattr(os, "statvfs", statvfs)


@pytest.fixture
async def manager(cache_dir: Path) -> AsyncGenerator[RegistryCacheManager]:
    manager = RegistryCacheManager(cache_dir)
    async with await manager.start():
        yield manager
    # Tests may intentionally strand a lease; the service never does this on EOF.
    for marker in list(manager.leases):
        await manager.release(marker)
    if manager._lock is not None:
        manager._lock.close()


@pytest.mark.anyio
async def test_warm_actions_do_not_scan_and_pins_block_eviction(
    manager: RegistryCacheManager, cache_dir: Path, readonly: None
) -> None:
    client = RegistryCacheClient(cache_dir)
    await client.ensure_swept()
    with patch.object(
        manager.cache, "_scan_cache_snapshot", side_effect=AssertionError("warm scan")
    ):
        for _ in range(20):
            async with client.lease([URI], paths_may_be_modified=True) as paths:
                assert (paths[0] / "example.py").read_text() == "VALUE = 42\n"
                assert manager.cache._refcount(KEY) == 1
                eviction = await manager.cache._evict_entry(KEY)
                assert not eviction.retired
            assert manager.cache._refcount(KEY) == 0
    assert not manager.leases
    assert not list(manager.lease_dir.iterdir())


@pytest.mark.anyio
async def test_reject_writable_executor_mount(cache_dir: Path) -> None:
    client = RegistryCacheClient(cache_dir)
    with pytest.raises(RuntimeError, match="read-only mount"):
        await client.ensure_swept()
    with pytest.raises(RuntimeError, match="read-only mount"):
        async with client.lease([URI]):
            pytest.fail("Writable artifacts must not reach the action")


@pytest.mark.anyio
async def test_disconnect_retains_pin_and_blocks_recovery(
    manager: RegistryCacheManager, cache_dir: Path
) -> None:
    reader, writer = await asyncio.open_unix_connection(cache_dir / SOCKET_NAME)
    writer.write(LeaseRequest(artifact_uris=[URI]).model_dump_json().encode() + b"\n")
    await writer.drain()
    assert LeaseResponse.model_validate_json(await reader.readline()).paths
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0)
    assert manager.cache._refcount(KEY) == 1
    assert len(list(manager.lease_dir.iterdir())) == 1
    # Simulate the original manager process having died without releasing pins.
    assert manager._lock is not None
    manager._lock.close()
    restarted = RegistryCacheManager(cache_dir)
    try:
        with pytest.raises(RuntimeError, match="unreleased leases"):
            await restarted.start()
    finally:
        if restarted._lock is not None:
            restarted._lock.close()


@pytest.mark.anyio
async def test_cancellation_during_acquire_releases_after_acquire_finishes(
    manager: RegistryCacheManager, cache_dir: Path, readonly: None
) -> None:
    entered = asyncio.Event()
    proceed = asyncio.Event()
    original = manager.cache._acquire_artifact

    async def delayed(lease):
        paths = await original(lease)
        entered.set()
        await proceed.wait()
        return paths

    async def action() -> None:
        async with RegistryCacheClient(cache_dir).lease([URI]):
            pytest.fail("Cancelled acquisition must not start an action")

    with patch.object(manager.cache, "_acquire_artifact", side_effect=delayed):
        task = asyncio.create_task(action())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert manager.cache._refcount(KEY) == 0
    assert not manager.leases


@pytest.mark.anyio
async def test_manager_cannot_start_twice(
    manager: RegistryCacheManager, cache_dir: Path
) -> None:
    duplicate = RegistryCacheManager(cache_dir)
    try:
        with pytest.raises(BlockingIOError):
            await duplicate.start()
    finally:
        if duplicate._lock is not None:
            duplicate._lock.close()


@pytest.mark.anyio
async def test_published_entries_are_measured_once(cache_dir: Path) -> None:
    cache = RegistryArtifactCache(cache_dir, immutable=True)
    await cache.ensure_swept()
    measured = cache._measure_entry(KEY)
    with patch(
        "tracecat.executor.registry_artifact_storage._directory_footprint",
        side_effect=AssertionError("published entry scanned twice"),
    ):
        assert cache._measure_entry(KEY).size_bytes == measured.size_bytes
    # Only manager-owned materialization invalidates a recorded size.
    cache._materializing.add(KEY)
    (cache_dir / "entries" / KEY / "tarball" / "large.bin").write_bytes(b"x" * 16384)
    assert cache._measure_entry(KEY).size_bytes > measured.size_bytes


@pytest.mark.anyio
async def test_immutable_cache_rejects_mutable_lease(cache_dir: Path) -> None:
    cache = RegistryArtifactCache(cache_dir, immutable=True)
    with pytest.raises(ValueError, match="writable leases"):
        async with cache.lease([URI], paths_may_be_modified=True):
            pytest.fail("Immutable accounting must not accept writable consumers")


@pytest.mark.anyio
async def test_cold_admission_reuses_published_sizes(
    manager: RegistryCacheManager, cache_dir: Path, readonly: None
) -> None:
    second_uri = "s3://test-registry/environments/second.tar.gz"
    artifact = TarballArtifact(
        uri=second_uri, cache_key=compute_registry_artifact_cache_key(second_uri)
    )
    original = storage._directory_footprint
    scanned: list[Path] = []

    def measure(path, **kwargs):
        scanned.append(path)
        return original(path, **kwargs)

    async def materialize(ctx):
        assert ctx.admission is not None
        await ctx.admission.ensure_capacity(65536)
        target = ctx.paths.tarball_target_dir
        target.mkdir(parents=True)
        (target / "second.py").write_text("VALUE = 2\n")
        return [target]

    with (
        patch.object(manager.cache, "_artifact_candidates", return_value=[artifact]),
        patch.object(TarballArtifact, "materialize", side_effect=materialize),
        patch.object(storage, "_directory_footprint", side_effect=measure),
    ):
        async with RegistryCacheClient(cache_dir).lease([second_uri]) as paths:
            assert (paths[0] / "second.py").read_text() == "VALUE = 2\n"
    assert cache_dir / "entries" / KEY not in scanned
    assert manager.cache._refcount(compute_registry_artifact_cache_key(second_uri)) == 0


@pytest.mark.anyio
async def test_failed_acquisition_does_not_strand_recovery_marker(
    manager: RegistryCacheManager, cache_dir: Path, readonly: None
) -> None:
    with patch.object(
        manager.cache, "_acquire_artifact", side_effect=ValueError("test failure")
    ):
        with pytest.raises(RuntimeError, match="acquisition failed"):
            async with RegistryCacheClient(cache_dir).lease([URI]):
                pytest.fail("Failed acquisition must not start an action")
    assert not manager.leases
    assert not list(manager.lease_dir.iterdir())
