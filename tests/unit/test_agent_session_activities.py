from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.agent.session.activities import (
    PendingToolResult,
    ReconcileToolResultsInput,
    reconcile_tool_results_activity,
)
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
