from temporalio.exceptions import ActivityError, ApplicationError

from tracecat.dsl.interceptor import _unclassified_retry_disposition
from tracecat.runtime.errors import RetryDisposition


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
