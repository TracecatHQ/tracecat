"""Typed Temporal failure facts, independent of ownership and retry policy."""

from dataclasses import dataclass

from temporalio.exceptions import ActivityError, TimeoutType
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

from tracecat.temporal.error_chain import iter_error_chain


@dataclass(frozen=True, slots=True)
class ActivityTimeout:
    """Bounded metadata describing an activity timeout."""

    timeout_type: TimeoutType


def extract_activity_timeout(error: BaseException) -> ActivityTimeout | None:
    """Extract the first activity timeout from deliberate exception causes.

    Incidental Python context may describe a different failure being handled,
    so it must not supply timeout metadata. Standalone workflow timeouts are
    excluded by requiring an ActivityError with a direct timeout cause.
    """
    for current in iter_error_chain(error, include_implicit_context=False):
        if (
            isinstance(current, ActivityError)
            and isinstance(cause := current.cause, TemporalTimeoutError)
            and cause.type is not None
        ):
            return ActivityTimeout(timeout_type=cause.type)
    return None
