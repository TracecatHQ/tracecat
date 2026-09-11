from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import anyio
import pytest
from anyio.abc import TaskStatus
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.mcp import executor
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.registry.lock.types import RegistryLock
from tracecat.workflow.executions.correlation import build_agent_session_correlation_id
from tracecat.workflow.executions.enums import TemporalSearchAttr


def _build_claims() -> MCPTokenClaims:
    session_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    return MCPTokenClaims(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        session_id=session_id,
        parent_agent_workflow_id=f"agent/{session_id}",
        parent_agent_run_id="run-123",
        allowed_actions=["core.http_request"],
    )


def _build_registry_lock() -> RegistryLock:
    return RegistryLock(
        origins={"tracecat_registry": "test-version"},
        actions={"core.http_request": "tracecat_registry"},
    )


@dataclass(frozen=True, slots=True)
class _PendingWorkflow:
    client: Mock
    handle: Mock
    waiting: asyncio.Event


@pytest.fixture
def pending_workflow(monkeypatch: pytest.MonkeyPatch) -> _PendingWorkflow:
    waiting = asyncio.Event()

    async def result() -> None:
        waiting.set()
        await asyncio.Event().wait()

    handle = Mock(spec=WorkflowHandle, id="agent-tool/pending")
    handle.result = AsyncMock(side_effect=result)
    handle.cancel = AsyncMock()
    client = Mock(spec=Client)
    client.start_workflow = AsyncMock(return_value=handle)
    client.get_workflow_handle_for = Mock(return_value=handle)
    monkeypatch.setattr(executor, "build_agent_tool_workflow_id", lambda: handle.id)
    monkeypatch.setattr(executor, "get_temporal_client", AsyncMock(return_value=client))
    return _PendingWorkflow(client=client, handle=handle, waiting=waiting)


@pytest.mark.anyio
async def test_execute_action_starts_registry_tool_workflow_with_alias_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _build_claims()
    untrusted_session_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    monkeypatch.setattr(
        executor, "build_agent_tool_workflow_id", lambda: "agent-tool/tool-wf-123"
    )
    handle = SimpleNamespace(result=AsyncMock(return_value={"uri": "s3://stored"}))
    fake_client = SimpleNamespace(start_workflow=AsyncMock(return_value=handle))
    monkeypatch.setattr(
        executor, "get_temporal_client", AsyncMock(return_value=fake_client)
    )
    monkeypatch.setattr(
        executor.StoredObjectValidator,
        "validate_python",
        staticmethod(lambda value: value),
    )
    retrieve_stored_object = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(executor, "retrieve_stored_object", retrieve_stored_object)

    result = await executor.execute_action(
        "core.http_request",
        {
            "url": "https://example.com",
            "agent_session_id": str(untrusted_session_id),
        },
        claims,
        _build_registry_lock(),
        tool_call_id="toolu_123",
    )

    start_call = fake_client.start_workflow.await_args
    assert start_call.kwargs["id"] == "agent-tool/tool-wf-123"
    assert start_call.kwargs.get("run_timeout") is None
    assert start_call.kwargs.get("execution_timeout") is None
    workflow_input = start_call.args[1]
    assert workflow_input.run_input.agent_session_id == claims.session_id
    assert workflow_input.run_input.task.args["agent_session_id"] == str(
        untrusted_session_id
    )
    assert start_call.kwargs["memo"] == {
        "parent_agent_workflow_id": f"agent/{claims.session_id}",
        "parent_agent_run_id": "run-123",
        "parent_agent_session_id": str(claims.session_id),
        "tool_call_id": "toolu_123",
        "action_name": "core.http_request",
    }
    search_attributes = start_call.kwargs["search_attributes"]
    pairs = {pair.key.name: pair.value for pair in search_attributes.search_attributes}
    assert pairs[
        TemporalSearchAttr.CORRELATION_ID.value
    ] == build_agent_session_correlation_id(claims.session_id)
    assert pairs[TemporalSearchAttr.ALIAS.value] == "cc:toolu_123"
    assert pairs[TemporalSearchAttr.WORKSPACE_ID.value] == str(claims.workspace_id)
    retrieve_stored_object.assert_awaited_once_with({"uri": "s3://stored"})
    assert result == {"ok": True}


@pytest.mark.anyio
async def test_execute_action_reuses_existing_workflow_on_duplicate_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _build_claims()
    workflow_id = "agent-tool/tool-wf-123"
    monkeypatch.setattr(executor, "build_agent_tool_workflow_id", lambda: workflow_id)
    handle = SimpleNamespace(result=AsyncMock(return_value={"uri": "s3://existing"}))
    fake_client = SimpleNamespace(
        start_workflow=AsyncMock(
            side_effect=WorkflowAlreadyStartedError(
                workflow_id,
                "ExecuteRegistryToolWorkflow.run",
                run_id="existing-run",
            )
        ),
        get_workflow_handle_for=Mock(return_value=handle),
    )
    monkeypatch.setattr(
        executor, "get_temporal_client", AsyncMock(return_value=fake_client)
    )
    monkeypatch.setattr(
        executor.StoredObjectValidator,
        "validate_python",
        staticmethod(lambda value: value),
    )
    retrieve_stored_object = AsyncMock(return_value={"ok": "existing"})
    monkeypatch.setattr(executor, "retrieve_stored_object", retrieve_stored_object)

    result = await executor.execute_action(
        "core.http_request",
        {"url": "https://example.com"},
        claims,
        _build_registry_lock(),
        tool_call_id="toolu_123",
    )

    fake_client.get_workflow_handle_for.assert_called_once_with(
        executor.ExecuteRegistryToolWorkflow.run,
        workflow_id,
        first_execution_run_id="existing-run",
    )
    handle.result.assert_awaited_once()
    assert result == {"ok": "existing"}


@pytest.mark.anyio
async def test_execute_action_maps_existing_workflow_failures_on_duplicate_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _build_claims()
    workflow_id = "agent-tool/tool-wf-123"
    monkeypatch.setattr(executor, "build_agent_tool_workflow_id", lambda: workflow_id)
    handle = SimpleNamespace(
        result=AsyncMock(
            side_effect=WorkflowFailureError(
                cause=ApplicationError("registry execution failed")
            )
        )
    )
    fake_client = SimpleNamespace(
        start_workflow=AsyncMock(
            side_effect=WorkflowAlreadyStartedError(
                workflow_id,
                "ExecuteRegistryToolWorkflow.run",
            )
        ),
        get_workflow_handle_for=Mock(return_value=handle),
    )
    monkeypatch.setattr(
        executor, "get_temporal_client", AsyncMock(return_value=fake_client)
    )

    with pytest.raises(
        executor.ActionExecutionError, match="registry execution failed"
    ):
        await executor.execute_action(
            "core.http_request",
            {"url": "https://example.com"},
            claims,
            _build_registry_lock(),
            tool_call_id="toolu_123",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("during_start", [True, False])
@pytest.mark.parametrize("already_started", [True, False])
async def test_caller_cancellation_cancels_workflow_despite_start_race(
    pending_workflow: _PendingWorkflow,
    during_start: bool,
    already_started: bool,
) -> None:
    start_entered = asyncio.Event()
    allow_start = asyncio.Event()
    cancel_entered = asyncio.Event()
    allow_cancel = asyncio.Event()

    async def start(*_args: object, **_kwargs: object) -> Mock:
        start_entered.set()
        await allow_start.wait()
        if already_started:
            raise WorkflowAlreadyStartedError(
                pending_workflow.handle.id,
                "ExecuteRegistryToolWorkflow",
                run_id="existing-run",
            )
        return pending_workflow.handle

    async def cancel(**_kwargs: object) -> None:
        cancel_entered.set()
        await allow_cancel.wait()

    pending_workflow.client.start_workflow.side_effect = start
    pending_workflow.handle.cancel.side_effect = cancel
    task = asyncio.create_task(
        executor.execute_action(
            "core.http_request", {}, _build_claims(), _build_registry_lock()
        )
    )
    try:
        async with asyncio.timeout(5):
            await start_entered.wait()
            if not during_start:
                allow_start.set()
                await pending_workflow.waiting.wait()
            task.cancel("caller left")
            await asyncio.sleep(0)
            assert not task.done()
            allow_start.set()
            await cancel_entered.wait()
            # A second cancellation must not abandon the remote cancellation RPC.
            task.cancel("caller still gone")
            await asyncio.sleep(0)
            assert not task.done()
            allow_cancel.set()
            with pytest.raises(asyncio.CancelledError, match="caller left"):
                await task
    finally:
        allow_start.set()
        allow_cancel.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    pending_workflow.handle.cancel.assert_awaited_once_with(
        rpc_timeout=executor._WORKFLOW_RPC_TIMEOUT
    )
    if already_started:
        assert (
            call(
                executor.ExecuteRegistryToolWorkflow.run,
                pending_workflow.handle.id,
                first_execution_run_id="existing-run",
            )
            in pending_workflow.client.get_workflow_handle_for.call_args_list
        )


@pytest.mark.anyio
async def test_anyio_scope_cancellation_reaches_temporal(
    pending_workflow: _PendingWorkflow,
) -> None:
    finished = asyncio.Event()

    async def run(*, task_status: TaskStatus[anyio.CancelScope]) -> None:
        with anyio.CancelScope() as scope:
            task_status.started(scope)
            await executor.execute_action(
                "core.http_request", {}, _build_claims(), _build_registry_lock()
            )
        finished.set()

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            scope = await tasks.start(run)
            await pending_workflow.waiting.wait()
            scope.cancel()
            await finished.wait()

    pending_workflow.handle.cancel.assert_awaited_once_with(
        rpc_timeout=executor._WORKFLOW_RPC_TIMEOUT
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status", [RPCStatusCode.NOT_FOUND, RPCStatusCode.UNAVAILABLE])
async def test_cancellation_rpc_failure_preserves_caller_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    pending_workflow: _PendingWorkflow,
    status: RPCStatusCode,
) -> None:
    pending_workflow.handle.cancel.side_effect = RPCError(
        "Cancellation request failed", status, b""
    )
    logger = Mock()
    monkeypatch.setattr(executor, "logger", logger)
    task = asyncio.create_task(
        executor.execute_action(
            "core.http_request", {}, _build_claims(), _build_registry_lock()
        )
    )
    async with asyncio.timeout(5):
        await pending_workflow.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    pending_workflow.handle.cancel.assert_awaited_once()
    if status == RPCStatusCode.NOT_FOUND:
        logger.warning.assert_not_called()
    else:
        logger.warning.assert_called_once()


@pytest.mark.anyio
async def test_ambiguous_start_failure_cancels_by_workflow_id(
    pending_workflow: _PendingWorkflow,
) -> None:
    error = RPCError("Start response lost", RPCStatusCode.DEADLINE_EXCEEDED, b"")
    pending_workflow.client.start_workflow.side_effect = error

    with pytest.raises(RPCError) as raised:
        await executor.execute_action(
            "core.http_request", {}, _build_claims(), _build_registry_lock()
        )

    assert raised.value is error
    pending_workflow.client.get_workflow_handle_for.assert_called_once_with(
        executor.ExecuteRegistryToolWorkflow.run, pending_workflow.handle.id
    )
    pending_workflow.handle.cancel.assert_awaited_once_with(
        rpc_timeout=executor._WORKFLOW_RPC_TIMEOUT
    )


def test_build_tool_workflow_alias_attrs_uses_claude_code_prefix() -> None:
    attrs = executor.build_tool_workflow_alias_attrs("toolu_123")
    assert len(attrs) == 1
    assert attrs[0].key.name == TemporalSearchAttr.ALIAS.value
    assert attrs[0].value == "cc:toolu_123"


def test_build_tool_workflow_alias_attrs_skips_missing_tool_call_id() -> None:
    assert executor.build_tool_workflow_alias_attrs(None) == []
