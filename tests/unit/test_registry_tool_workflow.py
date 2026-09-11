"""Registry tool timeout ordering and terminal attribution regressions."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutType
from temporalio.exceptions import TimeoutError as TemporalTimeoutError
from tracecat_ee.agent.workflows.registry_tool import ExecuteRegistryToolWorkflow

from tracecat.agent.workflows.tool_execution import ExecuteRegistryToolWorkflowInput
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import (
    application_error_from_classification,
    extract_error_classifications,
)

MODULE = "tracecat_ee.agent.workflows.registry_tool"


def _activity_error(cause: Exception) -> ActivityError:
    error = ActivityError(
        "Activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test-executor",
        activity_type="execute_action_activity",
        activity_id="1",
        retry_state=None,
    )
    error.__cause__ = cause
    return error


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("patched", "executor_seconds", "expected_seconds", "expected_schedule_to_close"),
    [
        (True, 300, 360, timedelta(seconds=360)),
        (True, 300.5, 360.5, timedelta(seconds=360.5)),
        (False, 300, 300, None),
        (False, 300.5, 300, None),
    ],
)
async def test_activity_leaves_time_for_sandbox_failure(
    patched: bool,
    executor_seconds: float,
    expected_seconds: float,
    expected_schedule_to_close: timedelta | None,
) -> None:
    result = InlineObject(data="done")
    execute = AsyncMock(return_value=result)
    with (
        patch(f"{MODULE}.workflow.patched", return_value=patched),
        patch(f"{MODULE}.workflow.execute_activity", execute),
        patch(f"{MODULE}.config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT", executor_seconds),
    ):
        assert (
            await ExecuteRegistryToolWorkflow().run(
                Mock(
                    spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock()
                )
            )
            == result
        )
    assert execute.call_args.kwargs["start_to_close_timeout"] == timedelta(
        seconds=expected_seconds
    )
    assert (
        execute.call_args.kwargs["schedule_to_close_timeout"]
        == expected_schedule_to_close
    )
    assert execute.call_args.kwargs["retry_policy"].maximum_attempts == 1


@pytest.mark.anyio
@pytest.mark.parametrize("timeout_type", list(TimeoutType))
@pytest.mark.parametrize("patched", [True, False])
async def test_outer_timeouts_are_classified_without_retry(
    timeout_type: TimeoutType, patched: bool
) -> None:
    error = _activity_error(
        TemporalTimeoutError("Timed out", type=timeout_type, last_heartbeat_details=[])
    )
    with (
        patch(f"{MODULE}.workflow.patched", return_value=patched),
        patch(f"{MODULE}.workflow.execute_activity", AsyncMock(side_effect=error)),
        pytest.raises(ApplicationError) as raised,
    ):
        await ExecuteRegistryToolWorkflow().run(
            Mock(spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock())
        )
    assert raised.value.non_retryable
    assert raised.value.__cause__ is error
    classifications = extract_error_classifications(raised.value)
    if patched:
        assert len(classifications) == 1
        classification = classifications[0]
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert classification.kind is RuntimeErrorKind.EXECUTOR_ACTIVITY_TIMED_OUT
        assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    else:
        assert not classifications


@pytest.mark.anyio
async def test_classified_sandbox_failure_keeps_user_ownership() -> None:
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.SANDBOX_RESOURCE_LIMIT_EXCEEDED,
        message="Sandbox workload exceeded its limit",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _activity_error(application_error_from_classification(classification))
    with (
        patch(f"{MODULE}.workflow.patched", return_value=True),
        patch(f"{MODULE}.workflow.execute_activity", AsyncMock(side_effect=error)),
        pytest.raises(ApplicationError) as raised,
    ):
        await ExecuteRegistryToolWorkflow().run(
            Mock(spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock())
        )
    assert extract_error_classifications(raised.value) == (classification,)
    assert raised.value.non_retryable
