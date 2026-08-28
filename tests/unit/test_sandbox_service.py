"""Tests for SandboxService promotion and cleanup concurrency behavior."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_mock

from tracecat.sandbox.service import SandboxService
from tracecat.sandbox.types import SandboxResult


@pytest.mark.anyio
async def test_install_packages_rejoins_copy_before_cleanup_on_cancellation(
    tmp_path: Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Cancellation during package promotion must rejoin the copy thread.

    Cancelling an ``asyncio.to_thread`` await does not stop the worker thread.
    If the promotion flow raced ahead to cleanup on cancellation, the
    finally-block rmtree (and the caller's job-dir removal) would race the
    still-running copy and strand a partial .tmp tree in the shared package
    cache. The promotion must join the copy thread before removing anything.
    """
    cache_dir = tmp_path / "cache"
    service = SandboxService(cache_dir=str(cache_dir))

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    site_packages = job_dir / "cache" / "site-packages"
    site_packages.mkdir(parents=True)
    (site_packages / "pkg.py").write_text("x")

    class _FakeExecutor:
        async def execute_install(
            self,
            job_dir: Path,  # noqa: ARG002
            cache_key: str,  # noqa: ARG002
            timeout_seconds: int,  # noqa: ARG002
        ) -> Any:
            return SandboxResult(success=True, exit_code=0)

    # The nsjail_executor property lazily builds the real executor; seed the
    # backing cache field directly so no jail is needed for this test.
    service._nsjail_executor = cast(Any, _FakeExecutor())

    copy_started = asyncio.Event()
    copy_finished = asyncio.Event()
    events: list[str] = []

    def slow_copy(
        site_packages_src: Path,  # noqa: ARG001
        temp_dest: Path,
        **kwargs: object,
    ) -> bool:
        copy_started.set()
        time.sleep(0.2)  # Simulate a large tree copy.
        temp_dest.mkdir(parents=True, exist_ok=True)
        (temp_dest / "pkg.py").write_text("copied")
        copy_finished.set()
        events.append("copy_finished")
        return True

    # Capture the original BEFORE patching: service.shutil IS the global
    # module, so slow_rmtree must not call the (patched) shutil.rmtree.
    _original_rmtree = shutil.rmtree

    def slow_rmtree(path: Path, **kwargs: object) -> None:
        events.append("cleanup_started")
        _original_rmtree(path, **kwargs)  # type: ignore[arg-type]

    mocker.patch(
        "tracecat.sandbox.service.copy_tree_without_following_symlinks",
        slow_copy,
    )
    mocker.patch("tracecat.sandbox.service.shutil.rmtree", slow_rmtree)

    task = asyncio.create_task(
        service._install_packages(
            job_dir=job_dir,
            dependencies=["pkg"],
            cache_key="abc123",
        )
    )
    await copy_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # The copy thread completed before the finally-block cleanup began:
    # rmtree cannot race an in-flight copy.
    assert copy_finished.is_set()
    assert events == ["copy_finished", "cleanup_started"]

    # The cancelled promotion was not published and left no partial .tmp
    # tree stranded in the shared package cache. (The cache-key parent dir
    # is created before the copy as setup and may remain; the promoted
    # site-packages tree must not.)
    published = cache_dir / "packages" / "abc123" / "site-packages"
    assert not published.exists()
    assert list((cache_dir / "packages").glob("*.tmp")) == []
