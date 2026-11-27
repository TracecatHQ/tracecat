from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

import tracecat.dsl.workflow as dsl_workflow_module
from tracecat import config
from tracecat.admission.types import ConcurrencyResource, NotAdmitted
from tracecat.admission.waiting import RunAdmission
from tracecat.auth.types import Role
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.action import DSLActivities, _evaluate_loop_iterations
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import FailStrategy, PlatformAction, WaitStrategy
from tracecat.dsl.init_activities import RunLimitsSnapshot
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionRetryPolicy,
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    RunActionInput,
    RunContext,
    TaskResult,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.dsl.workflow_logging import get_workflow_logger
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import extract_error_classification
from tracecat.temporal.exceptions import UserError
from tracecat.temporal.patches import WorkflowPatch
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.workflow.executions.enums import ExecutionType, TriggerType


def _build_workflow(*, max_actions_per_run: int | None = None) -> DSLWorkflow:
    workflow = object.__new__(DSLWorkflow)
    workflow.role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    assert workflow.role.organization_id is not None
    workflow.organization_id = workflow.role.organization_id
    workflow.logger = get_workflow_logger()
    workflow.runtime_config = DSLConfig()
    workflow._max_actions_per_run = max_actions_per_run
    workflow._action_execution_count = 0
    workflow._action_admission = RunAdmission(
        resource=ConcurrencyResource.ACTIONS, logger=workflow.logger
    )
    workflow.execution_type = ExecutionType.PUBLISHED
    workflow.run_context = RunContext(
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000001"),
        wf_exec_id="wf-00000000000000000000000000000001:exec-00000000000000000000000000000001",
        wf_run_id=uuid.uuid4(),
        environment="__TEST__",
        logical_time=datetime.now(UTC),
    )
    context = ExecutionContext(ACTIONS={}, TRIGGER=None)
    workflow.context = context
    workflow.scheduler = cast(Any, SimpleNamespace(streams={ROOT_STREAM: context}))
    assert workflow.role.workspace_id is not None
    workflow.workspace_id = workflow.role.workspace_id
    workflow.wf_exec_id = workflow.run_context.wf_exec_id
    return workflow


def _child_dsl() -> DSLInput:
    return DSLInput(
        title="Child",
        description="child workflow for unit test",
        entrypoint=DSLEntrypoint(ref="noop", expects={}),
        actions=[
            ActionStatement(
                ref="noop",
                action="core.transform.reshape",
                args={"value": "ok"},
            )
        ],
        triggers=[],
    )


def _prepared_subflow_stub(
    *,
    get_config: Callable[[int], DSLConfig] | None = None,
    get_trigger_input_at: Callable[[int], InlineObject] | None = None,
) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            dsl=_child_dsl(),
            wf_id=WorkflowUUID.new("wf-00000000000000000000000000000001"),
            registry_lock=None,
            get_config=get_config or (lambda _idx: DSLConfig()),
            get_trigger_input_at=get_trigger_input_at
            or (lambda idx: InlineObject(data={"index": idx})),
        ),
    )


def _activity_error_from(
    cause: Exception, *, activity_type: str = "prepare_subflow_activity"
) -> ActivityError:
    try:
        raise ActivityError(
            "Activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test",
            activity_type=activity_type,
            activity_id=activity_type,
            retry_state=None,
        ) from cause
    except ActivityError as e:
        return e


def _user_error_from(cause: Exception, message: str) -> UserError:
    try:
        raise UserError(message) from cause
    except UserError as e:
        return e


@contextmanager
def _prepared_run_workflow(
    workflow: DSLWorkflow, dsl: DSLInput
) -> Iterator[DSLRunArgs]:
    """Stub workflow info and dependency compilation around ``_run_workflow``."""
    time_anchor = datetime.now(UTC)
    run_args = DSLRunArgs(
        role=workflow.role,
        dsl=dsl,
        wf_id=workflow.run_context.wf_id,
        time_anchor=time_anchor,
    )
    workflow.dsl = dsl
    workflow.dispatch_type = "push"
    workflow.wf_run_id = str(workflow.run_context.wf_run_id)
    workflow.start_to_close_timeout = run_args.timeout
    workflow_info = SimpleNamespace(
        start_time=time_anchor,
        workflow_id=workflow.run_context.wf_exec_id,
        run_id=str(workflow.run_context.wf_run_id),
        execution_timeout=None,
    )
    with (
        patch("tracecat.dsl.workflow.workflow.info", return_value=workflow_info),
        patch(
            "tracecat.dsl.workflow.get_trigger_type",
            return_value=TriggerType.MANUAL,
        ),
        patch.object(workflow, "_compile_dependencies", new=AsyncMock()),
    ):
        yield run_args


@pytest.mark.anyio
async def test_retry_until_enforces_action_execution_limit() -> None:
    workflow = _build_workflow(max_actions_per_run=2)
    task = ActionStatement(
        ref="retry_action",
        action="core.transform.reshape",
        retry_policy=ActionRetryPolicy(
            retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}"
        ),
    )

    execute_task_mock = AsyncMock(
        side_effect=[
            TaskResult.from_result({"status": "loading"}),
            TaskResult.from_result({"status": "loading"}),
            TaskResult.from_result({"status": "success"}),
        ]
    )

    with (
        patch.object(workflow, "_execute_task", new=execute_task_mock),
        patch.object(workflow, "_set_logical_time_context", return_value=None),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=AsyncMock(side_effect=[False, False, True]),
        ),
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await workflow.execute_task(task)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.kind is RuntimeErrorKind.TENANT_QUOTA_EXHAUSTED
    assert execute_task_mock.await_count == 2
    assert workflow._action_execution_count == 3


@pytest.mark.anyio
@pytest.mark.parametrize("preserve_cancellation", [True, False])
async def test_execute_task_preserves_temporal_cancellation_with_replay_gate(
    preserve_cancellation: bool,
) -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="cancelled_action", action="core.transform.reshape")
    activity_error = _activity_error_from(CancelledError("cancelled by sibling"))

    with (
        patch.object(workflow, "_handle_timers", new=AsyncMock(return_value=None)),
        patch.object(
            workflow, "_run_action", new=AsyncMock(side_effect=activity_error)
        ),
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=preserve_cancellation,
        ) as patched,
    ):
        if preserve_cancellation:
            with pytest.raises(ActivityError) as exc_info:
                await workflow._execute_task(task)
            assert exc_info.value is activity_error
        else:
            with pytest.raises(ApplicationError) as exc_info:
                await workflow._execute_task(task)
            assert exc_info.value.type == CancelledError.__name__

    patched.assert_called_once_with(WorkflowPatch.PRESERVE_TEMPORAL_CANCELLATION)


@pytest.mark.anyio
async def test_run_workflow_preserves_scheduler_cancellation() -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="noop", action="core.noop")
    dsl = DSLInput(
        title="Scheduler cancellation",
        description="preserve native cancellation",
        entrypoint=DSLEntrypoint(ref=task.ref),
        actions=[task],
    )
    cancellation = _activity_error_from(CancelledError("cancelled by sibling"))
    scheduler = SimpleNamespace(start=AsyncMock(side_effect=cancellation))

    with (
        _prepared_run_workflow(workflow, dsl) as run_args,
        patch("tracecat.dsl.workflow.DSLScheduler", return_value=scheduler),
        pytest.raises(ActivityError) as exc_info,
    ):
        await workflow._run_workflow(run_args)

    assert exc_info.value is cancellation


@pytest.mark.anyio
async def test_run_workflow_classifies_stored_dependency_cycle_as_user_error() -> None:
    workflow = _build_workflow()
    dsl = DSLInput(
        title="Stored cycle",
        description="published before commit-time cycle validation",
        entrypoint=DSLEntrypoint(ref="start"),
        actions=[
            ActionStatement(ref="start", action="core.noop"),
            ActionStatement(ref="a", action="core.noop", depends_on=["start", "b"]),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
        ],
    )

    with (
        _prepared_run_workflow(workflow, dsl) as run_args,
        pytest.raises(ApplicationError) as exc_info,
    ):
        await workflow._run_workflow(run_args)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_DEFINITION_INVALID
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classification.message.startswith(
        "The workflow definition is invalid: Cycle detected"
    )
    assert exc_info.value.non_retryable


@pytest.mark.anyio
@pytest.mark.parametrize("preserve_cancellation", [True, False])
@pytest.mark.parametrize(
    "error_kind", ["cancellation", "wrapped_cancellation", "failure"]
)
async def test_run_workflow_return_error_classification(
    preserve_cancellation: bool, error_kind: str
) -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="noop", action="core.noop")
    dsl = DSLInput(
        title="Return cancellation",
        description="preserve native cancellation",
        entrypoint=DSLEntrypoint(ref=task.ref),
        actions=[task],
    )
    error: Exception
    match error_kind:
        case "cancellation":
            error = CancelledError("cancelled by sibling")
        case "wrapped_cancellation":
            error = _activity_error_from(CancelledError("cancelled by sibling"))
        case _:
            error = _activity_error_from(RuntimeError("return storage failed"))
    scheduler = SimpleNamespace(start=AsyncMock(return_value=None))

    with (
        _prepared_run_workflow(workflow, dsl) as run_args,
        patch("tracecat.dsl.workflow.DSLScheduler", return_value=scheduler),
        patch.object(workflow, "_handle_return", new=AsyncMock(side_effect=error)),
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=preserve_cancellation,
        ) as patched,
    ):
        if preserve_cancellation and error_kind != "failure":
            with pytest.raises(type(error)) as exc_info:
                await workflow._run_workflow(run_args)
            assert exc_info.value is error
        else:
            with pytest.raises(ApplicationError) as classified_exc_info:
                await workflow._run_workflow(run_args)
            classification = extract_error_classification(classified_exc_info.value)
            assert classification is not None
            assert classification.owner is RuntimeErrorOwner.PLATFORM
            assert (
                classification.kind
                is RuntimeErrorKind.WORKFLOW_RUNTIME_INVARIANT_VIOLATION
            )

    if error_kind == "failure":
        patched.assert_not_called()
    else:
        patched.assert_called_once_with(WorkflowPatch.PRESERVE_RETURN_CANCELLATION)


@pytest.mark.anyio
@pytest.mark.parametrize("control_flow_patch", [True, False])
async def test_prepare_subflow_activity_failure_control_flow_is_replay_gated(
    control_flow_patch: bool,
) -> None:
    workflow = _build_workflow()
    dsl = DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action=PlatformAction.TRANSFORM_SCATTER,
                args={"collection": ["item"]},
            ),
            ActionStatement(
                ref="call_child",
                action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                args={"workflow_alias": "child"},
                depends_on=["scatter"],
            ),
            ActionStatement(
                ref="handle_error",
                action="core.noop",
                depends_on=["call_child.error"],
            ),
        ],
    )
    scheduler = DSLScheduler(
        executor=workflow.execute_task,
        dsl=dsl,
        max_pending_tasks=16,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=workflow.role,
        run_context=workflow.run_context,
    )
    workflow.dsl = dsl
    workflow.scheduler = scheduler
    executed_refs: list[str] = []

    async def execute_activity(activity: object, *_: Any, **__: Any) -> object:
        if activity == DSLActivities.handle_scatter_input_activity:
            return InlineObject(data=["item"], typename="list")
        if activity == DSLActivities.prepare_subflow_activity:
            raise _activity_error_from(RuntimeError("prepare failed"))
        raise AssertionError(f"Unexpected activity: {activity}")

    async def run_action(task: ActionStatement) -> InlineObject:
        executed_refs.append(task.ref)
        return InlineObject(data={"handled": True})

    with (
        patch(
            "tracecat.dsl.scheduler.workflow.execute_activity",
            new=AsyncMock(side_effect=execute_activity),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=AsyncMock(side_effect=execute_activity),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=control_flow_patch,
        ) as patched,
        patch.object(workflow, "_run_action", new=AsyncMock(side_effect=run_action)),
    ):
        task_exceptions = await scheduler.start()

    if control_flow_patch:
        assert task_exceptions is None
        assert executed_refs == ["handle_error"]
        assert not scheduler.task_exceptions
    else:
        assert task_exceptions is not None
        assert "call_child" in task_exceptions
        assert "prepare failed" in task_exceptions["call_child"].details.message
        assert executed_refs == []
    patched.assert_called_once_with(WorkflowPatch.ERROR_OWNER_CONTROL_FLOW)
    assert not scheduler.stream_exceptions


@pytest.mark.anyio
@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(
            lambda: UserError("Workflow alias 'child' not found"), id="user_error"
        ),
        pytest.param(
            lambda: _user_error_from(
                ValueError("Invalid for_each expression"),
                "Error evaluating subflow for_each expression: Invalid for_each expression",
            ),
            id="user_error_cause",
        ),
    ],
)
async def test_prepare_subflow_user_error_in_scatter_uses_error_path(
    make_error: Callable[[], UserError],
) -> None:
    workflow = _build_workflow()
    dsl = DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action=PlatformAction.TRANSFORM_SCATTER,
                args={"collection": ["item"]},
            ),
            ActionStatement(
                ref="call_child",
                action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                args={"workflow_alias": "child"},
                depends_on=["scatter"],
            ),
            ActionStatement(
                ref="handle_error",
                action="core.noop",
                depends_on=["call_child.error"],
            ),
        ],
    )
    scheduler = DSLScheduler(
        executor=workflow.execute_task,
        dsl=dsl,
        max_pending_tasks=16,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=workflow.role,
        run_context=workflow.run_context,
    )
    workflow.dsl = dsl
    workflow.scheduler = scheduler
    executed_refs: list[str] = []

    async def execute_activity(activity: object, *_: Any, **__: Any) -> object:
        if activity == DSLActivities.handle_scatter_input_activity:
            return InlineObject(data=["item"], typename="list")
        if activity == DSLActivities.prepare_subflow_activity:
            raise _activity_error_from(make_error())
        raise AssertionError(f"Unexpected activity: {activity}")

    async def run_action(task: ActionStatement) -> InlineObject:
        executed_refs.append(task.ref)
        return InlineObject(data={"handled": True})

    with (
        patch(
            "tracecat.dsl.scheduler.workflow.execute_activity",
            new=AsyncMock(side_effect=execute_activity),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=AsyncMock(side_effect=execute_activity),
        ),
        patch.object(workflow, "_run_action", new=AsyncMock(side_effect=run_action)),
    ):
        task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert executed_refs == ["handle_error"]
    assert not scheduler.task_exceptions
    assert not scheduler.stream_exceptions


def test_evaluate_loop_iterations_invalid_for_each_raises_user_error() -> None:
    task = ActionStatement(
        ref="call_child",
        action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
        args={"workflow_alias": "child"},
        for_each="${{ [1, 2, 3] }}",
    )

    with pytest.raises(UserError, match="Error evaluating subflow for_each expression"):
        _evaluate_loop_iterations(
            task,
            materialized=cast(Any, {"ACTIONS": {}, "TRIGGER": None}),
            dsl_config=DSLConfig(),
        )


def _run_boundary_args(workflow: DSLWorkflow) -> DSLRunArgs:
    task = ActionStatement(
        ref="noop",
        action="core.transform.reshape",
        args={"value": "ok"},
    )
    dsl = DSLInput(
        title="Run boundary",
        description="run limits boundary test",
        entrypoint=DSLEntrypoint(ref="noop", expects={}),
        actions=[task],
        triggers=[],
    )
    return DSLRunArgs(
        role=workflow.role,
        dsl=dsl,
        wf_id=workflow.run_context.wf_id,
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={"core.transform.reshape": "tracecat_registry"},
        ),
    )


@pytest.mark.anyio
async def test_run_resolves_limits_in_one_local_activity() -> None:
    """Patched runs read the budget from one local activity, no regular activity."""
    workflow = _build_workflow()
    run_result = InlineObject(data={"ok": True})
    local_activity_mock = AsyncMock(
        return_value=RunLimitsSnapshot(max_action_executions_per_workflow=7)
    )
    execute_activity_mock = AsyncMock()

    with (
        patch.object(workflow, "_initialize_run", return_value=None),
        patch.object(
            workflow,
            "_resolve_organization_id",
            new=AsyncMock(return_value=workflow.organization_id),
        ),
        patch("tracecat.dsl.workflow.workflow.patched", return_value=True),
        patch(
            "tracecat.dsl.workflow.workflow.execute_local_activity",
            new=local_activity_mock,
        ),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=execute_activity_mock,
        ),
        patch.object(workflow, "_run_workflow", new=AsyncMock(return_value=run_result)),
    ):
        result = await workflow.run(_run_boundary_args(workflow))

    assert local_activity_mock.await_count == 1
    assert (
        local_activity_mock.await_args_list[0].args[0]
        is dsl_workflow_module.resolve_run_limits_activity
    )
    execute_activity_mock.assert_not_awaited()
    assert workflow._max_actions_per_run == 7
    assert isinstance(result, InlineObject)
    assert result.data == {"ok": True}


@pytest.mark.anyio
@pytest.mark.parametrize("flag_enabled", [False, True])
async def test_run_pre_patch_history_keeps_flag_snapshot(flag_enabled: bool) -> None:
    """Pre-patch histories replay the flag snapshot and, when on, the tier lookup."""
    workflow = _build_workflow()
    run_result = InlineObject(data={"ok": True})
    execute_activity_mock = AsyncMock(
        return_value=EffectiveLimits(
            api_rate_limit=None,
            api_burst_capacity=None,
            max_concurrent_workflows=1,
            max_action_executions_per_workflow=3,
            max_concurrent_actions=1,
        )
    )

    with (
        patch.object(workflow, "_initialize_run", return_value=None),
        patch.object(
            workflow,
            "_resolve_organization_id",
            new=AsyncMock(return_value=workflow.organization_id),
        ),
        patch("tracecat.dsl.workflow.workflow.patched", return_value=False),
        patch(
            "tracecat.dsl.workflow.workflow.execute_local_activity",
            new=AsyncMock(return_value=flag_enabled),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=execute_activity_mock,
        ),
        patch.object(workflow, "_run_workflow", new=AsyncMock(return_value=run_result)),
    ):
        await workflow.run(_run_boundary_args(workflow))

    if flag_enabled:
        execute_activity_mock.assert_awaited_once()
        assert (
            execute_activity_mock.await_args_list[0].args[0]
            is dsl_workflow_module.get_tier_limits_activity
        )
        assert workflow._max_actions_per_run == 3
    else:
        execute_activity_mock.assert_not_awaited()
        assert workflow._max_actions_per_run is None
    # Pre-patch histories never throttle outstanding actions.
    assert workflow._run_throttle is None


async def _resolve_limits_from(
    workflow: DSLWorkflow, snapshot: RunLimitsSnapshot
) -> None:
    with (
        patch("tracecat.dsl.workflow.workflow.patched", return_value=True),
        patch(
            "tracecat.dsl.workflow.workflow.execute_local_activity",
            new=AsyncMock(return_value=snapshot),
        ),
    ):
        await workflow._resolve_run_limits()


@pytest.mark.anyio
async def test_snapshot_recorded_before_the_field_gives_no_run_throttle() -> None:
    """A history recorded before the field decodes it as None: no throttle on replay."""
    converter = get_data_converter(compression_enabled=False)
    payloads = await converter.encode([{"max_action_executions_per_workflow": 7}])
    (snapshot,) = await converter.decode(payloads, [RunLimitsSnapshot])
    assert isinstance(snapshot, RunLimitsSnapshot)
    assert snapshot.max_concurrent_actions is None

    workflow = _build_workflow()
    await _resolve_limits_from(workflow, snapshot)

    assert workflow._max_actions_per_run == 7
    assert workflow._run_throttle is None


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [None, 0, -1])
async def test_missing_or_non_positive_limit_means_no_run_throttle(
    limit: int | None,
) -> None:
    """As in the gate, a limit of 0 or less is no limit."""
    workflow = _build_workflow()
    await _resolve_limits_from(
        workflow, RunLimitsSnapshot(max_concurrent_actions=limit)
    )
    assert workflow._run_throttle is None


@dataclass
class _FakeExecutor:
    """Admits up to ``limit`` attempts at once, like the gate for one run."""

    limit: int
    in_use: int = 0
    max_in_use: int = 0
    attempts: int = 0
    busy: int = 0

    async def execute_activity(self, *_args: Any, **_kwargs: Any) -> Any:
        self.attempts += 1
        if self.in_use >= self.limit:
            self.busy += 1
            return NotAdmitted(
                resource=ConcurrencyResource.ACTIONS,
                in_use=self.in_use,
                limit=self.limit,
                attempt=1,
            ).model_dump(mode="json")
        self.in_use += 1
        self.max_in_use = max(self.max_in_use, self.in_use)
        try:
            # Stay running for a few loop turns so dispatches overlap.
            for _ in range(3):
                await asyncio.sleep(0)
            return {"ok": True}
        finally:
            # The executor frees the lease before its result reaches the run.
            self.in_use -= 1


@pytest.mark.anyio
async def test_run_throttle_keeps_a_fan_out_at_the_org_limit_without_bounces() -> None:
    """200 items at limit 5: at most 5 outstanding, one attempt each, no bounce."""
    workflow = _build_workflow()
    await _resolve_limits_from(workflow, RunLimitsSnapshot(max_concurrent_actions=5))
    executor = _FakeExecutor(limit=5)
    task = ActionStatement(ref="item", action="core.transform.reshape")
    arg = cast(RunActionInput, MagicMock())

    with patch(
        "tracecat.dsl.workflow.workflow.execute_activity",
        new=executor.execute_activity,
    ):
        results = await asyncio.gather(
            *(workflow._execute_action_activity_once(task, arg) for _ in range(200))
        )

    assert results == [{"ok": True}] * 200
    assert executor.busy == 0
    assert executor.attempts == 200
    assert executor.max_in_use == 5


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("limit", "wake_recorded", "wake_enabled"),
    [
        (5, True, True),
        (5, False, False),  # recorded before the wake
        (None, True, False),  # no limit: never refused, so never queued
        (0, True, False),  # 0 or less is no limit, as in the gate
        (-1, True, False),
    ],
)
async def test_wake_needs_its_marker_and_a_limit(
    limit: int | None, wake_recorded: bool, wake_enabled: bool
) -> None:
    workflow = _build_workflow()
    asked: list[str] = []

    def patched(patch_id: str) -> bool:
        asked.append(patch_id)
        return patch_id != WorkflowPatch.ADMISSION_WAKE_ON_RELEASE or wake_recorded

    with (
        patch("tracecat.dsl.workflow.workflow.patched", side_effect=patched),
        patch(
            "tracecat.dsl.workflow.workflow.execute_local_activity",
            new=AsyncMock(return_value=RunLimitsSnapshot(max_concurrent_actions=limit)),
        ),
    ):
        await workflow._resolve_run_limits()

    assert workflow._admission_wake_enabled is wake_enabled
    assert workflow._action_admission.wake_enabled is wake_enabled
    # A run that can't be refused records no marker.
    has_limit = limit is not None and limit > 0
    assert (WorkflowPatch.ADMISSION_WAKE_ON_RELEASE in asked) is has_limit


@pytest.mark.anyio
@pytest.mark.parametrize("wake_enabled", [True, False])
async def test_only_a_waking_run_asks_to_be_queued(wake_enabled: bool) -> None:
    workflow = _build_workflow()
    if wake_enabled:
        await _resolve_limits_from(
            workflow, RunLimitsSnapshot(max_concurrent_actions=5)
        )
    arg = MagicMock()
    sent = MagicMock()
    arg.model_copy.return_value = sent
    execute_activity = AsyncMock(return_value={"ok": True})
    task = ActionStatement(ref="item", action="core.transform.reshape")

    with patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute_activity):
        await workflow._execute_action_activity_once(task, cast(RunActionInput, arg))

    sent_call = execute_activity.await_args
    assert sent_call is not None
    run_input = sent_call.kwargs["args"][0]
    if wake_enabled:
        arg.model_copy.assert_called_once_with(update={"wake_on_release": True})
        assert run_input is sent
    else:
        arg.model_copy.assert_not_called()
        assert run_input is arg


def test_wake_signal_for_a_run_without_the_wake_is_ignored() -> None:
    workflow = _build_workflow()
    workflow._action_admission._active = 1  # pyright: ignore[reportPrivateUsage]

    workflow._on_admission_wake()

    assert workflow._action_admission._woken is False  # pyright: ignore[reportPrivateUsage]


def test_resolve_child_loop_batch_plan_uses_dispatch_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plan is (requested batch size, configured dispatch window)."""
    workflow = _build_workflow()
    monkeypatch.setattr(config, "TRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW", 12)

    assert workflow._resolve_child_loop_batch_plan(
        total_count=10,
        requested_batch_size=6,
    ) == (6, 12)


@pytest.mark.anyio
async def test_execute_child_workflow_batch_prepared_limits_dispatch_window() -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="run_child", action="core.workflow.execute", args={})
    prepared = _prepared_subflow_stub()

    dispatch_in_flight = 0
    max_dispatch_in_flight = 0
    child_runs_in_flight = 0
    max_child_runs_in_flight = 0
    dispatch_lock = asyncio.Lock()
    child_runs_lock = asyncio.Lock()

    async def child_run(loop_index: int) -> InlineObject:
        nonlocal child_runs_in_flight, max_child_runs_in_flight
        async with child_runs_lock:
            child_runs_in_flight += 1
            max_child_runs_in_flight = max(
                max_child_runs_in_flight,
                child_runs_in_flight,
            )
        try:
            await asyncio.sleep(0.05)
            return InlineObject(data={"index": loop_index})
        finally:
            async with child_runs_lock:
                child_runs_in_flight -= 1

    async def dispatch_child_mock(
        _: ActionStatement,
        __: Any,
        *,
        wait_strategy: Any,
        loop_index: int | None = None,
    ) -> asyncio.Task[InlineObject]:
        del wait_strategy
        nonlocal dispatch_in_flight, max_dispatch_in_flight
        assert loop_index is not None
        async with dispatch_lock:
            dispatch_in_flight += 1
            max_dispatch_in_flight = max(max_dispatch_in_flight, dispatch_in_flight)
        try:
            await asyncio.sleep(0.01)
            return asyncio.create_task(child_run(loop_index))
        finally:
            async with dispatch_lock:
                dispatch_in_flight -= 1

    with patch.object(
        workflow,
        "_dispatch_child_workflow",
        new=AsyncMock(side_effect=dispatch_child_mock),
    ):
        result = await workflow._execute_child_workflow_batch_prepared(
            task=task,
            prepared=prepared,
            batch_start=0,
            batch_size=6,
            dispatch_window=2,
            wait_strategy=WaitStrategy.WAIT,
            fail_strategy=FailStrategy.ISOLATED,
            child_time_anchor=datetime.now(UTC),
        )

    assert max_dispatch_in_flight == 2
    assert max_child_runs_in_flight > 2
    assert [cast(InlineObject, val).data["index"] for val in result] == list(range(6))


@pytest.mark.anyio
async def test_child_workflow_batch_constructs_trusted_run_args() -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="run_child", action="core.workflow.execute", args={})
    prepared = _prepared_subflow_stub()

    captured_args: list[DSLRunArgs] = []

    async def dispatch_child_mock(
        _: ActionStatement,
        run_args: DSLRunArgs,
        *,
        wait_strategy: WaitStrategy,
        loop_index: int | None = None,
    ) -> Any:
        del wait_strategy
        assert loop_index is not None
        captured_args.append(run_args)
        return SimpleNamespace(id=f"child-{loop_index}")

    with (
        patch.object(
            DSLRunArgs,
            "__init__",
            side_effect=AssertionError("prepared child args should not revalidate"),
        ),
        patch.object(
            workflow,
            "_dispatch_child_workflow",
            new=AsyncMock(side_effect=dispatch_child_mock),
        ),
    ):
        result = await workflow._execute_child_workflow_batch_prepared(
            task=task,
            prepared=prepared,
            batch_start=0,
            batch_size=2,
            dispatch_window=8,
            wait_strategy=WaitStrategy.DETACH,
            fail_strategy=FailStrategy.ISOLATED,
            child_time_anchor=datetime.now(UTC),
        )

    assert [cast(InlineObject, val).data for val in result] == ["child-0", "child-1"]
    assert [args.trigger_inputs for args in captured_args] == [
        InlineObject(data={"index": 0}),
        InlineObject(data={"index": 1}),
    ]


@pytest.mark.anyio
async def test_child_workflow_batch_yields_before_next_arg_prep() -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="run_child", action="core.workflow.execute", args={})
    prepared_count = dsl_workflow_module._CHILD_RUN_ARG_PREP_YIELD_EVERY + 1
    config_indices: list[int] = []
    sleep_checkpoints: list[tuple[float, tuple[int, ...]]] = []

    def get_config(index: int) -> DSLConfig:
        config_indices.append(index)
        return DSLConfig()

    prepared = _prepared_subflow_stub(get_config=get_config)

    async def dispatch_child_mock(
        _: ActionStatement,
        __: DSLRunArgs,
        *,
        wait_strategy: WaitStrategy,
        loop_index: int | None = None,
    ) -> Any:
        del wait_strategy
        assert loop_index is not None
        return SimpleNamespace(id=f"child-{loop_index}")

    original_sleep = asyncio.sleep

    async def sleep_mock(delay: float) -> None:
        sleep_checkpoints.append((delay, tuple(config_indices)))
        await original_sleep(0)

    with (
        patch.object(dsl_workflow_module.asyncio, "sleep", new=sleep_mock),
        patch.object(
            workflow,
            "_dispatch_child_workflow",
            new=AsyncMock(side_effect=dispatch_child_mock),
        ),
    ):
        result = await workflow._execute_child_workflow_batch_prepared(
            task=task,
            prepared=prepared,
            batch_start=0,
            batch_size=prepared_count,
            dispatch_window=prepared_count + 1,
            wait_strategy=WaitStrategy.DETACH,
            fail_strategy=FailStrategy.ISOLATED,
            child_time_anchor=datetime.now(UTC),
        )

    assert len(result) == prepared_count
    assert sleep_checkpoints == [
        (
            0,
            tuple(range(dsl_workflow_module._CHILD_RUN_ARG_PREP_YIELD_EVERY)),
        )
    ]


@pytest.mark.anyio
async def test_run_cancellation_safe_cleanup_completes_after_cancellation() -> None:
    workflow = _build_workflow()
    started = asyncio.Event()
    completed = asyncio.Event()

    async def cleanup() -> None:
        started.set()
        await asyncio.sleep(0)
        completed.set()

    cleanup_task = asyncio.create_task(
        workflow._run_cancellation_safe_cleanup(
            cleanup,
            operation="test_cleanup",
        )
    )
    await started.wait()
    cleanup_task.cancel()
    await cleanup_task

    assert completed.is_set()
