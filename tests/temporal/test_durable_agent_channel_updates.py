"""DurableAgentWorkflow channel updates: which lifecycle activity publishes each."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle, WorkflowHistory
from temporalio.worker import Worker
from tracecat_ee.agent.activities import (
    ApplyApprovalResultsActivityInputs,
    EmitSessionCancelledInputs,
    PersistApprovalsActivityInputs,
)
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.agent.workflows.durable import (
    AgentWorkflowArgs,
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)

from tests.temporal import test_durable_agent_workflow as durable_tests
from tests.temporal.test_durable_agent_workflow import (
    create_mock_build_tool_definitions_activity,
    create_mock_emit_session_done_activity,
    create_mock_execute_action_activity,
    create_mock_load_session_activity,
    create_mock_load_session_messages_activity,
    create_mock_reconcile_tool_results_activity,
    create_mock_run_agent_activity,
    replay_durable_agent_workflow_history,
)
from tracecat.agent.channel_publisher import (
    ApprovalDecided,
    ApprovalPending,
    RunUpdate,
    TurnFinished,
    approval_batch_id,
)
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.executor.activity import AgentExecutorInput, AgentExecutorResult
from tracecat.agent.schemas import AgentOutput, RunAgentArgs
from tracecat.agent.session.activities import (
    CreateSessionInput,
    CreateSessionResult,
    FinalizeTurnInput,
    FinalizeTurnResult,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.dsl.common import RETRY_POLICIES

pytestmark = [pytest.mark.temporal, pytest.mark.usefixtures("db")]

# Re-export the worker fixture under the name collected by this test module.
agent_worker_factory = durable_tests.agent_worker_factory

TOOL_CALL_ID = "call-1"


class Recorder:
    """Each lifecycle activity in order, with the updates it was handed."""

    def __init__(self) -> None:
        self.steps: list[tuple[str, list[RunUpdate]]] = []
        self.approval_pending = asyncio.Event()

    def record(self, step: str, updates: list[RunUpdate] | None = None) -> None:
        self.steps.append((step, updates or []))


def _create_session_activity(linked: bool) -> Callable[..., Any]:
    @activity.defn(name="create_session_activity")
    async def create_session(input: CreateSessionInput) -> CreateSessionResult:
        return CreateSessionResult(
            session_id=input.session_id, success=True, channel_linked=linked
        )

    return create_session


def _finalize_activity(recorder: Recorder) -> Callable[..., Any]:
    @activity.defn(name="finalize_turn_activity")
    async def finalize(input: FinalizeTurnInput) -> FinalizeTurnResult:
        recorder.record("finalize_turn", input.channel_updates)
        return FinalizeTurnResult(terminal_done_emitted=True)

    return finalize


def _record_requests_activity(
    recorder: Recorder, pending_ref: str | None
) -> Callable[..., Any]:
    @activity.defn(name="record_approval_requests")
    async def record_requests(
        input: PersistApprovalsActivityInputs,
    ) -> str | None:
        recorder.record("record_approval_requests", input.channel_updates)
        recorder.approval_pending.set()
        return pending_ref

    return record_requests


def _apply_decisions_activity(recorder: Recorder) -> Callable[..., Any]:
    @activity.defn(name="apply_approval_decisions")
    async def apply_decisions(input: ApplyApprovalResultsActivityInputs) -> None:
        recorder.record("apply_approval_decisions", input.channel_updates)

    return apply_decisions


def _emit_cancelled_activity(recorder: Recorder) -> Callable[..., Any]:
    @activity.defn(name="emit_session_cancelled")
    async def emit_cancelled(input: EmitSessionCancelledInputs) -> None:
        del input
        recorder.record("emit_session_cancelled")

    return emit_cancelled


def _activities(
    recorder: Recorder,
    executor: Callable[[int, AgentExecutorInput], AgentExecutorResult],
    *,
    linked: bool = True,
    pending_ref: str | None = "msg-1",
) -> list[Callable[..., Any]]:
    return [
        _create_session_activity(linked),
        create_mock_load_session_activity(),
        create_mock_load_session_messages_activity(),
        create_mock_build_tool_definitions_activity(),
        create_mock_run_agent_activity(executor),
        create_mock_execute_action_activity(),
        create_mock_reconcile_tool_results_activity(),
        create_mock_emit_session_done_activity(),
        _finalize_activity(recorder),
        _record_requests_activity(recorder, pending_ref),
        _apply_decisions_activity(recorder),
        _emit_cancelled_activity(recorder),
    ]


def _completes(call_count: int, input: AgentExecutorInput) -> AgentExecutorResult:
    del call_count, input
    return AgentExecutorResult(success=True)


def _asks_for_approval_once(
    call_count: int, input: AgentExecutorInput
) -> AgentExecutorResult:
    del input
    if call_count > 0:
        return AgentExecutorResult(success=True)
    return AgentExecutorResult(
        success=True,
        approval_requested=True,
        approval_items=[
            ToolCallContent(
                id=TOOL_CALL_ID,
                name="core__http_request",
                input={"url": "https://example.com", "method": "GET"},
            )
        ],
    )


def _workflow_args(
    role: Role, session_id: uuid.UUID, *, approvals: bool = False
) -> AgentWorkflowArgs:
    return AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            session_id=session_id,
            user_prompt="Test prompt",
            config=AgentConfig(
                model_name="claude-3-5-sonnet-20241022",
                model_provider="anthropic",
                actions=["core.http_request"],
                tool_approvals={"core.http_request": True} if approvals else None,
            ),
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )


async def _start(
    client: Client, args: AgentWorkflowArgs, session_id: uuid.UUID, queue: str
) -> WorkflowHandle[DurableAgentWorkflow, AgentOutput]:
    return await client.start_workflow(
        DurableAgentWorkflow.run,
        args,
        id=AgentWorkflowID(session_id),
        task_queue=queue,
        retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        execution_timeout=timedelta(seconds=30),
    )


async def _run_to_completion(
    client: Client,
    worker: Worker,
    args: AgentWorkflowArgs,
    session_id: uuid.UUID,
    queue: str,
) -> WorkflowHistory:
    async with worker:
        handle = await _start(client, args, session_id, queue)
        await handle.result()
        return await handle.fetch_history()


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize("linked", [True, False])
async def test_finalize_publishes_turn_finished_before_the_release(
    svc_role: Role, temporal_client: Client, agent_worker_factory, linked: bool
) -> None:
    session_id = uuid.uuid4()
    queue = f"test-channel-updates-{session_id}"
    recorder = Recorder()

    history = await _run_to_completion(
        temporal_client,
        agent_worker_factory(
            temporal_client,
            task_queue=queue,
            custom_activities=_activities(recorder, _completes, linked=linked),
        ),
        _workflow_args(svc_role, session_id),
        session_id,
        queue,
    )

    # The workflow id token doubles as the run id.
    finished = [TurnFinished(session_id=session_id, run_id=session_id)]
    assert recorder.steps == [("finalize_turn", finished if linked else [])]
    await replay_durable_agent_workflow_history(temporal_client, history)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("cancel", "pending_ref"),
    [(False, "msg-1"), (True, "msg-1"), (False, None)],
    ids=["decided", "cancelled", "no-ref"],
)
async def test_approval_updates_ride_the_approval_activities(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    cancel: bool,
    pending_ref: str | None,
) -> None:
    session_id = uuid.uuid4()
    queue = f"test-channel-updates-{session_id}"
    recorder = Recorder()
    worker = agent_worker_factory(
        temporal_client,
        task_queue=queue,
        custom_activities=_activities(
            recorder, _asks_for_approval_once, pending_ref=pending_ref
        ),
    )

    async with worker:
        handle = await _start(
            temporal_client,
            _workflow_args(svc_role, session_id, approvals=True),
            session_id,
            queue,
        )
        await asyncio.wait_for(recorder.approval_pending.wait(), timeout=10)
        if cancel:
            await handle.execute_update(
                DurableAgentWorkflow.request_cancel, WorkflowCancelRequest()
            )
        else:
            await handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                WorkflowApprovalSubmission(approvals={TOOL_CALL_ID: False}),
            )
        await handle.result()
        history = await handle.fetch_history()

    batch_id = approval_batch_id(session_id, [TOOL_CALL_ID])
    pending = ApprovalPending(
        session_id=session_id, run_id=session_id, batch_id=batch_id
    )
    # Without a ref the publisher finds the message by batch id.
    decided = ApprovalDecided(
        session_id=session_id, batch_id=batch_id, pending_ref=pending_ref
    )
    finished = TurnFinished(session_id=session_id, run_id=session_id)
    if cancel:
        # Stop reaches the UI first; finalize publishes the decision the wait skipped.
        expected = [
            ("record_approval_requests", [pending]),
            ("apply_approval_decisions", []),
            ("emit_session_cancelled", []),
            ("finalize_turn", [decided, finished]),
        ]
    else:
        expected = [
            ("record_approval_requests", [pending]),
            ("apply_approval_decisions", [decided]),
            ("finalize_turn", [finished]),
        ]
    assert recorder.steps == expected
    await replay_durable_agent_workflow_history(temporal_client, history)
