from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.dsl.action import DSLActivities
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    RunContext,
    StreamID,
)
from tracecat.dsl.types import ActionErrorInfo, ActionErrorInfoAdapter, Task
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.dsl.workflow_logging import get_workflow_logger
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage.object import InlineObject
from tracecat.workflow.executions.enums import ExecutionType


def _make_scheduler(
    *actions: ActionStatement,
    executor: Callable[[ActionStatement], Awaitable[Any]] | None = None,
) -> DSLScheduler:
    async def default_executor(_: ActionStatement) -> None:
        return None

    wf_id = WorkflowUUID.new_uuid4()
    return DSLScheduler(
        executor=executor or default_executor,
        dsl=DSLInput(
            title="test",
            description="test",
            entrypoint=DSLEntrypoint(ref=actions[0].ref),
            actions=list(actions),
        ),
        max_pending_tasks=16,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        ),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
    )


def _make_workflow(context: ExecutionContext) -> DSLWorkflow:
    workflow = object.__new__(DSLWorkflow)
    workflow.role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    assert workflow.role.workspace_id is not None
    assert workflow.role.organization_id is not None
    workflow.organization_id = workflow.role.organization_id
    workflow.workspace_id = workflow.role.workspace_id
    workflow.logger = get_workflow_logger()
    workflow.runtime_config = DSLConfig()
    workflow._tier_limits = None
    workflow._workflow_permit_acquired = False
    workflow._workflow_permit_heartbeat_task = None
    workflow._action_execution_count = 0
    workflow.execution_type = ExecutionType.PUBLISHED
    workflow.run_context = RunContext(
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000001"),
        wf_exec_id="wf-00000000000000000000000000000001:exec-00000000000000000000000000000001",
        wf_run_id=uuid.uuid4(),
        environment="__TEST__",
        logical_time=datetime.now(UTC),
    )
    workflow.wf_exec_id = workflow.run_context.wf_exec_id
    workflow.context = context
    return workflow


@pytest.mark.anyio
async def test_platform_stream_error_promotes_to_workflow_failure() -> None:
    scheduler = _make_scheduler(
        ActionStatement(
            ref="call_child",
            action="core.workflow.execute",
            args={"workflow_alias": "child"},
        ),
        ActionStatement(
            ref="handle_error",
            action="core.noop",
            depends_on=["call_child.error"],
        ),
    )
    stream_id = StreamID.new("scatter", 0, base_stream_id=ROOT_STREAM)
    error = ApplicationError("Failed to materialize context", non_retryable=True)

    await scheduler._handle_error_path(
        Task(ref="call_child", stream_id=stream_id),
        error,
        force_workflow_failure=True,
    )

    assert scheduler.task_exceptions["call_child"].exception is error
    assert stream_id not in scheduler.stream_exceptions
    assert scheduler.queue.empty()


@pytest.mark.anyio
async def test_detail_less_executor_error_uses_error_path() -> None:
    error = ApplicationError("Workflow alias 'child' not found", non_retryable=True)
    executed_refs: list[str] = []

    async def executor(action: ActionStatement) -> None:
        executed_refs.append(action.ref)
        if action.ref == "call_child":
            raise error

    scheduler = _make_scheduler(
        ActionStatement(
            ref="call_child",
            action="core.workflow.execute",
            args={"workflow_alias": "child"},
        ),
        ActionStatement(
            ref="handle_error",
            action="core.noop",
            depends_on=["call_child.error"],
        ),
        executor=executor,
    )

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert executed_refs == ["call_child", "handle_error"]
    assert not scheduler.task_exceptions
    assert not scheduler.stream_exceptions


@pytest.mark.anyio
async def test_action_stream_error_uses_gather_error_strategy() -> None:
    scheduler = _make_scheduler(
        ActionStatement(ref="throw", action="core.transform.reshape")
    )
    stream_id = StreamID.new("scatter", 0, base_stream_id=ROOT_STREAM)
    error_info = ActionErrorInfo(
        ref="throw",
        message="User action failed",
        type="ExecutionError",
        stream_id=stream_id,
    )
    error = ApplicationError(
        "Action failed",
        ActionErrorInfoAdapter.dump_python(error_info),
        non_retryable=True,
    )

    await scheduler._handle_error_path(Task(ref="throw", stream_id=stream_id), error)

    assert not scheduler.task_exceptions
    assert scheduler.stream_exceptions[stream_id].exception is error
    assert scheduler.stream_exceptions[stream_id].details == error_info


@pytest.mark.anyio
async def test_prepare_subflow_runtime_error_in_scatter_fails_workflow() -> None:
    context = ExecutionContext(ACTIONS={}, TRIGGER=None)
    workflow = _make_workflow(context)
    wf_id = WorkflowUUID.new_uuid4()
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
        context=context,
        role=workflow.role,
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
    )
    workflow.dsl = dsl
    workflow.scheduler = scheduler
    executed_refs: list[str] = []

    async def execute_activity(activity: object, *_: Any, **__: Any) -> object:
        if activity == DSLActivities.handle_scatter_input_activity:
            return InlineObject(data=["item"], typename="list")
        if activity == DSLActivities.prepare_subflow_activity:
            raise RuntimeError("prepare failed")
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

    assert task_exceptions is not None
    assert "call_child" in task_exceptions
    assert isinstance(task_exceptions["call_child"].exception, RuntimeError)
    assert "prepare failed" in task_exceptions["call_child"].details.message
    assert executed_refs == []
    assert not scheduler.stream_exceptions
