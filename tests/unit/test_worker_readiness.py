"""Readiness must bracket initialized workers and end before their drain."""

import asyncio
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent import executor_worker
from tracecat.executor import worker

TELEMETRY_LIFECYCLE = [
    "initialize_platform_tracing",
    "initialize_platform_metrics",
    "shutdown_platform_metrics",
    "shutdown_platform_tracing",
]


@pytest.fixture
def telemetry() -> Mock:
    """Parent mock that records the platform telemetry calls in order."""
    return Mock()


@pytest.fixture(params=[worker, executor_worker], ids=["executor", "agent"])
def worker_module(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, telemetry: Mock
) -> ModuleType:
    module: ModuleType = request.param
    monkeypatch.setattr("tracecat.executor.startup.validate_execution_backend", Mock())
    for name in TELEMETRY_LIFECYCLE:
        monkeypatch.setattr(module, name, getattr(telemetry, name))
    monkeypatch.setattr(module, "close_storage_client_cache", AsyncMock())
    if module is worker:
        monkeypatch.setattr(
            module, "initialize_executor_sentry_from_environment", Mock()
        )
        monkeypatch.setattr(module, "ActionGateway", Mock(return_value=AsyncMock()))
        monkeypatch.setattr(
            module,
            "get_action_runner",
            Mock(return_value=Mock(registry_artifacts=AsyncMock())),
        )
        monkeypatch.setattr(module, "initialize_executor_backend", AsyncMock())
        monkeypatch.setattr(module, "shutdown_executor_backend", AsyncMock())
        monkeypatch.setattr(module, "get_temporal_client", AsyncMock())
    else:
        monkeypatch.setattr(module, "sandbox_cgroup", Mock())
        monkeypatch.setattr(module, "initialize_worker_sentry_from_environment", Mock())
        monkeypatch.setattr(module, "_start_runtime_services", AsyncMock())
        monkeypatch.setattr(module, "_stop_runtime_services", AsyncMock())
    return module


@pytest.mark.anyio
@pytest.mark.parametrize("fail", [False, True], ids=["shutdown", "failure"])
async def test_readiness_ends_before_worker_drain(
    worker_module: ModuleType,
    telemetry: Mock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: bool,
) -> None:
    ready = tmp_path / "ready"
    ready.touch()
    monkeypatch.setattr("tracecat.executor.startup.WORKER_READY_FILE", ready)
    shutdown = asyncio.Event()
    drained = False

    class FakeWorker:
        async def __aenter__(self) -> None:
            assert not ready.exists()

        async def __aexit__(self, *args: object) -> None:
            nonlocal drained
            assert not ready.exists()
            drained = True

    async def wait() -> bool:
        assert ready.is_file()
        if fail:
            raise RuntimeError("worker failed")
        return True

    monkeypatch.setattr(worker_module, "Worker", Mock(return_value=FakeWorker()))
    monkeypatch.setattr(shutdown, "wait", wait)
    if fail:
        with pytest.raises(RuntimeError, match="worker failed"):
            await worker_module.main(shutdown_event=shutdown)
    else:
        await worker_module.main(shutdown_event=shutdown)
    assert drained
    assert not ready.exists()
    # Metrics start after tracing and flush before it.
    assert [c[0] for c in telemetry.mock_calls] == TELEMETRY_LIFECYCLE


@pytest.mark.anyio
async def test_cleanup_failure_still_flushes_telemetry(
    worker_module: ModuleType,
    telemetry: Mock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = tmp_path / "ready"
    ready.touch()
    monkeypatch.setattr("tracecat.executor.startup.WORKER_READY_FILE", ready)
    cleanup = (
        "shutdown_executor_backend"
        if worker_module is worker
        else "_stop_runtime_services"
    )
    monkeypatch.setattr(
        worker_module, cleanup, AsyncMock(side_effect=RuntimeError("cleanup failed"))
    )

    class FakeWorker:
        async def __aenter__(self) -> None: ...

        async def __aexit__(self, *args: object) -> None: ...

    shutdown = asyncio.Event()
    shutdown.set()
    monkeypatch.setattr(worker_module, "Worker", Mock(return_value=FakeWorker()))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await worker_module.main(shutdown_event=shutdown)
    assert [c[0] for c in telemetry.mock_calls] == TELEMETRY_LIFECYCLE


@pytest.mark.anyio
async def test_unremovable_ready_file_fails_before_initialization(
    worker_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "ready"
    ready.mkdir()
    monkeypatch.setattr("tracecat.executor.startup.WORKER_READY_FILE", ready)
    validate = Mock()
    monkeypatch.setattr(
        "tracecat.executor.startup.validate_execution_backend", validate
    )
    with pytest.raises(OSError):
        await worker_module.main()
    validate.assert_not_called()
