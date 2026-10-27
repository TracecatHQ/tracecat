from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.schemas import AgentWorkflowArgs
from tracecat.agent.session.service import AGENT_SESSION_EXECUTION_SCOPES
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.cases.agent_invocations.activities import (
    complete_comment_agent_invocation_activity,
    prepare_comment_agent_invocation_activity,
)
from tracecat.cases.agent_invocations.schemas import (
    CompleteCommentAgentInvocationInput,
    CompleteCommentAgentInvocationResult,
    PrepareCommentAgentInvocationInput,
)
from tracecat.cases.agent_invocations.types import PreparedCommentAgentSession


@pytest.mark.anyio
async def test_prepare_comment_invocation_uses_session_execution_role() -> None:
    invocation_id = uuid.uuid4()
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    actor_role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"case:update", "agent:execute"}),
    )
    execution_role = actor_role.model_copy(
        update={
            "scopes": (actor_role.scopes or frozenset())
            | AGENT_SESSION_EXECUTION_SCOPES
        }
    )
    prepared_session = PreparedCommentAgentSession(
        invocation_id=invocation_id,
        session_id=session_id,
        prompt="Investigate this thread",
        display_messages=("@Agent investigate",),
    )
    prepared_turn = SimpleNamespace(
        prompt=prepared_session.prompt,
        session_id=session_id,
        run_id=uuid.uuid4(),
        active_stream_id=uuid.uuid4(),
        config=AgentConfig(
            model_name="test-model",
            model_provider="openai",
            actions=["core.cases.get_case", "core.cases.reply_to_comment"],
        ),
        title="Case agent",
        entity_type=AgentSessionEntity.CASE,
        entity_id=uuid.uuid4(),
        tools=["core.cases.get_case", "core.cases.create_comment"],
        agent_preset_id=uuid.uuid4(),
        agent_preset_version_id=uuid.uuid4(),
        backend_id="oss",
        harness_type="claude_code",
    )
    dispatcher = SimpleNamespace(
        session=object(),
        create_or_get_agent_session=AsyncMock(return_value=prepared_session),
    )
    session_service = SimpleNamespace(
        execution_role=execution_role,
        prepare_new_turn=AsyncMock(return_value=prepared_turn),
    )

    @contextlib.asynccontextmanager
    async def dispatcher_context():
        yield dispatcher

    with (
        patch(
            "tracecat.cases.agent_invocations.activities."
            "CaseCommentAgentInvocationDispatcher.with_session",
            return_value=dispatcher_context(),
        ),
        patch(
            "tracecat.cases.agent_invocations.activities.AgentSessionService",
            return_value=session_service,
        ),
    ):
        result = await prepare_comment_agent_invocation_activity(
            PrepareCommentAgentInvocationInput(
                role=actor_role,
                invocation_id=invocation_id,
            )
        )

    # New turns go through the selected backend's contract, started by name.
    assert result.workflow_args is None
    turn = result.turn
    assert turn is not None
    assert turn.backend_id == "oss"
    assert turn.workflow_type == "DurableAgentWorkflow"
    assert turn.workflow_id == f"agent/{prepared_turn.run_id}"
    assert turn.task_queue == DefaultBackend.task_queue
    assert turn.retry_policy == DefaultBackend.retry_policy
    args = turn.args
    assert isinstance(args, AgentWorkflowArgs)
    assert args.role == execution_role
    assert args.role.scopes == (
        (actor_role.scopes or frozenset()) | AGENT_SESSION_EXECUTION_SCOPES
    )
    assert actor_role.scopes == frozenset({"case:update", "agent:execute"})
    # The agent can't reply on its own; the invocation posts the reply.
    assert args.agent_args.config is not None
    assert args.agent_args.config.actions == ["core.cases.get_case"]
    assert args.tools == ["core.cases.get_case"]
    assert args.agent_args.curr_run_id == prepared_turn.run_id
    assert args.agent_args.active_stream_id == prepared_turn.active_stream_id


def _complete_input(output: object) -> CompleteCommentAgentInvocationInput:
    return CompleteCommentAgentInvocationInput(
        role=Role(
            type="service",
            service_id="tracecat-api",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
        session_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        output=output,
        backend_id="oss",
    )


@contextlib.contextmanager
def _completion_service(reply_id: uuid.UUID):
    service = SimpleNamespace(
        create_reply_and_mark_succeeded=AsyncMock(
            return_value=SimpleNamespace(id=reply_id)
        )
    )

    @contextlib.asynccontextmanager
    async def service_context():
        yield service

    with patch(
        "tracecat.cases.agent_invocations.activities."
        "CaseCommentAgentInvocationCompletionService.with_session",
        return_value=service_context(),
    ):
        yield service


@pytest.mark.anyio
async def test_complete_reads_the_turn_result_through_the_backend() -> None:
    reply_id = uuid.uuid4()
    input = _complete_input(
        {"output": "verdict", "duration": 1.0, "session_id": str(uuid.uuid4())}
    )
    with _completion_service(reply_id) as service:
        result = await complete_comment_agent_invocation_activity(input)
    service.create_reply_and_mark_succeeded.assert_awaited_once_with(
        input.session_id, "verdict"
    )
    assert result == CompleteCommentAgentInvocationResult(
        handled=True, reply_comment_id=reply_id
    )


@pytest.mark.anyio
async def test_complete_posts_nothing_for_a_turn_without_output() -> None:
    """A cancelled or empty turn reports its error to the workflow, unposted."""
    input = _complete_input(
        {"output": None, "duration": 1.0, "session_id": str(uuid.uuid4())}
    )
    with _completion_service(uuid.uuid4()) as service:
        result = await complete_comment_agent_invocation_activity(input)
    service.create_reply_and_mark_succeeded.assert_not_awaited()
    assert result == CompleteCommentAgentInvocationResult(
        handled=False, turn_error="Agent turn ended without output"
    )
