from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from temporalio.exceptions import ActivityError, ApplicationError

from tracecat.dsl.interceptor import (
    _RuntimeErrorAttributionWorkflowInterceptor,
    _unclassified_retry_disposition,
)
from tracecat.runtime.errors import RetryDisposition
from tracecat.workflow.executions.enums import TemporalSearchAttr


def _activity_error_from(cause: BaseException) -> ActivityError:
    try:
        raise ActivityError(
            "Synthetic activity failure",
            scheduled_event_id=1,
            started_event_id=2,
            identity="test-worker",
            activity_type="synthetic_activity",
            activity_id="synthetic-activity-id",
            retry_state=None,
        ) from cause
    except ActivityError as error:
        return error


def test_unclassified_activity_error_preserves_retryable_application_cause() -> None:
    error = _activity_error_from(ApplicationError("Transient activity failure"))

    assert _unclassified_retry_disposition(error) is RetryDisposition.RETRYABLE


def test_unclassified_activity_error_preserves_non_retryable_application_cause() -> (
    None
):
    error = _activity_error_from(
        ApplicationError("Permanent activity failure", non_retryable=True)
    )

    assert _unclassified_retry_disposition(error) is RetryDisposition.NON_RETRYABLE


def test_unclassified_raw_error_remains_non_retryable() -> None:
    assert (
        _unclassified_retry_disposition(RuntimeError("Unknown failure"))
        is RetryDisposition.NON_RETRYABLE
    )


def test_successful_retry_clears_inherited_error_owner() -> None:
    info = SimpleNamespace(
        attempt=2,
        typed_search_attributes=MagicMock(get=MagicMock(return_value="platform")),
    )

    with (
        patch("tracecat.dsl.interceptor.workflow.info", return_value=info),
        patch("tracecat.dsl.interceptor.workflow.upsert_search_attributes") as upsert,
    ):
        _RuntimeErrorAttributionWorkflowInterceptor._clear_inherited_owner()

    update = upsert.call_args.args[0][0]
    assert update.key == TemporalSearchAttr.ERROR_OWNER.key
    assert update.value is None


def test_first_attempt_success_does_not_clear_error_owner() -> None:
    info = SimpleNamespace(
        attempt=1,
        typed_search_attributes=MagicMock(get=MagicMock(return_value="platform")),
    )

    with (
        patch("tracecat.dsl.interceptor.workflow.info", return_value=info),
        patch("tracecat.dsl.interceptor.workflow.upsert_search_attributes") as upsert,
    ):
        _RuntimeErrorAttributionWorkflowInterceptor._clear_inherited_owner()

    upsert.assert_not_called()
