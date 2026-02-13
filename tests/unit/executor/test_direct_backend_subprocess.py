from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tracecat.auth.types import Role
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID
from tracecat.registry.lock.types import RegistryLock


def _make_input() -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    exec_id = ExecutionUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            action="core.transform.reshape",
            args={"value": {"x": 1}},
            ref="test_action",
        ),
        exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={"core.transform.reshape": "tracecat_registry"},
        ),
    )


def _make_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.UUID("38be3315-c172-4332-aea6-53fc4b93f053"),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _make_resolved_context() -> ResolvedContext:
    return ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(
            type="udf",
            action_name="core.transform.reshape",
            module="tracecat_registry.core.transform",
            name="reshape",
            origin="tracecat_registry",
        ),
        evaluated_args={"value": {"x": 1}},
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        executor_token="test-token",
        logical_time=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_direct_backend_uses_subprocess_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = DirectBackend()
    input_data = _make_input()
    role = _make_role()
    resolved_context = _make_resolved_context()

    mock_runner = AsyncMock()
    mock_runner.execute_action = AsyncMock(return_value={"ok": True})

    async def _get_tarballs(_input: RunActionInput, _role: Role) -> list[str]:
        return ["s3://tracecat-registry/test/site-packages.tar.gz"]

    monkeypatch.setattr(
        "tracecat.executor.backends.direct.get_action_runner",
        lambda: mock_runner,
    )
    monkeypatch.setattr(backend, "_get_tarball_uris", _get_tarballs)

    result = await backend.execute(
        input=input_data,
        role=role,
        resolved_context=resolved_context,
        timeout=15.0,
    )

    assert result.type == "success"
    assert result.result == {"ok": True}
    mock_runner.execute_action.assert_awaited_once()
    call = mock_runner.execute_action.await_args.kwargs
    assert call["force_sandbox"] is False
    assert call["tarball_uris"] == ["s3://tracecat-registry/test/site-packages.tar.gz"]


@pytest.mark.anyio
async def test_direct_backend_fails_without_tarballs(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = DirectBackend()
    input_data = _make_input()
    role = _make_role()
    resolved_context = _make_resolved_context()

    async def _get_tarballs(_input: RunActionInput, _role: Role) -> list[str]:
        return []

    monkeypatch.setattr("tracecat.executor.backends.direct.config.TRACECAT__LOCAL_REPOSITORY_ENABLED", False)
    monkeypatch.setattr(backend, "_get_tarball_uris", _get_tarballs)

    result = await backend.execute(
        input=input_data,
        role=role,
        resolved_context=resolved_context,
        timeout=15.0,
    )

    assert result.type == "failure"
    assert result.error.type == "RegistryError"


@pytest.mark.anyio
async def test_direct_backend_maps_runner_error(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = DirectBackend()
    input_data = _make_input()
    role = _make_role()
    resolved_context = _make_resolved_context()

    mock_runner = AsyncMock()
    mock_runner.execute_action = AsyncMock(
        return_value=ExecutorActionErrorInfo(
            action_name=input_data.task.action,
            type="ValueError",
            message="boom",
            filename="<subprocess>",
            function="run",
        )
    )

    async def _get_tarballs(_input: RunActionInput, _role: Role) -> list[str]:
        return ["s3://tracecat-registry/test/site-packages.tar.gz"]

    monkeypatch.setattr(
        "tracecat.executor.backends.direct.get_action_runner",
        lambda: mock_runner,
    )
    monkeypatch.setattr(backend, "_get_tarball_uris", _get_tarballs)

    result = await backend.execute(
        input=input_data,
        role=role,
        resolved_context=resolved_context,
        timeout=15.0,
    )

    assert result.type == "failure"
    assert result.error.type == "ValueError"
