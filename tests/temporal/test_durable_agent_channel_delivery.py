"""DurableAgentWorkflow channel delivery: ordering, failure, and replay."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio import workflow as temporal_workflow
from temporalio.client import Client, WorkflowHandle, WorkflowHistory
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
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

from tests.shared import recorded_patch_ids
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
from tracecat import config
from tracecat.agent.approvals.batch import approval_batch_id
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.delivery import (
    ApprovalDecided,
    ApprovalPending,
    ChannelNotice,
    ChannelNoticeResult,
    DeliverChannelNoticeInput,
    TurnFinished,
)
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
from tracecat.temporal.patches import DurableAgentWorkflowPatch

pytestmark = [pytest.mark.temporal, pytest.mark.usefixtures("db")]

TOOL_CALL_ID = "call-1"


class Recorder:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.notices: list[ChannelNotice] = []
        self.approval_pending = asyncio.Event()


def _create_session_activity(linked: bool) -> Callable[..., Any]:
    @activity.defn(name="create_session_activity")
    async def create_session(input: CreateSessionInput) -> CreateSessionResult:
        return CreateSessionResult(
            session_id=input.session_id, success=True, channel_thread_linked=linked
        )

    return create_session


def _deliver_activity(recorder: Recorder, *, fail: bool = False) -> Callable[..., Any]:
    @activity.defn(name="deliver_channel_notice")
    async def deliver(input: DeliverChannelNoticeInput) -> ChannelNoticeResult:
        notice = input.notice
        recorder.order.append(f"deliver:{notice.kind}")
        recorder.notices.append(notice)
        if fail:
            raise ApplicationError("provider down", non_retryable=True)
        if isinstance(notice, ApprovalPending):
            recorder.approval_pending.set()
            return ChannelNoticeResult(ref="card-1")
        return ChannelNoticeResult(ref=None)

    return deliver


def _finalize_activity(recorder: Recorder) -> Callable[..., Any]:
    @activity.defn(name="finalize_turn_activity")
    async def finalize(input: FinalizeTurnInput) -> FinalizeTurnResult:
        del input
        recorder.order.append("finalize_turn")
        return FinalizeTurnResult(terminal_done_emitted=True)

    return finalize


@activity.defn(name="record_approval_requests")
async def _record_approval_requests(input: PersistApprovalsActivityInputs) -> None:
    del input


@activity.defn(name="apply_approval_decisions")
async def _apply_approval_decisions(input: ApplyApprovalResultsActivityInputs) -> None:
    del input


@activity.defn(name="emit_session_cancelled")
async def _emit_session_cancelled(input: EmitSessionCancelledInputs) -> None:
    del input


def _activities(
    recorder: Recorder,
    executor: Callable[[int, AgentExecutorInput], AgentExecutorResult],
    *,
    linked: bool = True,
    fail_delivery: bool = False,
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
        _deliver_activity(recorder, fail=fail_delivery),
        _record_approval_requests,
        _apply_approval_decisions,
        _emit_session_cancelled,
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


@pytest.fixture
def worker_for(threadpool, monkeypatch: pytest.MonkeyPatch):
    def create(
        client: Client, queue: str, activities: list[Callable[..., Any]]
    ) -> Worker:
        monkeypatch.setattr(config, "TRACECAT__AGENT_EXECUTOR_QUEUE", queue)
        monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", queue)
        return Worker(
            client=client,
            task_queue=queue,
            activities=activities,
            workflows=[DurableAgentWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            activity_executor=threadpool,
            max_cached_workflows=0,
        )

    return create


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
async def test_turn_finished_is_delivered_before_the_session_is_released(
    svc_role: Role, temporal_client: Client, worker_for
) -> None:
    session_id = uuid.uuid4()
    queue = f"test-channel-delivery-{session_id}"
    recorder = Recorder()

    history = await _run_to_completion(
        temporal_client,
        worker_for(temporal_client, queue, _activities(recorder, _completes)),
        _workflow_args(svc_role, session_id),
        session_id,
        queue,
    )

    assert recorder.order == ["deliver:turn_finished", "finalize_turn"]
    # The workflow id token doubles as the run id.
    assert recorder.notices == [TurnFinished(session_id=session_id, run_id=session_id)]
    assert DurableAgentWorkflowPatch.CHANNEL_DELIVERY in await recorded_patch_ids(
        temporal_client, history
    )
    await replay_durable_agent_workflow_history(temporal_client, history)


@pytest.mark.anyio
@pytest.mark.integration
async def test_failed_delivery_still_releases_the_session(
    svc_role: Role, temporal_client: Client, worker_for
) -> None:
    session_id = uuid.uuid4()
    queue = f"test-channel-delivery-{session_id}"
    recorder = Recorder()

    await _run_to_completion(
        temporal_client,
        worker_for(
            temporal_client,
            queue,
            _activities(recorder, _completes, fail_delivery=True),
        ),
        _workflow_args(svc_role, session_id),
        session_id,
        queue,
    )

    assert recorder.order == ["deliver:turn_finished", "finalize_turn"]


@pytest.mark.anyio
@pytest.mark.integration
async def test_unlinked_session_schedules_no_delivery(
    svc_role: Role, temporal_client: Client, worker_for
) -> None:
    session_id = uuid.uuid4()
    queue = f"test-channel-delivery-{session_id}"
    recorder = Recorder()

    await _run_to_completion(
        temporal_client,
        worker_for(
            temporal_client, queue, _activities(recorder, _completes, linked=False)
        ),
        _workflow_args(svc_role, session_id),
        session_id,
        queue,
    )

    assert recorder.order == ["finalize_turn"]


@pytest.mark.anyio
@pytest.mark.integration
async def test_history_without_the_patch_never_schedules_delivery(
    svc_role: Role,
    temporal_client: Client,
    worker_for,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    queue = f"test-channel-delivery-{session_id}"
    recorder = Recorder()
    patched = temporal_workflow.patched

    # Record history as a pre-patch worker would: no marker, no delivery.
    with monkeypatch.context() as pre_patch:
        pre_patch.setattr(
            temporal_workflow,
            "patched",
            lambda patch_id: (
                False
                if patch_id == DurableAgentWorkflowPatch.CHANNEL_DELIVERY
                else patched(patch_id)
            ),
        )
        history = await _run_to_completion(
            temporal_client,
            worker_for(temporal_client, queue, _activities(recorder, _completes)),
            _workflow_args(svc_role, session_id),
            session_id,
            queue,
        )

    assert recorder.order == ["finalize_turn"]
    assert DurableAgentWorkflowPatch.CHANNEL_DELIVERY not in await recorded_patch_ids(
        temporal_client, history
    )
    # Current code replays the marker-less history without scheduling delivery.
    await replay_durable_agent_workflow_history(temporal_client, history)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["decided", "cancelled"])
async def test_approval_notices_carry_the_card_ref(
    svc_role: Role, temporal_client: Client, worker_for, outcome: str
) -> None:
    session_id = uuid.uuid4()
    queue = f"test-channel-delivery-{session_id}"
    recorder = Recorder()
    worker = worker_for(
        temporal_client, queue, _activities(recorder, _asks_for_approval_once)
    )

    async with worker:
        handle = await _start(
            temporal_client,
            _workflow_args(svc_role, session_id, approvals=True),
            session_id,
            queue,
        )
        await asyncio.wait_for(recorder.approval_pending.wait(), timeout=10)
        if outcome == "decided":
            await handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                WorkflowApprovalSubmission(approvals={TOOL_CALL_ID: False}),
            )
        else:
            await handle.execute_update(
                DurableAgentWorkflow.request_cancel, WorkflowCancelRequest()
            )
        await handle.result()
        history = await handle.fetch_history()

    batch_id = approval_batch_id(session_id, [TOOL_CALL_ID])
    assert recorder.order == [
        "deliver:approval_pending",
        "deliver:approval_decided",
        "deliver:turn_finished",
        "finalize_turn",
    ]
    assert recorder.notices[:2] == [
        ApprovalPending(session_id=session_id, run_id=session_id, batch_id=batch_id),
        ApprovalDecided(
            session_id=session_id, batch_id=batch_id, previous_ref="card-1"
        ),
    ]
    await replay_durable_agent_workflow_history(temporal_client, history)
