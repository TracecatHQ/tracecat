"""Readiness must bracket initialized workers and end before their drain."""

import asyncio
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent import executor_worker
from tracecat.executor import worker


@pytest.fixture(params=[worker, executor_worker], ids=["executor", "agent"])
def worker_module(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    module: ModuleType = request.param
    monkeypatch.setattr("tracecat.executor.startup.validate_execution_backend", Mock())
    monkeypatch.setattr(module, "initialize_platform_tracing", Mock())
    monkeypatch.setattr(module, "shutdown_platform_tracing", Mock())
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
