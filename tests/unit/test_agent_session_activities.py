from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.agent.session.activities import (
    CreateSessionInput,
    PendingToolResult,
    ReconcileToolResultsInput,
    create_session_activity,
    reconcile_tool_results_activity,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import ResolvedAgentsConfig
from tracecat.auth.types import Role
from tracecat.storage.object import ExternalObject, ObjectRef


@pytest.mark.anyio
async def test_reconcile_tool_results_raises_on_materialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = MagicMock()
    stream.append = AsyncMock()

    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentStream.new",
        AsyncMock(return_value=stream),
    )
    monkeypatch.setattr(
        "tracecat.agent.session.activities.retrieve_stored_object",
        AsyncMock(side_effect=RuntimeError("blob unavailable")),
    )

    input = ReconcileToolResultsInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        role=Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            service_id="tracecat-service",
        ),
        pending_results=[
            PendingToolResult(
                tool_call_id="toolu_123",
                tool_name="core.http_request",
                stored_result=ExternalObject(
                    ref=ObjectRef(
                        bucket="tracecat-workflow",
                        key="results/test",
                        size_bytes=1,
                        sha256="0" * 64,
                    )
                ),
            )
        ],
    )

    with pytest.raises(RuntimeError, match="blob unavailable"):
        await reconcile_tool_results_activity(input)

    stream.append.assert_not_awaited()


@pytest.mark.anyio
async def test_reconcile_tool_results_removes_interrupts_and_returns_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = MagicMock()
    stream.append = AsyncMock()

    service = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = service

    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentStream.new",
        AsyncMock(return_value=stream),
    )
    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentSessionService.with_session",
        MagicMock(return_value=ctx),
    )

    input = ReconcileToolResultsInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        role=Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            service_id="tracecat-service",
        ),
        pending_results=[
            PendingToolResult(
                tool_call_id="toolu_123",
                tool_name="core.http_request",
                raw_result={"status": "ok"},
            )
        ],
    )

    result = await reconcile_tool_results_activity(input)

    assert len(result.results) == 1
    assert result.results[0].tool_call_id == "toolu_123"
    assert result.results[0].result == {"status": "ok"}
    stream.append.assert_awaited_once()
    service.replace_interrupt_with_tool_results.assert_awaited_once_with(
        input.session_id,
        result.results,
    )


@pytest.mark.anyio
async def test_reconcile_tool_results_appends_artifact_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = MagicMock()
    stream.append = AsyncMock()

    service = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = service

    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentStream.new",
        AsyncMock(return_value=stream),
    )
    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentSessionService.with_session",
        MagicMock(return_value=ctx),
    )

    input = ReconcileToolResultsInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        role=Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            service_id="tracecat-service",
        ),
        pending_results=[
            PendingToolResult(
                tool_call_id="toolu_123",
                tool_name="core.cases.create_case",
                tool_input={"summary": "Suspicious login"},
                raw_result={
                    "id": "case_123",
                    "summary": "Suspicious login",
                    "severity": "high",
                    "status": "new",
                },
            )
        ],
    )

    await reconcile_tool_results_activity(input)

    append_calls = [call.args[0] for call in stream.append.await_args_list]
    assert len(append_calls) == 2
    assert append_calls[0].tool_call_id == "toolu_123"
    assert append_calls[1].artifact_data is not None
    assert append_calls[1].artifact_data.op == "upsert"
    assert append_calls[1].artifact_data.artifact == {
        "type": "case",
        "id": "case_123",
        "title": "Suspicious login",
        "scope": {"parentToolCallId": "toolu_123"},
        "severity": "high",
        "status": "new",
    }
    service.apply_artifact_side_effects.assert_awaited_once()
    apply_args = service.apply_artifact_side_effects.await_args.args
    assert apply_args[0] == input.session_id
    assert len(apply_args[1]) == 1
    assert apply_args[1][0].op == "upsert"


@pytest.mark.anyio
async def test_create_session_activity_accepts_matching_legacy_session_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy workflows can verify a matching stored session binding."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    agents_binding = ResolvedAgentsConfig.model_validate(
        {"enabled": True, "subagents": []}
    )
    agent_session = MagicMock()
    agent_session.id = uuid.uuid4()
    agent_session.agents_binding = agents_binding.model_dump(mode="json")
    agent_session.sdk_session_id = "sdk-session"
    agent_session.parent_session_id = None

    service = MagicMock()
    service.get_or_create_session = AsyncMock(return_value=(agent_session, False))
    service.session.add = MagicMock()
    service.session.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = service
    stream = MagicMock()
    stream.clear_buffer = AsyncMock()

    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentSessionService.with_session",
        MagicMock(return_value=ctx),
    )
    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentStream.new",
        AsyncMock(return_value=stream),
    )

    result = await create_session_activity(
        CreateSessionInput(
            role=role,
            session_id=agent_session.id,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            agents_binding=agents_binding,
            curr_run_id=uuid.uuid4(),
        )
    )

    assert result.success is True
    service.session.add.assert_called_with(agent_session)
    service.session.commit.assert_awaited_once()
    stream.clear_buffer.assert_awaited_once()
