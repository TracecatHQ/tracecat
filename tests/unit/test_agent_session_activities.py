from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.agent.session.activities import (
    FinalizeTurnInput,
    PendingToolResult,
    ReconcileToolResultsInput,
    finalize_turn_activity,
    reconcile_tool_results_activity,
)
from tracecat.auth.types import Role
from tracecat.storage.object import ExternalObject, ObjectRef


@pytest.mark.anyio
@pytest.mark.parametrize(
    "emit_terminal_done",
    [False, True],
    ids=["legacy-workflow", "combined-workflow"],
)
async def test_finalize_turn_activity_bridges_workflow_versions(
    monkeypatch: pytest.MonkeyPatch,
    *,
    emit_terminal_done: bool,
) -> None:
    service = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = service
    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentSessionService.with_session",
        MagicMock(return_value=ctx),
    )

    role = Role(
        type="service",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        service_id="tracecat-service",
    )
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    active_stream_id = uuid.uuid4()
    input = FinalizeTurnInput(
        role=role,
        session_id=session_id,
        run_id=run_id,
        active_stream_id=active_stream_id,
        emit_terminal_done=emit_terminal_done,
    )

    result = await finalize_turn_activity(input)

    if emit_terminal_done:
        assert result is not None
        assert result.terminal_done_emitted is True
        service.finalize_turn.assert_awaited_once_with(
            session_id,
            run_id,
            active_stream_id=active_stream_id,
        )
        service.clear_turn_pointers.assert_not_awaited()
    else:
        assert result is None
        service.clear_turn_pointers.assert_awaited_once_with(session_id, run_id)
        service.finalize_turn.assert_not_awaited()


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
