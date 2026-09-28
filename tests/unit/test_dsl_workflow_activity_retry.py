from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    TimeoutType,
)
from temporalio.exceptions import (
    TimeoutError as TemporalTimeoutError,
)

from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement, RunActionInput
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.storage.object import InlineObject


def _activity_error_from(cause: Exception) -> ActivityError:
    try:
        raise ActivityError(
            "Activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test-executor",
            activity_type="execute_action_activity",
            activity_id="execute_action_activity",
            retry_state=None,
        ) from cause
    except ActivityError as exc:
        return exc


def _timeout_error(timeout_type: TimeoutType) -> ActivityError:
    return _activity_error_from(
        TemporalTimeoutError(
            "Activity timed out",
            type=timeout_type,
            last_heartbeat_details=[],
        )
    )


def _workflow() -> DSLWorkflow:
    dsl_workflow = object.__new__(DSLWorkflow)
    dsl_workflow.role = cast(Any, object())
    dsl_workflow.logger = Mock()
    return dsl_workflow


def _task(*, max_attempts: int = 1) -> ActionStatement:
    return ActionStatement(
        ref="test_action",
        action="core.transform.reshape",
        retry_policy=ActionRetryPolicy(max_attempts=max_attempts),
    )


@pytest.mark.anyio
async def test_action_retries_once_after_heartbeat_timeout() -> None:
    dsl_workflow = _workflow()
    execute_activity = AsyncMock(
        side_effect=[
            _timeout_error(TimeoutType.HEARTBEAT),
            InlineObject(data={"ok": True}),
        ]
    )

    with (
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute_activity),
        patch("tracecat.dsl.workflow.workflow.patched", return_value=True),
    ):
        result = await dsl_workflow._execute_action_activity(
            _task(), cast(RunActionInput, object())
        )

    assert result == InlineObject(data={"ok": True})
    assert execute_activity.await_count == 2
    cast(Any, dsl_workflow.logger.warning).assert_called_once_with(
        "Retrying action after activity heartbeat timeout",
        task_ref="test_action",
    )


@pytest.mark.anyio
async def test_action_propagates_second_heartbeat_timeout() -> None:
    dsl_workflow = _workflow()
    second_timeout = _timeout_error(TimeoutType.HEARTBEAT)
    execute_activity = AsyncMock(
        side_effect=[_timeout_error(TimeoutType.HEARTBEAT), second_timeout]
    )

    with (
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute_activity),
        patch("tracecat.dsl.workflow.workflow.patched", return_value=True),
        pytest.raises(ActivityError) as raised,
    ):
        await dsl_workflow._execute_action_activity(
            _task(), cast(RunActionInput, object())
        )

    assert raised.value is second_timeout
    assert execute_activity.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        _timeout_error(TimeoutType.START_TO_CLOSE),
        _activity_error_from(ApplicationError("Action failed")),
    ],
    ids=["start-to-close-timeout", "application-error"],
)
async def test_action_does_not_retry_other_failures(failure: ActivityError) -> None:
    dsl_workflow = _workflow()
    execute_activity = AsyncMock(side_effect=failure)

    with (
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute_activity),
        pytest.raises(ActivityError) as raised,
    ):
        await dsl_workflow._execute_action_activity(
            _task(), cast(RunActionInput, object())
        )

    assert raised.value is failure
    assert execute_activity.await_count == 1


@pytest.mark.anyio
async def test_action_with_configured_retries_has_no_extra_heartbeat_retry() -> None:
    dsl_workflow = _workflow()
    heartbeat_timeout = _timeout_error(TimeoutType.HEARTBEAT)
    execute_activity = AsyncMock(side_effect=heartbeat_timeout)

    with (
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute_activity),
        pytest.raises(ActivityError) as raised,
    ):
        await dsl_workflow._execute_action_activity(
            _task(max_attempts=2), cast(RunActionInput, object())
        )

    assert raised.value is heartbeat_timeout
    assert execute_activity.await_count == 1


@pytest.mark.anyio
async def test_action_preserves_legacy_path_without_retry_patch_marker() -> None:
    dsl_workflow = _workflow()
    heartbeat_timeout = _timeout_error(TimeoutType.HEARTBEAT)
    execute_activity = AsyncMock(side_effect=heartbeat_timeout)

    with (
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute_activity),
        patch("tracecat.dsl.workflow.workflow.patched", return_value=False),
        pytest.raises(ActivityError) as raised,
    ):
        await dsl_workflow._execute_action_activity(
            _task(), cast(RunActionInput, object())
        )

    assert raised.value is heartbeat_timeout
    assert execute_activity.await_count == 1
