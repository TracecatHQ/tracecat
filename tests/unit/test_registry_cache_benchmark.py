"""Compare warm lease overhead, excluding downloads and action execution.

Run: uv run pytest --confcutdir=tests/unit \
    tests/unit/test_registry_cache_benchmark.py --benchmark-enable

Normal CI still checks the deterministic regression: zero recursive footprint
calls for immutable remote leases, versus a full scan for writable local leases.
Timing is informational; no machine-dependent latency threshold is asserted.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from tracecat.executor import registry_artifact_storage as storage
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    compute_registry_artifact_cache_key,
)
from tracecat.executor.registry_cache_manager import (
    RegistryCacheClient,
    RegistryCacheManager,
)


@pytest.mark.parametrize("file_count", [100, 10_000])
@pytest.mark.parametrize(
    "remote", [False, True], ids=["writable-local", "immutable-remote"]
)
def test_warm_lease_scaling(
    benchmark: BenchmarkFixture,
    file_count: int,
    remote: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = "s3://test-registry/environments/benchmark.tar.gz"
    key = compute_registry_artifact_cache_key(uri)
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="rc-bench-") as directory:
        root = Path(directory)
        packages = root / "entries" / key / "tarball"
        packages.mkdir(parents=True)
        for index in range(file_count):
            (packages / f"module_{index}.py").write_text("VALUE = 1\n")

        # Unit benchmark models the RO mount; the Linux container smoke test
        # separately exercises the actual kernel-enforced mount boundary.
        actual_statvfs = os.statvfs

        def readonly_statvfs(path: str | os.PathLike[str] | int) -> os.statvfs_result:
            fields = list(actual_statvfs(path))
            fields[8] |= os.ST_RDONLY
            return os.statvfs_result(fields)

        monkeypatch.setattr(os, "statvfs", readonly_statvfs)
        manager = RegistryCacheManager(root)
        client = RegistryCacheClient(root) if remote else RegistryArtifactCache(root)

        async def warm_action() -> None:
            async with client.lease([uri], paths_may_be_modified=True) as paths:
                assert paths == [packages]

        with asyncio.Runner() as runner:
            server = runner.run(manager.start()) if remote else None
            try:
                runner.run(warm_action())
                with patch.object(
                    storage, "_directory_footprint", wraps=storage._directory_footprint
                ) as footprint:
                    benchmark.pedantic(
                        lambda: runner.run(warm_action()), rounds=5, iterations=1
                    )
                    benchmark.extra_info["recursive_footprint_calls"] = (
                        footprint.call_count
                    )
                    benchmark.extra_info["cache_files"] = file_count
                    if remote:
                        assert footprint.call_count == 0
                    else:
                        assert footprint.call_count > 0
            finally:
                if server is not None:
                    server.close()
                    runner.run(server.wait_closed())
                if manager._lock is not None:
                    manager._lock.close()
