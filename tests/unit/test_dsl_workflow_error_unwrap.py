from temporalio.exceptions import ApplicationError

from tracecat.dsl.workflow import DSLWorkflow, _wrap_activity_application_error
from tracecat.runtime.errors import (
    RuntimeErrorEnvelope,
    RuntimeErrorKind,
    RuntimeErrorOrigin,
    RuntimeErrorPhase,
)
from tracecat.temporal.errors import (
    extract_runtime_error_from_details,
    runtime_error_detail,
)


class CauseError(Exception):
    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


def test_unwrap_temporal_failure_cause_returns_deepest_nested_exception() -> None:
    root = CauseError("Workflow alias 'invalid' not found")
    activity_wrapper = CauseError("Activity task failed", cause=root)
    workflow_wrapper = CauseError("Workflow execution failed", cause=activity_wrapper)

    deepest_error, message = DSLWorkflow._unwrap_temporal_failure_cause(
        workflow_wrapper
    )

    assert deepest_error is root
    assert message == "Workflow alias 'invalid' not found"


def test_unwrap_temporal_failure_cause_falls_back_to_outer_message() -> None:
    root = CauseError("")
    wrapper = CauseError("Activity task failed", cause=root)

    deepest_error, message = DSLWorkflow._unwrap_temporal_failure_cause(wrapper)

    assert deepest_error is root
    assert message == "Activity task failed"


def test_unwrap_temporal_failure_cause_handles_cyclic_causes() -> None:
    first = CauseError("first")
    second = CauseError("second", cause=first)
    first.cause = second

    deepest_error, message = DSLWorkflow._unwrap_temporal_failure_cause(first)

    assert deepest_error is first
    assert message == "first"


def test_wrap_activity_application_error_preserves_runtime_classification() -> None:
    envelope = RuntimeErrorEnvelope.build(
        kind=RuntimeErrorKind.INFRA,
        code="dsl.trigger_inputs.retrieve_failed",
        message="Failed to retrieve trigger inputs",
        origin=RuntimeErrorOrigin.DSL,
        phase=RuntimeErrorPhase.PREPARE,
        retryable=True,
        root=OSError("object storage unavailable"),
    )
    activity_error = ApplicationError(
        "Failed to retrieve trigger inputs",
        {"legacy_payload": True},
        runtime_error_detail("trigger", envelope),
        type="OSError",
        non_retryable=False,
    )

    wrapped = _wrap_activity_application_error(
        "Failed to normalize trigger inputs",
        activity_error,
        fallback_type="ActivityError",
    )

    assert wrapped.message == "Failed to normalize trigger inputs"
    assert wrapped.type == "OSError"
    assert wrapped.non_retryable is False
    assert {"legacy_payload": True} not in wrapped.details
    extracted = extract_runtime_error_from_details(wrapped.details)
    assert extracted is not None
    assert extracted.kind == RuntimeErrorKind.INFRA
    assert extracted.code == "dsl.trigger_inputs.retrieve_failed"
    assert extracted.retryable is True
