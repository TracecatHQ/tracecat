from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

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


@pytest.mark.anyio
async def test_execute_action_starts_registry_tool_workflow_with_alias_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _build_claims()
    monkeypatch.setattr(
        executor, "build_agent_tool_workflow_id", lambda: "agent-tool/tool-wf-123"
    )
    fake_client = SimpleNamespace(
        execute_workflow=AsyncMock(return_value={"uri": "s3://stored"}),
    )
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
        {"url": "https://example.com"},
        claims,
        _build_registry_lock(),
        tool_call_id="toolu_123",
    )

    call = fake_client.execute_workflow.await_args
    assert call.kwargs["id"] == "agent-tool/tool-wf-123"
    assert call.kwargs["memo"] == {
        "parent_agent_workflow_id": f"agent/{claims.session_id}",
        "parent_agent_run_id": "run-123",
        "parent_agent_session_id": str(claims.session_id),
        "tool_call_id": "toolu_123",
        "action_name": "core.http_request",
    }
    search_attributes = call.kwargs["search_attributes"]
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
        execute_workflow=AsyncMock(
            side_effect=WorkflowAlreadyStartedError(
                workflow_id,
                "ExecuteRegistryToolWorkflow.run",
            )
        ),
        get_workflow_handle=Mock(return_value=handle),
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

    fake_client.get_workflow_handle.assert_called_once_with(workflow_id)
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
        execute_workflow=AsyncMock(
            side_effect=WorkflowAlreadyStartedError(
                workflow_id,
                "ExecuteRegistryToolWorkflow.run",
            )
        ),
        get_workflow_handle=Mock(return_value=handle),
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


def test_build_tool_workflow_alias_attrs_uses_claude_code_prefix() -> None:
    attrs = executor.build_tool_workflow_alias_attrs("toolu_123")
    assert len(attrs) == 1
    assert attrs[0].key.name == TemporalSearchAttr.ALIAS.value
    assert attrs[0].value == "cc:toolu_123"


def test_build_tool_workflow_alias_attrs_skips_missing_tool_call_id() -> None:
    assert executor.build_tool_workflow_alias_attrs(None) == []
