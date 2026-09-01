from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from temporalio.exceptions import ActivityError, CancelledError

from tests.shared import capture_application_error as _capture_application_error
from tracecat.auth.types import Role
from tracecat.dsl import scheduler as scheduler_module
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.constants import PRESERVE_TEMPORAL_CANCELLATION_PATCH
from tracecat.dsl.error_transport import parse_classified_action_error_payload
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    ExecutionContext,
    RunContext,
    StreamID,
)
from tracecat.dsl.types import (
    ActionErrorInfo,
    Task,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.temporal.errors import (
    build_error_transport_detail,
    extract_error_classifications,
)


def _build_scheduler(
    *,
    total_tasks: int,
    executor: Callable[[ActionStatement], Awaitable[None]],
    max_pending_tasks: int | None = None,
) -> DSLScheduler:
    dsl = DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref="task_0"),
        actions=[
            ActionStatement(ref=f"task_{index}", action="core.noop")
            for index in range(total_tasks)
        ],
    )
    wf_id = WorkflowUUID.new_uuid4()
    test_role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    test_run_context = RunContext(
        wf_id=wf_id,
        wf_exec_id=f"{wf_id.short()}/exec_test",
        wf_run_id=uuid.uuid4(),
        environment="test",
        logical_time=datetime.now(UTC),
    )
    return DSLScheduler(
        executor=executor,
        dsl=dsl,
        max_pending_tasks=max_pending_tasks or total_tasks,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=test_role,
        run_context=test_run_context,
    )


def _cancelled_activity_error() -> ActivityError:
    try:
        raise ActivityError(
            "Activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test-worker",
            activity_type="test-activity",
            activity_id="test-activity-id",
            retry_state=None,
        ) from CancelledError("cancelled by sibling")
    except ActivityError as error:
        return error


@pytest.mark.anyio
@pytest.mark.parametrize("preserve_cancellation", [True, False])
async def test_scheduler_preserves_temporal_cancellation_with_replay_gate(
    preserve_cancellation: bool,
) -> None:
    activity_error = _cancelled_activity_error()

    async def executor(_: ActionStatement) -> None:
        raise activity_error

    scheduler = _build_scheduler(total_tasks=1, executor=executor)
    with patch(
        "tracecat.dsl.scheduler.workflow.patched",
        return_value=preserve_cancellation,
    ):
        if preserve_cancellation:
            with pytest.raises(ActivityError) as exc_info:
                await scheduler.start()
            assert exc_info.value is activity_error
            assert not scheduler.task_exceptions
        else:
            await scheduler.start()
            assert "task_0" in scheduler.task_exceptions


@pytest.mark.anyio
async def test_scheduler_legacy_cancellation_does_not_hide_same_batch_error() -> None:
    async def executor(_: ActionStatement) -> None:
        return None

    scheduler = _build_scheduler(total_tasks=2, executor=executor)
    ready: set[str] = set()
    all_ready = asyncio.Event()

    async def schedule_task(task: Task) -> None:
        ready.add(task.ref)
        if len(ready) == 2:
            all_ready.set()
        await all_ready.wait()
        if task.ref == "task_0":
            raise asyncio.CancelledError
        raise RuntimeError("causal scheduler failure")

    with (
        patch.object(scheduler, "_schedule_task", new=schedule_task),
        patch(
            "tracecat.dsl.scheduler.workflow.patched",
            return_value=False,
        ) as patched,
        pytest.raises(RuntimeError, match="causal scheduler failure"),
    ):
        await scheduler.start()

    patched.assert_called_once_with(PRESERVE_TEMPORAL_CANCELLATION_PATCH)


@pytest.mark.anyio
async def test_scheduler_unwraps_classified_action_error_payload() -> None:
    """Transported diagnostics become task error info and retain classification."""

    async def executor(_: ActionStatement) -> None:
        return None

    scheduler = _build_scheduler(total_tasks=1, executor=executor)
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    payload = ActionErrorInfo(
        ref="task_0",
        message="The action failed",
        type="ValueError",
    )
    error = _capture_application_error(
        classification, build_error_transport_detail(classification, payload)
    )

    await scheduler._handle_error_path(Task(ref="task_0", stream_id=ROOT_STREAM), error)

    details = scheduler.task_exceptions["task_0"].details
    assert details == payload
    assert scheduler.stream_error_classifications[ROOT_STREAM] == classification


@pytest.mark.anyio
async def test_scheduler_rebinds_classified_action_error_to_current_stream() -> None:
    """An unwrapped payload is rebound to the failing task's ref and stream."""

    async def executor(_: ActionStatement) -> None:
        return None

    scheduler = _build_scheduler(total_tasks=1, executor=executor)
    stream_id = StreamID.new("task_0", 2)
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        classification,
        build_error_transport_detail(
            classification,
            ActionErrorInfo(
                ref="legacy_ref",
                message=classification.message,
                type="ValueError",
            ),
        ),
    )

    await scheduler._handle_error_path(Task(ref="task_0", stream_id=stream_id), error)

    details = scheduler.stream_exceptions[stream_id].details
    assert details.ref == "task_0"
    assert details.stream_id == stream_id
    assert details.message == classification.message
    assert scheduler.stream_error_classifications[stream_id] == classification


@pytest.mark.anyio
async def test_scheduler_unwraps_classified_child_workflow_error_map() -> None:
    """A child's terminal transport map becomes children payloads; ownership
    aggregates platform-wins across entries, so a platform-owned entry after a
    user-owned one still attributes the stream to the platform."""

    async def executor(_: ActionStatement) -> None:
        return None

    scheduler = _build_scheduler(total_tasks=1, executor=executor)
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the child action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    user_detail = ActionErrorInfo(
        ref="user_action",
        message=user_classification.message,
        type="UserError",
    )
    platform_detail = ActionErrorInfo(
        ref="platform_action",
        message=platform_classification.message,
        type="RuntimeError",
    )
    error = _capture_application_error(
        user_classification,
        {
            user_detail.ref: build_error_transport_detail(
                user_classification, user_detail
            ).model_dump(mode="json"),
            platform_detail.ref: build_error_transport_detail(
                platform_classification, platform_detail
            ).model_dump(mode="json"),
        },
    )

    await scheduler._handle_error_path(Task(ref="task_0", stream_id=ROOT_STREAM), error)

    details = scheduler.task_exceptions["task_0"].details
    assert details.ref == "task_0"
    assert details.message == platform_classification.message
    assert details.children == [user_detail, platform_detail]
    assert (
        scheduler.stream_error_classifications[ROOT_STREAM] == platform_classification
    )
    assert extract_error_classifications(error) == (
        user_classification,
        platform_classification,
    )


@pytest.mark.anyio
async def test_scheduler_child_workflow_error_map_all_user_keeps_first_classification() -> (
    None
):
    """An all-user terminal map still attributes the stream to the first entry."""

    async def executor(_: ActionStatement) -> None:
        return None

    scheduler = _build_scheduler(total_tasks=1, executor=executor)
    first_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The first child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    second_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The second child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        first_classification,
        {
            "first_action": build_error_transport_detail(
                first_classification,
                ActionErrorInfo(
                    ref="first_action",
                    message=first_classification.message,
                    type="UserError",
                ),
            ).model_dump(mode="json"),
            "second_action": build_error_transport_detail(
                second_classification,
                ActionErrorInfo(
                    ref="second_action",
                    message=second_classification.message,
                    type="UserError",
                ),
            ).model_dump(mode="json"),
        },
    )

    await scheduler._handle_error_path(Task(ref="task_0", stream_id=ROOT_STREAM), error)

    details = scheduler.task_exceptions["task_0"].details
    assert details.message == first_classification.message
    assert scheduler.stream_error_classifications[ROOT_STREAM] == first_classification


@pytest.mark.anyio
async def test_scheduler_synthesizes_info_from_bare_transport_detail() -> None:
    """A detail without diagnostics still yields synthesized task error info."""

    async def executor(_: ActionStatement) -> None:
        return None

    scheduler = _build_scheduler(total_tasks=1, executor=executor)
    classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    error = _capture_application_error(classification)

    await scheduler._handle_error_path(Task(ref="task_0", stream_id=ROOT_STREAM), error)

    details = scheduler.task_exceptions["task_0"].details
    assert details.ref == "task_0"
    assert details.message == classification.message
    assert details.children is None
    assert scheduler.stream_error_classifications[ROOT_STREAM] == classification


@pytest.mark.anyio
async def test_scheduler_rejects_undiscriminated_classification() -> None:
    """An undiscriminated classification never classifies the failure."""

    async def executor(_: ActionStatement) -> None:
        return None

    scheduler = _build_scheduler(total_tasks=1, executor=executor)
    invalid_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="Invalid unversioned classification",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    fallback_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    invalid_detail = build_error_transport_detail(
        invalid_classification,
        ActionErrorInfo(
            ref="forged_action",
            message=invalid_classification.message,
            type="UserError",
        ),
    ).model_dump(mode="json")
    invalid_detail["classification"].pop("schema")
    error = _capture_application_error(fallback_classification, invalid_detail)

    await scheduler._handle_error_path(Task(ref="task_0", stream_id=ROOT_STREAM), error)

    details = scheduler.task_exceptions["task_0"].details
    assert parse_classified_action_error_payload(invalid_detail) is None
    assert details.ref == "task_0"
    assert details.message == fallback_classification.message
    assert (
        scheduler.stream_error_classifications[ROOT_STREAM] == fallback_classification
    )


@pytest.mark.anyio
async def test_scheduler_respects_max_pending_tasks_cap() -> None:
    max_pending_tasks = 3
    total_tasks = 10
    active_tasks = 0
    started_tasks = 0
    max_active_tasks = 0
    cap_reached = asyncio.Event()
    release_tasks = asyncio.Event()

    async def executor(_: ActionStatement) -> None:
        nonlocal active_tasks, max_active_tasks, started_tasks
        started_tasks += 1
        active_tasks += 1
        max_active_tasks = max(max_active_tasks, active_tasks)
        if max_active_tasks == max_pending_tasks:
            cap_reached.set()
        await release_tasks.wait()
        active_tasks -= 1

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        executor=executor,
        max_pending_tasks=max_pending_tasks,
    )

    scheduler_task = asyncio.create_task(scheduler.start())
    await asyncio.wait_for(cap_reached.wait(), timeout=2)
    await asyncio.sleep(0)

    assert max_active_tasks == max_pending_tasks
    assert active_tasks == max_pending_tasks

    release_tasks.set()
    result = await asyncio.wait_for(scheduler_task, timeout=2)

    assert result is None
    assert started_tasks == total_tasks
    assert max_active_tasks == max_pending_tasks


@pytest.mark.anyio
async def test_scheduler_yields_while_spawning_ready_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    total_tasks = scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY + 1
    yield_count = 0
    original_sleep = asyncio.sleep

    async def spy_sleep(delay: float, result: object = None) -> object:
        nonlocal yield_count
        if delay == 0:
            yield_count += 1
        return await original_sleep(0, result)

    monkeypatch.setattr(scheduler_module.asyncio, "sleep", spy_sleep)

    async def executor(_: ActionStatement) -> None:
        await original_sleep(0)

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        max_pending_tasks=total_tasks,
        executor=executor,
    )

    result = await asyncio.wait_for(scheduler.start(), timeout=2)

    assert result is None
    assert yield_count >= 1


@pytest.mark.anyio
async def test_scheduler_reclaims_completed_tasks_after_spawn_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    total_tasks = scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY + 1
    wait_count = 0
    original_wait = scheduler_module.workflow.wait

    async def spy_wait(*args: Any, **kwargs: Any) -> Any:
        nonlocal wait_count
        wait_count += 1
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(scheduler_module.workflow, "wait", spy_wait)

    started_refs: list[str] = []

    async def executor(stmt: ActionStatement) -> None:
        started_refs.append(stmt.ref)

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        max_pending_tasks=scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY,
        executor=executor,
    )

    result = await asyncio.wait_for(scheduler.start(), timeout=2)

    assert result is None
    assert started_refs == [f"task_{index}" for index in range(total_tasks)]
    assert wait_count == 1


@pytest.mark.anyio
async def test_scheduler_stops_spawning_after_failure_during_spawn_yield() -> None:
    total_tasks = scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY + 1
    started_refs: list[str] = []
    block_pending_tasks = asyncio.Event()

    async def executor(stmt: ActionStatement) -> None:
        started_refs.append(stmt.ref)
        if stmt.ref == "task_0":
            raise RuntimeError("intentional scheduler test failure")
        await block_pending_tasks.wait()

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        max_pending_tasks=total_tasks,
        executor=executor,
    )

    result = await asyncio.wait_for(scheduler.start(), timeout=2)

    assert result is not None
    first_unstarted_ref = f"task_{scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY}"
    assert len(started_refs) == scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY
    assert first_unstarted_ref not in started_refs
