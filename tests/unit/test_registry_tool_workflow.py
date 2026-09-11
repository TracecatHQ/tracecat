"""Registry tool timeout ordering and terminal attribution regressions."""

from datetime import UTC, datetime, timedelta
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
WORKFLOW_START = datetime(2026, 1, 1, tzinfo=UTC)


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
    ("patched", "expected_seconds", "expected_schedule_to_close"),
    [
        (True, 360, timedelta(seconds=360)),
        (False, 300, None),
    ],
)
async def test_activity_leaves_time_for_sandbox_failure(
    patched: bool,
    expected_seconds: int,
    expected_schedule_to_close: timedelta | None,
) -> None:
    result = InlineObject(data="done")
    execute = AsyncMock(return_value=result)
    workflow_info = Mock(
        workflow_start_time=WORKFLOW_START,
        run_timeout=timedelta(seconds=390),
    )
    with (
        patch(f"{MODULE}.workflow.patched", return_value=patched),
        patch(f"{MODULE}.workflow.execute_activity", execute),
        patch(f"{MODULE}.config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 300),
        patch(f"{MODULE}.workflow.info", return_value=workflow_info) as info,
        patch(f"{MODULE}.workflow.now", return_value=WORKFLOW_START) as now,
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
    if not patched:
        info.assert_not_called()
        now.assert_not_called()


@pytest.mark.anyio
async def test_activity_schedule_to_close_accounts_for_late_workflow_start() -> None:
    result = InlineObject(data="done")
    execute = AsyncMock(return_value=result)
    workflow_info = Mock(
        workflow_start_time=WORKFLOW_START,
        run_timeout=timedelta(seconds=390),
    )
    with (
        patch(f"{MODULE}.workflow.patched", return_value=True),
        patch(f"{MODULE}.workflow.execute_activity", execute),
        patch(f"{MODULE}.config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 300),
        patch(f"{MODULE}.workflow.info", return_value=workflow_info),
        patch(
            f"{MODULE}.workflow.now",
            return_value=WORKFLOW_START + timedelta(seconds=90),
        ),
    ):
        await ExecuteRegistryToolWorkflow().run(
            Mock(spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock())
        )

    assert execute.call_args.kwargs["start_to_close_timeout"] == timedelta(seconds=360)
    assert execute.call_args.kwargs["schedule_to_close_timeout"] == timedelta(
        seconds=270
    )


@pytest.mark.anyio
@pytest.mark.parametrize("elapsed_seconds", [360, 375])
async def test_exhausted_activity_budget_skips_scheduling(
    elapsed_seconds: int,
) -> None:
    execute = AsyncMock(return_value=InlineObject(data="unreachable"))
    workflow_info = Mock(
        workflow_start_time=WORKFLOW_START,
        run_timeout=timedelta(seconds=390),
    )
    with (
        patch(f"{MODULE}.workflow.patched", return_value=True),
        patch(f"{MODULE}.workflow.execute_activity", execute),
        patch(f"{MODULE}.config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 300),
        patch(f"{MODULE}.workflow.info", return_value=workflow_info),
        patch(
            f"{MODULE}.workflow.now",
            return_value=WORKFLOW_START + timedelta(seconds=elapsed_seconds),
        ),
        pytest.raises(ApplicationError) as raised,
    ):
        await ExecuteRegistryToolWorkflow().run(
            Mock(spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock())
        )

    assert raised.value.non_retryable
    classification = extract_error_classifications(raised.value)
    assert len(classification) == 1
    assert classification[0].owner is RuntimeErrorOwner.PLATFORM
    assert classification[0].kind is RuntimeErrorKind.EXECUTOR_ACTIVITY_TIMED_OUT
    assert classification[0].retry_disposition is RetryDisposition.NON_RETRYABLE
    execute.assert_not_called()


@pytest.mark.anyio
async def test_no_workflow_run_timeout_uses_activity_duration() -> None:
    execute = AsyncMock(return_value=InlineObject(data="done"))
    workflow_info = Mock(workflow_start_time=WORKFLOW_START, run_timeout=None)
    with (
        patch(f"{MODULE}.workflow.patched", return_value=True),
        patch(f"{MODULE}.workflow.execute_activity", execute),
        patch(f"{MODULE}.config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 300),
        patch(f"{MODULE}.workflow.info", return_value=workflow_info),
    ):
        await ExecuteRegistryToolWorkflow().run(
            Mock(spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock())
        )

    assert execute.call_args.kwargs["start_to_close_timeout"] == timedelta(seconds=360)
    assert execute.call_args.kwargs["schedule_to_close_timeout"] == timedelta(
        seconds=360
    )


@pytest.mark.anyio
async def test_activity_timeout_preserves_fractional_executor_config() -> None:
    execute = AsyncMock(return_value=InlineObject(data="done"))
    workflow_info = Mock(
        workflow_start_time=WORKFLOW_START,
        run_timeout=timedelta(seconds=900),
    )
    with (
        patch(f"{MODULE}.workflow.patched", return_value=True),
        patch(f"{MODULE}.workflow.execute_activity", execute),
        patch(f"{MODULE}.config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 300.5),
        patch(f"{MODULE}.workflow.info", return_value=workflow_info),
        patch(f"{MODULE}.workflow.now", return_value=WORKFLOW_START),
    ):
        await ExecuteRegistryToolWorkflow().run(
            Mock(spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock())
        )

    assert execute.call_args.kwargs["start_to_close_timeout"] == timedelta(
        seconds=360.5
    )
    assert execute.call_args.kwargs["schedule_to_close_timeout"] == timedelta(
        seconds=360.5
    )


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
        patch(
            f"{MODULE}.workflow.info",
            return_value=Mock(
                workflow_start_time=WORKFLOW_START,
                run_timeout=timedelta(seconds=390),
            ),
        ),
        patch(f"{MODULE}.workflow.now", return_value=WORKFLOW_START),
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
        patch(
            f"{MODULE}.workflow.info",
            return_value=Mock(
                workflow_start_time=WORKFLOW_START,
                run_timeout=timedelta(seconds=390),
            ),
        ),
        patch(f"{MODULE}.workflow.now", return_value=WORKFLOW_START),
        pytest.raises(ApplicationError) as raised,
    ):
        await ExecuteRegistryToolWorkflow().run(
            Mock(spec=ExecuteRegistryToolWorkflowInput, run_input=Mock(), role=Mock())
        )
    assert extract_error_classifications(raised.value) == (classification,)
    assert raised.value.non_retryable
