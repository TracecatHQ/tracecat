from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from temporalio import workflow
from temporalio.common import Priority, RetryPolicy

from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.schemas import (
    AgentWorkflowArgs,
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)
from tracecat.agent.backends.types import (
    SessionForkContext,
    SessionWorkflowContext,
    TurnResult,
)
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
    PrepareCommentAgentInvocationResult,
)
from tracecat.cases.agent_invocations.types import PreparedCommentAgentSession

ACTIVITIES = "tracecat.cases.agent_invocations.activities"


@workflow.defn(name="InstalledTurnWorkflow")
class InstalledTurnWorkflow:
    @workflow.run
    async def run(self, args: dict[str, Any], /) -> dict[str, Any]:
        return args

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> bool:
        return True

    @workflow.update
    def request_cancel(self, request: WorkflowCancelRequest) -> None:
        pass


class InstalledBackend(AgentBackend[dict[str, Any], dict[str, Any]]):
    """An installed backend whose every routing detail differs from the built-in."""

    workflow = InstalledTurnWorkflow
    name = "Installed"
    default_harness = "claude_code"
    supported_harnesses = frozenset({"claude_code"})
    caller_owned_turns = True
    task_queue = "installed-queue"
    retry_policy = RetryPolicy(maximum_attempts=3)
    priority = Priority(priority_key=5, fairness_key="comments")

    async def prepare_fork(self, context: SessionForkContext) -> None:
        pass

    def workflow_id(self, run_id: uuid.UUID) -> str:
        return f"installed/{run_id}"

    async def build_workflow_args(
        self, context: SessionWorkflowContext
    ) -> dict[str, Any]:
        return {
            "backend": context.backend_id,
            "prompt": context.prompt,
            "actions": context.config.actions,
            "tools": list(context.tools or ()),
        }

    def parse_turn_result(self, result: object) -> TurnResult:
        match result:
            case {"kind": "completed", "answer": answer}:
                return TurnResult(output=answer)
            case {"error": str(error)}:
                return TurnResult(output=None, error=error)
        raise AssertionError(f"Unexpected result: {result!r}")


def _roles() -> tuple[Role, Role]:
    actor_role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
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
    return actor_role, execution_role


def _prepared_turn(backend_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        prompt="Investigate this thread",
        session_id=uuid.uuid4(),
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
        backend_id=backend_id,
        harness_type="claude_code",
    )


async def _prepare(
    prepared_turn: SimpleNamespace, actor_role: Role, execution_role: Role
) -> PrepareCommentAgentInvocationResult:
    prepared_session = PreparedCommentAgentSession(
        invocation_id=uuid.uuid4(),
        session_id=prepared_turn.session_id,
        prompt=prepared_turn.prompt,
        display_messages=("@Agent investigate",),
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
            f"{ACTIVITIES}.CaseCommentAgentInvocationDispatcher.with_session",
            return_value=dispatcher_context(),
        ),
        patch(f"{ACTIVITIES}.AgentSessionService", return_value=session_service),
    ):
        return await prepare_comment_agent_invocation_activity(
            PrepareCommentAgentInvocationInput(
                role=actor_role, invocation_id=prepared_session.invocation_id
            )
        )


@pytest.mark.anyio
async def test_prepare_comment_invocation_uses_session_execution_role() -> None:
    actor_role, execution_role = _roles()
    prepared_turn = _prepared_turn("oss")

    result = await _prepare(prepared_turn, actor_role, execution_role)

    # New turns go through the selected backend's contract, started by name.
    assert result.workflow_args is None
    turn = result.turn
    assert turn is not None
    assert turn.backend_id == "oss"
    assert turn.workflow_type == "DurableAgentWorkflow"
    assert turn.workflow_id == f"agent/{prepared_turn.run_id}"
    assert turn.task_queue == DefaultBackend.task_queue
    assert turn.retry_policy == DefaultBackend.retry_policy
    assert turn.priority == DefaultBackend.priority
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


@pytest.mark.anyio
async def test_prepare_routes_the_turn_to_the_sessions_installed_backend() -> None:
    actor_role, execution_role = _roles()
    prepared_turn = _prepared_turn("external")

    with patch(
        f"{ACTIVITIES}.get_agent_backend", return_value=InstalledBackend()
    ) as get_backend:
        result = await _prepare(prepared_turn, actor_role, execution_role)

    get_backend.assert_called_once_with("external", harness_type="claude_code")
    turn = result.turn
    assert turn is not None
    assert turn.backend_id == "external"
    assert turn.workflow_type == "InstalledTurnWorkflow"
    assert turn.workflow_id == f"installed/{prepared_turn.run_id}"
    assert turn.task_queue == "installed-queue"
    assert turn.retry_policy == RetryPolicy(maximum_attempts=3)
    assert turn.priority == Priority(priority_key=5, fairness_key="comments")
    # Built by the backend itself, with reply actions still stripped.
    assert turn.args == {
        "backend": "external",
        "prompt": "Investigate this thread",
        "actions": ["core.cases.get_case"],
        "tools": ["core.cases.get_case"],
    }


def _complete_input(
    output: object, backend_id: str
) -> CompleteCommentAgentInvocationInput:
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
        backend_id=backend_id,
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
        f"{ACTIVITIES}.CaseCommentAgentInvocationCompletionService.with_session",
        return_value=service_context(),
    ):
        yield service


@pytest.mark.anyio
async def test_complete_reads_the_turn_result_through_the_backend() -> None:
    reply_id = uuid.uuid4()
    input = _complete_input(
        {"output": "verdict", "duration": 1.0, "session_id": str(uuid.uuid4())},
        "oss",
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
        {"output": None, "duration": 1.0, "session_id": str(uuid.uuid4())}, "oss"
    )
    with _completion_service(uuid.uuid4()) as service:
        result = await complete_comment_agent_invocation_activity(input)
    service.create_reply_and_mark_succeeded.assert_not_awaited()
    assert result == CompleteCommentAgentInvocationResult(
        handled=False, turn_error="Agent turn ended without output"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "posted", "turn_error"),
    [
        ({"kind": "completed", "answer": "benign"}, "benign", None),
        (
            {"kind": "failed", "error": "model quota exceeded"},
            None,
            "model quota exceeded",
        ),
    ],
)
async def test_complete_reads_an_installed_backends_result_with_its_parser(
    result: dict[str, str], posted: str | None, turn_error: str | None
) -> None:
    reply_id = uuid.uuid4()
    input = _complete_input(result, "external")
    with (
        patch(
            f"{ACTIVITIES}.find_agent_backend", return_value=InstalledBackend()
        ) as find_backend,
        _completion_service(reply_id) as service,
    ):
        completed = await complete_comment_agent_invocation_activity(input)
    find_backend.assert_called_once_with("external")
    if posted is None:
        service.create_reply_and_mark_succeeded.assert_not_awaited()
        assert completed == CompleteCommentAgentInvocationResult(
            handled=False, turn_error=turn_error
        )
    else:
        service.create_reply_and_mark_succeeded.assert_awaited_once_with(
            input.session_id, posted
        )
        assert completed == CompleteCommentAgentInvocationResult(
            handled=True, reply_comment_id=reply_id
        )
