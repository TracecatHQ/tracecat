import asyncio
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import delete, select
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat import config
from tracecat.agent.schemas import AgentOutput, RunAgentArgs
from tracecat.agent.service import AgentManagementService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.agent.worker import new_sandbox_runner
from tracecat.auth.types import Role
from tracecat.cases.agent_invocations import activities as invocation_activities
from tracecat.cases.agent_invocations import queue as invocation_queue
from tracecat.cases.agent_invocations import schemas
from tracecat.cases.agent_invocations import workflows as invocation_workflows
from tracecat.cases.enums import (
    CaseCommentAgentInvocationStatus,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import CaseCommentCreate
from tracecat.cases.service import CaseCommentsService
from tracecat.db.models import (
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    Case,
    CaseComment,
    CaseCommentAgentInvocation,
    CaseCommentMention,
)
from tracecat.dsl._converter import get_data_converter

pytestmark = pytest.mark.temporal
_EVENTS: list[str] = []
_CHILD_WAITING = asyncio.Event()
_RELEASE_CHILD = asyncio.Event()
_FAILURES: list[schemas.FailCommentAgentInvocationInput] = []


@activity.defn(name=schemas.PREPARE_COMMENT_AGENT_INVOCATION_ACTIVITY)
async def prepare_turn(
    input: schemas.PrepareCommentAgentInvocationInput,
) -> schemas.PrepareCommentAgentInvocationResult:
    _EVENTS.append("prepare")
    turn_id = input.invocation_id
    return schemas.PrepareCommentAgentInvocationResult(
        workflow_args=AgentWorkflowArgs(
            role=input.role,
            agent_args=RunAgentArgs(
                user_prompt="Investigate",
                session_id=turn_id,
                curr_run_id=turn_id,
                config=AgentConfig(model_name="test", model_provider="openai"),
            ),
            entity_type=AgentSessionEntity.CASE,
            entity_id=turn_id,
        )
    )


@activity.defn(name=schemas.COMPLETE_COMMENT_AGENT_INVOCATION_ACTIVITY)
async def complete_turn(
    input: schemas.CompleteCommentAgentInvocationInput,
) -> schemas.CompleteCommentAgentInvocationResult:
    _EVENTS.append(f"complete:{input.output}")
    return schemas.CompleteCommentAgentInvocationResult(
        handled=True, reply_comment_id=input.run_id
    )


@activity.defn(name="wait_for_comment_agent_child_release")
async def wait_for_child_release() -> None:
    _EVENTS.append("child_waiting")
    _CHILD_WAITING.set()
    await _RELEASE_CHILD.wait()


@activity.defn(name="record_comment_agent_child_wait")
async def record_child_wait() -> None:
    _EVENTS.append("child_waiting")
    _CHILD_WAITING.set()


@activity.defn(name=schemas.PREPARE_COMMENT_AGENT_INVOCATION_ACTIVITY)
async def fail_prepare_turn(
    input: schemas.PrepareCommentAgentInvocationInput,
) -> schemas.PrepareCommentAgentInvocationResult:
    raise ApplicationError("preparation exploded", non_retryable=True)


@activity.defn(name=schemas.COMPLETE_COMMENT_AGENT_INVOCATION_ACTIVITY)
async def fail_complete_turn(
    input: schemas.CompleteCommentAgentInvocationInput,
) -> schemas.CompleteCommentAgentInvocationResult:
    raise ApplicationError("completion exploded", non_retryable=True)


@activity.defn(name=schemas.FAIL_COMMENT_AGENT_INVOCATION_ACTIVITY)
async def record_failure(
    input: schemas.FailCommentAgentInvocationInput,
) -> schemas.FailCommentAgentInvocationResult:
    _FAILURES.append(input)
    return schemas.FailCommentAgentInvocationResult(transitioned=True)


@activity.defn(name=schemas.FAIL_COMMENT_AGENT_INVOCATION_ACTIVITY)
async def fail_record_failure(
    input: schemas.FailCommentAgentInvocationInput,
) -> schemas.FailCommentAgentInvocationResult:
    _FAILURES.append(input)
    raise ApplicationError("failure persistence exploded", non_retryable=True)


@workflow.defn(name="DurableAgentWorkflow")
class SuccessfulAgentWorkflow:
    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        blocked_actions = {
            "core.cases.create_comment",
            "core.cases.reply_to_comment",
        }
        config_actions = (
            args.agent_args.config.actions if args.agent_args.config else None
        )
        assert blocked_actions.isdisjoint(config_actions or [])
        assert blocked_actions.isdisjoint(args.tools or [])
        if config_actions is not None:
            assert "core.cases.get_case" in config_actions
        if args.tools is not None:
            assert "core.cases.get_case" in args.tools
        return AgentOutput(
            output="child output",
            duration=0,
            session_id=args.agent_args.session_id,
        )


@workflow.defn(name="DurableAgentWorkflow")
class WaitingAgentWorkflow:
    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        await workflow.execute_activity(
            wait_for_child_release,
            start_to_close_timeout=timedelta(seconds=5),
        )
        return AgentOutput(
            output="released output",
            duration=0,
            session_id=args.agent_args.session_id,
        )


@workflow.defn(name="DurableAgentWorkflow")
class CancellationWaitingAgentWorkflow:
    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        await workflow.execute_activity(
            record_child_wait,
            start_to_close_timeout=timedelta(seconds=5),
        )
        await workflow.wait_condition(lambda: False)
        raise AssertionError("Unreachable")


@workflow.defn(name="DurableAgentWorkflow")
class FailingAgentWorkflow:
    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        raise ApplicationError("agent_turn exploded", non_retryable=True)


def root_cause(error: BaseException) -> BaseException:
    while error.__cause__ is not None:
        error = error.__cause__
    return error


@pytest.mark.anyio
async def test_parent_runs_child_then_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _EVENTS.clear()
    invocation_id = uuid.uuid4()
    queue = f"comment-agent-parent-{invocation_id}"
    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", queue)
    role = Role(type="service", service_id="tracecat-api")

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        async with Worker(
            env.client,
            task_queue=queue,
            activities=[prepare_turn, complete_turn],
            workflows=[
                invocation_workflows.CaseCommentAgentInvocationWorkflow,
                SuccessfulAgentWorkflow,
            ],
            workflow_runner=new_sandbox_runner(),
        ):
            result = await env.client.execute_workflow(
                invocation_workflows.CaseCommentAgentInvocationWorkflow.run,
                schemas.CaseCommentAgentInvocationWorkflowInput(
                    role=role, invocation_id=invocation_id
                ),
                id=f"parent/{invocation_id}",
                task_queue=queue,
            )

    assert result.handled and result.reply_comment_id == invocation_id
    assert _EVENTS == ["prepare", "complete:child output"]


@pytest.mark.anyio
async def test_parent_waits_while_child_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global _CHILD_WAITING, _RELEASE_CHILD
    _EVENTS.clear()
    _CHILD_WAITING = asyncio.Event()
    _RELEASE_CHILD = asyncio.Event()
    invocation_id = uuid.uuid4()
    queue = f"comment-agent-waiting-{invocation_id}"
    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", queue)
    role = Role(type="service", service_id="tracecat-api")

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        async with Worker(
            env.client,
            task_queue=queue,
            activities=[prepare_turn, wait_for_child_release, complete_turn],
            workflows=[
                invocation_workflows.CaseCommentAgentInvocationWorkflow,
                WaitingAgentWorkflow,
            ],
            workflow_runner=new_sandbox_runner(),
        ):
            handle = await env.client.start_workflow(
                invocation_workflows.CaseCommentAgentInvocationWorkflow.run,
                schemas.CaseCommentAgentInvocationWorkflowInput(
                    role=role, invocation_id=invocation_id
                ),
                id=f"parent/{invocation_id}",
                task_queue=queue,
            )
            result_task = asyncio.create_task(handle.result())
            await asyncio.wait_for(_CHILD_WAITING.wait(), timeout=5)

            assert _EVENTS == ["prepare", "child_waiting"]
            assert not result_task.done()

            _RELEASE_CHILD.set()
            result = await asyncio.wait_for(result_task, timeout=5)

    assert result.handled and result.reply_comment_id == invocation_id
    assert _EVENTS == [
        "prepare",
        "child_waiting",
        "complete:released output",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["preparation", "agent_turn", "completion"])
async def test_parent_routes_failure_stage_and_preserves_error(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    _FAILURES.clear()
    invocation_id = uuid.uuid4()
    queue = f"comment-agent-{stage}-{invocation_id}"
    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", queue)
    role = Role(type="service", service_id="tracecat-api")
    prepare_activity = fail_prepare_turn if stage == "preparation" else prepare_turn
    complete_activity = fail_complete_turn if stage == "completion" else complete_turn
    child_workflow = (
        FailingAgentWorkflow if stage == "agent_turn" else SuccessfulAgentWorkflow
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        async with Worker(
            env.client,
            task_queue=queue,
            activities=[prepare_activity, complete_activity, record_failure],
            workflows=[
                invocation_workflows.CaseCommentAgentInvocationWorkflow,
                child_workflow,
            ],
            workflow_runner=new_sandbox_runner(),
        ):
            with pytest.raises(WorkflowFailureError) as exc_info:
                await env.client.execute_workflow(
                    invocation_workflows.CaseCommentAgentInvocationWorkflow.run,
                    schemas.CaseCommentAgentInvocationWorkflowInput(
                        role=role, invocation_id=invocation_id
                    ),
                    id=f"parent/{invocation_id}",
                    task_queue=queue,
                )

    assert len(_FAILURES) == 1
    assert _FAILURES[0].invocation_id == invocation_id
    assert _FAILURES[0].kind == stage
    original_error = root_cause(exc_info.value)
    assert isinstance(original_error, ApplicationError)
    assert f"{stage} exploded" in str(original_error)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("origin", "cleanup_fails"),
    [("cancelled", False), ("agent_turn", True), ("cancelled", True)],
)
async def test_parent_preserves_failure_across_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
    cleanup_fails: bool,
) -> None:
    global _CHILD_WAITING
    _FAILURES.clear()
    _CHILD_WAITING = asyncio.Event()
    invocation_id = uuid.uuid4()
    queue = f"comment-agent-{origin}-cleanup-{cleanup_fails}-{invocation_id}"
    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", queue)
    role = Role(type="service", service_id="tracecat-api")
    child_workflow = (
        FailingAgentWorkflow
        if origin == "agent_turn"
        else CancellationWaitingAgentWorkflow
    )
    failure_activity = fail_record_failure if cleanup_fails else record_failure
    activities = [prepare_turn, complete_turn, failure_activity]
    if origin == "cancelled":
        activities.append(record_child_wait)

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        async with Worker(
            env.client,
            task_queue=queue,
            activities=activities,
            workflows=[
                invocation_workflows.CaseCommentAgentInvocationWorkflow,
                child_workflow,
            ],
            workflow_runner=new_sandbox_runner(),
        ):
            handle = await env.client.start_workflow(
                invocation_workflows.CaseCommentAgentInvocationWorkflow.run,
                schemas.CaseCommentAgentInvocationWorkflowInput(
                    role=role, invocation_id=invocation_id
                ),
                id=f"parent/{invocation_id}",
                task_queue=queue,
            )
            if origin == "cancelled":
                await asyncio.wait_for(_CHILD_WAITING.wait(), timeout=5)
                await handle.cancel()
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()

    assert len(_FAILURES) == 1
    assert _FAILURES[0].kind == origin
    original_error = root_cause(exc_info.value)
    if origin == "cancelled":
        assert isinstance(original_error, CancelledError)
    else:
        assert isinstance(original_error, ApplicationError)
        assert "agent_turn exploded" in str(original_error)


@pytest.mark.anyio
async def test_failure_cleanup_survives_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def slow_cleanup(*args: object, **kwargs: object) -> None:
        cleanup_started.set()
        await release_cleanup.wait()

    monkeypatch.setattr(
        invocation_workflows.workflow,
        "execute_activity",
        slow_cleanup,
    )
    invocation_id = uuid.uuid4()
    cleanup_task = asyncio.create_task(
        invocation_workflows.CaseCommentAgentInvocationWorkflow()._record_failure(
            schemas.CaseCommentAgentInvocationWorkflowInput(
                role=Role(type="service", service_id="tracecat-api"),
                invocation_id=invocation_id,
            ),
            "cancelled",
            "cancelled",
        )
    )

    await cleanup_started.wait()
    for _ in range(3):
        cleanup_task.cancel()
        await asyncio.sleep(0)
        assert not cleanup_task.done()

    release_cleanup.set()
    await cleanup_task


@pytest.mark.anyio
@pytest.mark.integration
async def test_comment_mention_runs_agent_and_posts_reply(
    monkeypatch: pytest.MonkeyPatch,
    svc_role: Role,
) -> None:
    role = svc_role.model_copy(
        update={
            "type": "service",
            "user_id": None,
            "scopes": frozenset({"case:update", "agent:execute"}),
        }
    )
    assert role.workspace_id is not None
    invocation_id = uuid.uuid4()
    queue = f"comment-agent-e2e-{invocation_id}"
    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", queue)
    temporal_client_mock = AsyncMock()
    monkeypatch.setattr(invocation_queue, "get_temporal_client", temporal_client_mock)
    monkeypatch.setattr(
        AgentManagementService,
        "get_workspace_runtime_provider_credentials",
        AsyncMock(return_value={"OPENAI_API_KEY": "test-key"}),
    )
    monkeypatch.setattr("tracecat.cases.events.publish_case_event_payload", AsyncMock())
    monkeypatch.setattr(
        "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
        Mock(),
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter()
    ) as env:
        temporal_client_mock.return_value = env.client
        async with Worker(
            env.client,
            task_queue=queue,
            activities=[
                invocation_activities.prepare_comment_agent_invocation_activity,
                invocation_activities.complete_comment_agent_invocation_activity,
                invocation_activities.fail_comment_agent_invocation_activity,
            ],
            workflows=[
                invocation_workflows.CaseCommentAgentInvocationWorkflow,
                SuccessfulAgentWorkflow,
            ],
            workflow_runner=new_sandbox_runner(),
        ):
            async with CaseCommentsService.with_session(role=role) as comments:
                preset = AgentPreset(
                    workspace_id=role.workspace_id,
                    name="Deterministic agent",
                    slug=f"deterministic-{invocation_id}",
                    model_name="test-model",
                    model_provider="openai",
                )
                comments.session.add(preset)
                await comments.session.flush()
                version = AgentPresetVersion(
                    workspace_id=role.workspace_id,
                    preset_id=preset.id,
                    version=1,
                    instructions="Return a deterministic answer.",
                    model_name=preset.model_name,
                    model_provider=preset.model_provider,
                    actions=[
                        "core.cases.create_comment",
                        "core.cases.get_case",
                        "core.cases.reply_to_comment",
                    ],
                )
                case = Case(
                    workspace_id=role.workspace_id,
                    case_number=1,
                    summary="Deterministic case",
                    description="End-to-end comment invocation",
                    priority=CasePriority.MEDIUM,
                    severity=CaseSeverity.LOW,
                    status=CaseStatus.NEW,
                )
                comments.session.add_all([version, case])
                await comments.session.flush()
                preset.current_version_id = version.id
                await comments.session.commit()
                comment = await comments.create_comment(
                    case,
                    CaseCommentCreate(content=f"[@Agent](mention://agent/{preset.id})"),
                )
                invocation = await comments.session.scalar(
                    select(CaseCommentAgentInvocation)
                    .join(CaseCommentMention)
                    .where(CaseCommentMention.comment_id == comment.id)
                )
                assert invocation is not None
                invocation_id = invocation.id
                handle = env.client.get_workflow_handle(
                    schemas.comment_agent_invocation_workflow_id(invocation_id)
                )
                async with asyncio.timeout(5):
                    while True:
                        try:
                            await handle.describe()
                            break
                        except RPCError:
                            await asyncio.sleep(0.01)
                    await handle.result()

                invocation, mention, agent_session, reply = (
                    await comments.session.execute(
                        select(
                            CaseCommentAgentInvocation,
                            CaseCommentMention,
                            AgentSession,
                            CaseComment,
                        )
                        .select_from(CaseCommentAgentInvocation)
                        .join(
                            CaseCommentMention,
                            CaseCommentMention.id
                            == CaseCommentAgentInvocation.mention_id,
                        )
                        .join(
                            AgentSession,
                            AgentSession.id == CaseCommentAgentInvocation.session_id,
                        )
                        .join(
                            CaseComment,
                            CaseComment.id
                            == CaseCommentAgentInvocation.reply_comment_id,
                        )
                        .where(CaseCommentAgentInvocation.id == invocation_id)
                        .execution_options(populate_existing=True)
                    )
                ).one()
                assert invocation.status == CaseCommentAgentInvocationStatus.SUCCEEDED
                assert mention.target_id == preset.id
                assert agent_session.agent_preset_version_id == version.id
                assert reply.parent_id == comment.id and reply.content == "child output"
                await comments.session.execute(delete(Case).where(Case.id == case.id))
                await comments.session.commit()
