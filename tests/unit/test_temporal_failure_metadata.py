"""Failure metadata follows deliberate Temporal causes only."""

import pytest
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutType
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

from tracecat.temporal.failure_metadata import ActivityTimeout, extract_activity_timeout


def _activity_timeout(timeout_type: TimeoutType | None) -> ActivityError:
    error = ActivityError(
        "synthetic activity failure",
        scheduled_event_id=1,
        started_event_id=2,
        identity="synthetic-worker",
        activity_type="synthetic_activity",
        activity_id="synthetic-activity",
        retry_state=None,
    )
    error.__cause__ = TemporalTimeoutError(
        "synthetic timeout", type=timeout_type, last_heartbeat_details=[]
    )
    return error


@pytest.mark.parametrize("timeout_type", list(TimeoutType))
@pytest.mark.parametrize("wrapped", [False, True])
def test_extract_activity_timeout(timeout_type: TimeoutType, wrapped: bool) -> None:
    error: BaseException = _activity_timeout(timeout_type)
    if wrapped:
        wrapper = ApplicationError("synthetic wrapper")
        wrapper.__cause__ = error
        error = wrapper
    assert extract_activity_timeout(error) == ActivityTimeout(timeout_type=timeout_type)


def test_incidental_context_does_not_supply_timeout_metadata() -> None:
    try:
        raise _activity_timeout(TimeoutType.HEARTBEAT)
    except ActivityError:
        try:
            raise ApplicationError("independent handler failure")
        except ApplicationError as error:
            assert isinstance(error.__context__, ActivityError)
            assert extract_activity_timeout(error) is None


def test_standalone_timeout_is_not_an_activity_timeout() -> None:
    error = TemporalTimeoutError(
        "synthetic timeout", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[]
    )
    assert extract_activity_timeout(error) is None


def test_missing_timeout_type_is_not_reported() -> None:
    assert extract_activity_timeout(_activity_timeout(None)) is None


def test_cyclic_causes_terminate_without_metadata() -> None:
    error = ApplicationError("synthetic cycle")
    error.__cause__ = error
    assert extract_activity_timeout(error) is None
