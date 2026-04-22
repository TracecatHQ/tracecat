from __future__ import annotations

from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.executor.activity import run_agent_activity
from tracecat.agent.executor_worker import (
    get_activities as get_agent_executor_activities,
)
from tracecat.agent.preset.activities import (
    resolve_agent_preset_config_activity,
    resolve_agent_preset_version_ref_activity,
)
from tracecat.agent.worker import get_activities as get_agent_worker_activities
from tracecat.dsl.worker import get_activities as get_dsl_worker_activities


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


def _activity_name(activity: object) -> str:
    return getattr(activity, "__temporal_activity_definition").name


def _activity_names(activities: Sequence[object]) -> set[str]:
    return {_activity_name(activity) for activity in activities}


def test_dsl_worker_registers_preset_version_resolution_activity() -> None:
    names = _activity_names(get_dsl_worker_activities())
    assert _activity_name(resolve_agent_preset_version_ref_activity) in names


def test_agent_worker_registers_preset_resolution_activities() -> None:
    names = _activity_names(get_agent_worker_activities())
    assert _activity_name(resolve_agent_preset_config_activity) in names
    assert _activity_name(resolve_agent_preset_version_ref_activity) in names


def test_agent_executor_worker_registers_only_runtime_execution_activity() -> None:
    names = _activity_names(get_agent_executor_activities())
    assert names == {_activity_name(run_agent_activity)}


@pytest.mark.anyio
async def test_agent_executor_worker_cleans_up_runtime_services_on_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.agent import executor_worker

    stop_mcp_server = AsyncMock()
    stop_broker = AsyncMock()

    monkeypatch.setattr(executor_worker, "start_claude_runtime_broker", AsyncMock())
    monkeypatch.setattr(executor_worker, "start_mcp_server", AsyncMock())
    monkeypatch.setattr(
        executor_worker,
        "get_temporal_client",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(executor_worker, "stop_mcp_server", stop_mcp_server)
    monkeypatch.setattr(executor_worker, "stop_claude_runtime_broker", stop_broker)
    executor_worker.interrupt_event.clear()

    with pytest.raises(RuntimeError, match="boom"):
        await executor_worker.main()

    stop_mcp_server.assert_awaited_once()
    stop_broker.assert_awaited_once()


@pytest.mark.anyio
async def test_agent_executor_worker_runs_runtime_service_hooks_on_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.agent import executor_worker

    start_broker = AsyncMock()
    stop_broker = AsyncMock()
    monkeypatch.setattr(executor_worker, "start_claude_runtime_broker", start_broker)
    monkeypatch.setattr(executor_worker, "start_mcp_server", AsyncMock())
    monkeypatch.setattr(
        executor_worker,
        "get_temporal_client",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(executor_worker, "stop_mcp_server", AsyncMock())
    monkeypatch.setattr(executor_worker, "stop_claude_runtime_broker", stop_broker)
    executor_worker.interrupt_event.clear()

    with pytest.raises(RuntimeError, match="boom"):
        await executor_worker.main()

    start_broker.assert_awaited_once()
    stop_broker.assert_awaited_once()


@pytest.mark.anyio
async def test_agent_executor_worker_treats_empty_numeric_env_vars_as_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.agent import executor_worker

    captured: dict[str, int | str | timedelta] = {}

    class _FakeWorker:
        def __init__(
            self,
            client: object,
            *,
            task_queue: str,
            activities: Sequence[object],
            workflow_runner: object,
            max_concurrent_activities: int,
            disable_eager_activity_execution: bool,
            activity_executor: ThreadPoolExecutor,
            graceful_shutdown_timeout: timedelta,
        ) -> None:
            del client, activities, workflow_runner, disable_eager_activity_execution
            captured["task_queue"] = task_queue
            captured["max_concurrent_activities"] = max_concurrent_activities
            captured["threadpool_max_workers"] = activity_executor._max_workers
            captured["graceful_shutdown_timeout"] = graceful_shutdown_timeout

        async def __aenter__(self) -> _FakeWorker:
            executor_worker.interrupt_event.set()
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> None:
            del exc_type, exc, tb

    monkeypatch.setenv("TRACECAT__AGENT_EXECUTOR_MAX_CONCURRENT_ACTIVITIES", "")
    monkeypatch.setenv("TEMPORAL__THREADPOOL_MAX_WORKERS", "")
    monkeypatch.setattr(
        executor_worker, "_start_runtime_services", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(executor_worker, "_stop_runtime_services", AsyncMock())
    monkeypatch.setattr(executor_worker, "Worker", _FakeWorker)
    monkeypatch.setattr(executor_worker, "new_sandbox_runner", lambda: object())
    monkeypatch.setattr(
        executor_worker.config,
        "TRACECAT__AGENT_EXECUTOR_QUEUE",
        "test-agent-executor-queue",
    )
    executor_worker.interrupt_event.clear()

    await executor_worker.main()

    assert captured == {
        "task_queue": "test-agent-executor-queue",
        "max_concurrent_activities": 1,
        "threadpool_max_workers": 100,
        "graceful_shutdown_timeout": timedelta(minutes=5),
    }


@pytest.mark.anyio
async def test_agent_executor_worker_raises_when_runtime_service_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.agent import executor_worker

    class _FakeWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> _FakeWorker:
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> None:
            del exc_type, exc, tb

    async def fake_start_runtime_services() -> object:
        executor_worker.runtime_failure_reason = "LLM gateway became unhealthy"
        executor_worker.interrupt_event.set()
        return object()

    monkeypatch.setattr(
        executor_worker, "_start_runtime_services", fake_start_runtime_services
    )
    monkeypatch.setattr(executor_worker, "_stop_runtime_services", AsyncMock())
    monkeypatch.setattr(executor_worker, "Worker", _FakeWorker)
    monkeypatch.setattr(executor_worker, "new_sandbox_runner", lambda: object())
    executor_worker.interrupt_event.clear()
    executor_worker.runtime_failure_reason = None

    with pytest.raises(RuntimeError, match="LLM gateway became unhealthy"):
        await executor_worker.main()
