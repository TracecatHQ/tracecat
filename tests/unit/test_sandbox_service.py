"""Tests for SandboxService promotion and cleanup concurrency behavior."""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_mock

from tracecat.sandbox import service as service_module
from tracecat.sandbox.exceptions import PackageInstallError, SandboxWorkloadError
from tracecat.sandbox.service import SandboxService, _await_task_rejoined
from tracecat.sandbox.types import SandboxErrorCode, SandboxResult


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
    # Recursive: a stranded .tmp tree lives at packages/<cache_key>/site-packages.*.tmp.
    assert list((cache_dir / "packages").glob("**/*.tmp")) == []


@pytest.mark.anyio
async def test_install_packages_discards_copy_worker_error_under_cancellation(
    tmp_path: Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A copy-worker failure must not mask a pending cancellation.

    If the copy thread completes with a non-safety error (e.g. PermissionError)
    at the same loop tick that cancellation lands, the rejoin helper re-raises
    the worker error inside the caller's cancellation handler. The promotion
    must discard it and re-raise the cancellation instead. Regression for the
    PR #3088 review finding.
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

    service._nsjail_executor = cast(Any, _FakeExecutor())

    copy_started = asyncio.Event()
    release = threading.Event()

    def failing_copy(
        site_packages_src: Path,  # noqa: ARG001
        temp_dest: Path,  # noqa: ARG001
        **kwargs: object,  # noqa: ARG001
    ) -> bool:
        copy_started.set()
        release.wait(timeout=5)
        raise PermissionError("copy worker failed")

    mocker.patch(
        "tracecat.sandbox.service.copy_tree_without_following_symlinks",
        failing_copy,
    )
    mocker.patch("tracecat.sandbox.service.shutil.rmtree", shutil.rmtree)

    # Simulate the rejoin helper being entered when the copy task has already
    # completed with an error: it surfaces the worker error to the caller's
    # cancellation handler. Awaiting the task is behaviorally identical to
    # the real helper's already-completed path (task.result()).
    async def fake_rejoined(task: asyncio.Task[Any]) -> Any:
        return await task

    mocker.patch(
        "tracecat.sandbox.service._await_task_rejoined",
        fake_rejoined,
    )

    task = asyncio.create_task(
        service._install_packages(
            job_dir=job_dir,
            dependencies=["pkg"],
            cache_key="abc123",
        )
    )
    await copy_started.wait()
    task.cancel()  # Cancellation lands while the copy is still in flight.
    release.set()  # The worker then fails before the handler's join completes.

    with pytest.raises(asyncio.CancelledError):
        await task

    # The cancelled promotion was not published.
    published = cache_dir / "packages" / "abc123" / "site-packages"
    assert not published.exists()


@pytest.mark.anyio
async def test_await_task_rejoined_reraises_cancellation_after_worker_finishes() -> (
    None
):
    """Suppressed cancellation during the wait must propagate after the join.

    Regression for the PR #3088 review finding: the helper previously consumed
    the CancelledError and returned normally once the worker finished, letting
    a cancelled promotion fall through to script execution. The worker here is
    release-gated so the cancellation deterministically lands mid-wait.
    """
    release = threading.Event()
    finished = threading.Event()

    def worker() -> int:
        release.wait(timeout=5)
        finished.set()
        return 7

    outer = asyncio.create_task(
        _await_task_rejoined(asyncio.create_task(asyncio.to_thread(worker)))
    )
    await asyncio.sleep(0.05)  # Let the helper reach its shielded await.
    outer.cancel()  # Cancellation arrives while the worker is still running.
    release.set()  # Unblock the worker; the helper must still join it.

    with pytest.raises(asyncio.CancelledError):
        await outer
    assert finished.is_set()


@pytest.mark.anyio
async def test_await_task_rejoined_cancellation_takes_priority_over_worker_error() -> (
    None
):
    """A worker error under a suppressed cancellation must not mask it."""
    release = threading.Event()
    finished = threading.Event()

    def worker() -> int:
        release.wait(timeout=5)
        finished.set()
        raise RuntimeError("worker failure")

    outer = asyncio.create_task(
        _await_task_rejoined(asyncio.create_task(asyncio.to_thread(worker)))
    )
    await asyncio.sleep(0.05)
    outer.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await outer
    assert finished.is_set()


@pytest.mark.anyio
async def test_await_task_rejoined_returns_result_without_cancellation() -> None:
    """Without cancellation the helper is a transparent pass-through."""

    def worker() -> dict[str, int]:
        return {"value": 5}

    result = await _await_task_rejoined(asyncio.create_task(asyncio.to_thread(worker)))
    assert result == {"value": 5}


@pytest.mark.anyio
@pytest.mark.parametrize("use_nsjail", [False, True])
async def test_run_python_failure_log_omits_sandbox_payload(
    tmp_path: Path,
    mocker: pytest_mock.MockerFixture,
    use_nsjail: bool,
) -> None:
    """Failure logs contain diagnostics metadata, never sandbox output."""
    secret = "synthetic-secret-token"
    derived_secret = "nekot-terces-citehtnys"
    error = f"ValueError: {secret}"
    stdout = f"stdout={secret} derived={derived_secret}"
    stderr = f"stderr={secret} derived={derived_secret}"
    result = SandboxResult(
        success=False,
        error=error,
        stdout=stdout,
        stderr=stderr,
        error_code=SandboxErrorCode.WORKLOAD_FAILURE,
        exit_code=1,
        execution_time_ms=12.5,
    )
    service = SandboxService(cache_dir=str(tmp_path / "cache"))
    executor = mocker.Mock()
    executor.execute = mocker.AsyncMock(return_value=result)
    if use_nsjail:
        mocker.patch.object(service, "_is_nsjail_available", return_value=True)
        service._nsjail_executor = cast(Any, executor)
    else:
        mocker.patch.object(service, "_is_nsjail_available", return_value=False)
        service._unsafe_pid_executor = cast(Any, executor)

    log_error = mocker.patch.object(service_module.logger, "error")
    resolved_env_vars = {"USER_SECRET": secret}

    with pytest.raises(SandboxWorkloadError) as exc_info:
        await service.run_python(
            "def main():\n    return None\n",
            env_vars=resolved_env_vars,
        )

    # The service preserves the result message for the outer, secret-aware
    # executor boundary to mask; this test covers the host log trust boundary.
    assert str(exc_info.value) == error
    log_error.assert_called_once()
    fields = log_error.call_args.kwargs
    assert set(fields) == {
        "error_code",
        "exit_code",
        "execution_time_ms",
        "stdout_chars",
        "stderr_chars",
    }
    assert fields["error_code"] is SandboxErrorCode.WORKLOAD_FAILURE
    assert fields["exit_code"] == 1
    assert fields["execution_time_ms"] == 12.5
    assert fields["stdout_chars"] == len(stdout)
    assert fields["stderr_chars"] == len(stderr)
    assert secret not in repr(log_error.call_args)
    assert derived_secret not in repr(log_error.call_args)


@pytest.mark.anyio
async def test_package_install_failure_log_omits_sandbox_payload(
    tmp_path: Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Package failure logs retain status metadata without installer output."""
    secret = "synthetic-package-secret"
    result = SandboxResult(
        success=False,
        error=f"InstallError: {secret}",
        stdout=f"downloaded {secret}",
        stderr=f"registry response {secret}",
        error_code=SandboxErrorCode.WORKLOAD_FAILURE,
        exit_code=1,
        execution_time_ms=25.0,
    )
    service = SandboxService(cache_dir=str(tmp_path / "cache"))
    executor = mocker.Mock()
    executor.execute_install = mocker.AsyncMock(return_value=result)
    service._nsjail_executor = cast(Any, executor)
    log_error = mocker.patch.object(service_module.logger, "error")

    with pytest.raises(PackageInstallError) as exc_info:
        await service._install_packages(
            tmp_path,
            ["synthetic-package==1.0.0"],
            "deadbeef",
        )

    assert str(exc_info.value) == f"Failed to install packages: InstallError: {secret}"
    log_error.assert_called_once()
    fields = log_error.call_args.kwargs
    assert set(fields) == {
        "dependencies",
        "error_code",
        "exit_code",
        "execution_time_ms",
        "stdout_chars",
        "stderr_chars",
    }
    assert fields["error_code"] is SandboxErrorCode.WORKLOAD_FAILURE
    assert fields["exit_code"] == 1
    assert fields["stdout_chars"] == len(result.stdout)
    assert fields["stderr_chars"] == len(result.stderr)
    assert secret not in repr(log_error.call_args)
