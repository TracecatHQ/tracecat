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
from tracecat.dsl.types import Task
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import (
    RuntimeErrorEnvelope,
    RuntimeErrorKind,
    RuntimeErrorOrigin,
    RuntimeErrorPhase,
)
from tracecat.temporal.errors import TemporalErrorDetails


def _build_scheduler(action_ref: str = "scatter") -> DSLScheduler:
    async def executor(_: ActionStatement) -> None:
        return None

    wf_id = WorkflowUUID.new_uuid4()
    dsl = DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref=action_ref),
        actions=[ActionStatement(ref=action_ref, action="core.transform.scatter")],
    )
    return DSLScheduler(
        executor=executor,
        dsl=dsl,
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
async def test_handle_error_path_ignores_runtime_metadata_when_building_action_error() -> (
    None
):
    scheduler = _build_scheduler()
    message = "Scatter collection is not iterable: <class 'int'>: 1"
    envelope = RuntimeErrorEnvelope.build(
        kind=RuntimeErrorKind.USER,
        code="dsl.scatter.collection_not_iterable",
        message=message,
        origin=RuntimeErrorOrigin.DSL,
        phase=RuntimeErrorPhase.USER_CODE,
        action_ref="scatter",
        stream_id=ROOT_STREAM,
        workflow_exec_id=scheduler.wf_exec_id,
    )
    app_error = ApplicationError(
        message,
        TemporalErrorDetails.with_runtime_error("scatter", envelope),
        type="ScatterCollectionNotIterable",
        non_retryable=True,
    )

    await scheduler._handle_error_path(
        Task(ref="scatter", stream_id=ROOT_STREAM), app_error
    )

    task_exception = scheduler.task_exceptions["scatter"]
    assert task_exception.details.message == message
    assert task_exception.details.type == "ScatterCollectionNotIterable"
    assert "Child workflow error details" not in task_exception.details.message
    assert task_exception.runtime_error is not None
    assert task_exception.runtime_error.code == "dsl.scatter.collection_not_iterable"


@pytest.mark.anyio
async def test_handle_error_path_preserves_child_error_for_runtime_errors_ref() -> None:
    scheduler = _build_scheduler(action_ref="runtime_errors")
    message = "Child action failed"
    app_error = ApplicationError(
        message,
        {"ref": "runtime_errors", "message": message, "type": "ValueError"},
        type="ApplicationError",
        non_retryable=True,
    )

    await scheduler._handle_error_path(
        Task(ref="runtime_errors", stream_id=ROOT_STREAM), app_error
    )

    task_exception = scheduler.task_exceptions["runtime_errors"]
    assert task_exception.details.ref == "runtime_errors"
    assert task_exception.details.message == message
    assert task_exception.details.type == "ValueError"


@pytest.mark.anyio
async def test_handle_error_path_honors_runtime_error_affects_workflow() -> None:
    scheduler = _build_scheduler()
    child_stream = StreamID.new("scatter", 0, base_stream_id=ROOT_STREAM)
    message = "Failed to materialize action context"
    envelope = RuntimeErrorEnvelope.build(
        kind=RuntimeErrorKind.INFRA,
        code="executor.materialize_context.failed",
        message=message,
        origin=RuntimeErrorOrigin.EXECUTOR,
        phase=RuntimeErrorPhase.PREPARE,
        affects_workflow=True,
        retryable=True,
        action_ref="scatter",
        stream_id=child_stream,
        workflow_exec_id=scheduler.wf_exec_id,
    )
    app_error = ApplicationError(
        message,
        TemporalErrorDetails.with_runtime_error(
            "scatter",
            envelope,
            payloads=(
                {
                    "ref": "scatter",
                    "message": message,
                    "type": "OSError",
                    "stream_id": child_stream,
                },
            ),
        ),
        type="OSError",
        non_retryable=False,
    )

    await scheduler._handle_error_path(
        Task(ref="scatter", stream_id=child_stream), app_error
    )

    assert child_stream not in scheduler.stream_exceptions
    task_exception = scheduler.task_exceptions["scatter"]
    assert task_exception.runtime_error is not None
    assert task_exception.runtime_error.affects_workflow is True
    assert task_exception.runtime_error.code == "executor.materialize_context.failed"
