from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

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
async def test_execute_action_starts_deterministic_registry_tool_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _build_claims()
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
    assert call.kwargs["id"] == f"agent-tool/{claims.session_id}/toolu_123"
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
    assert pairs[TemporalSearchAttr.WORKSPACE_ID.value] == str(claims.workspace_id)
    retrieve_stored_object.assert_awaited_once_with({"uri": "s3://stored"})
    assert result == {"ok": True}


@pytest.mark.anyio
async def test_execute_action_reuses_existing_workflow_on_duplicate_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _build_claims()
    workflow_id = f"agent-tool/{claims.session_id}/toolu_123"
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


def test_build_tool_workflow_id_falls_back_for_unsafe_tool_call_id() -> None:
    session_id = uuid.UUID("00000000-0000-0000-0000-000000000003")

    workflow_id = executor._build_tool_workflow_id(session_id, "tool/123")

    assert workflow_id.startswith(f"agent-tool/{session_id}/")
    assert workflow_id != f"agent-tool/{session_id}/tool/123"
