"""Compare warm lease overhead, excluding downloads and action execution.

Run: uv run pytest --confcutdir=tests/unit \
    tests/unit/test_registry_cache_benchmark.py --benchmark-enable

The baseline reproduces the old release-time dirty flag. Normal CI also runs
these cases and asserts zero recursive scans for the fixed path. Timing is
informational; no machine-dependent latency threshold is asserted.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from tracecat.executor import registry_artifact_storage as storage
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    compute_registry_artifact_cache_key,
)


@pytest.mark.parametrize("file_count", [100, 10_000])
@pytest.mark.parametrize("legacy_release", [True, False], ids=["before", "after"])
def test_warm_lease_scaling(
    benchmark: BenchmarkFixture,
    tmp_path: Path,
    file_count: int,
    legacy_release: bool,
) -> None:
    uri = "s3://test-registry/environments/benchmark.tar.gz"
    key = compute_registry_artifact_cache_key(uri)
    packages = tmp_path / "entries" / key / "tarball"
    packages.mkdir(parents=True)
    for index in range(file_count):
        (packages / f"module_{index}.py").write_text("VALUE = 1\n")

    cache = RegistryArtifactCache(tmp_path)

    async def warm_action() -> None:
        async with cache.lease([uri]) as paths:
            assert paths == [packages]
            if legacy_release:
                # Previously every direct action invalidated the budget here,
                # even when it hadn't modified any registry files.
                cache._budget_dirty = True

    with asyncio.Runner() as runner:
        runner.run(cache.ensure_swept())
        runner.run(warm_action())
        with patch.object(
            storage, "_directory_footprint", wraps=storage._directory_footprint
        ) as footprint:
            benchmark.pedantic(
                lambda: runner.run(warm_action()), rounds=5, iterations=1
            )
            benchmark.extra_info["recursive_footprint_calls"] = footprint.call_count
            benchmark.extra_info["cache_files"] = file_count
            if legacy_release:
                assert footprint.call_count > 0
            else:
                assert footprint.call_count == 0
