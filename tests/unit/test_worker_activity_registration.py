from __future__ import annotations

from collections.abc import Iterator, Sequence
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
    stop_litellm_proxy = AsyncMock()

    monkeypatch.setattr(executor_worker, "start_litellm_proxy", AsyncMock())
    monkeypatch.setattr(executor_worker, "start_mcp_server", AsyncMock())
    monkeypatch.setattr(
        executor_worker,
        "get_temporal_client",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(executor_worker, "stop_mcp_server", stop_mcp_server)
    monkeypatch.setattr(executor_worker, "stop_litellm_proxy", stop_litellm_proxy)
    executor_worker.interrupt_event.clear()

    with pytest.raises(RuntimeError, match="boom"):
        await executor_worker.main()

    stop_mcp_server.assert_awaited_once()
    stop_litellm_proxy.assert_awaited_once()
