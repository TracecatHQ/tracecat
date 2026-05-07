from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    ExecutionContext,
    RunContext,
    StreamID,
)
from tracecat.dsl.types import ActionErrorInfo, ActionErrorInfoAdapter, Task
from tracecat.identifiers.workflow import WorkflowUUID


def _make_scheduler(*actions: ActionStatement) -> DSLScheduler:
    async def executor(_: ActionStatement) -> None:
        return None

    wf_id = WorkflowUUID.new_uuid4()
    return DSLScheduler(
        executor=executor,
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
        Task(ref="call_child", stream_id=stream_id), error
    )

    assert scheduler.task_exceptions["call_child"].exception is error
    assert stream_id not in scheduler.stream_exceptions
    assert scheduler.queue.empty()


@pytest.mark.anyio
async def test_child_action_error_uses_error_path() -> None:
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
    error_info = ActionErrorInfo(
        ref="child_task",
        message="Child action failed",
        type="ExecutionError",
        stream_id=stream_id,
    )
    error = ApplicationError(
        "Child workflow failed",
        {"child_task": ActionErrorInfoAdapter.dump_python(error_info)},
        non_retryable=True,
    )

    await scheduler._handle_error_path(
        Task(ref="call_child", stream_id=stream_id), error
    )

    assert not scheduler.task_exceptions
    assert not scheduler.stream_exceptions
    assert scheduler.queue.get_nowait() == Task(ref="handle_error", stream_id=stream_id)
    assert scheduler.queue.empty()


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
