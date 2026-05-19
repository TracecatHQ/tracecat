from temporalio.exceptions import ApplicationError

from tracecat.dsl.workflow import (
    DSLWorkflow,
    _first_payload_detail,
    _format_trigger_input_validation_error,
)
from tracecat.runtime.errors import (
    RuntimeErrorEnvelope,
    RuntimeErrorKind,
    RuntimeErrorOrigin,
    RuntimeErrorPhase,
)
from tracecat.temporal.errors import (
    WorkflowRuntimeError,
    extract_runtime_error_from_details,
    runtime_error_detail,
)
from tracecat.validation.schemas import ValidationDetail


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


def test_format_trigger_input_validation_error_preserves_runtime_classification() -> (
    None
):
    envelope = RuntimeErrorEnvelope.build(
        kind=RuntimeErrorKind.USER,
        code="dsl.trigger_inputs.validation_failed",
        message="Failed to validate trigger inputs",
        origin=RuntimeErrorOrigin.DSL,
        phase=RuntimeErrorPhase.PREPARE,
        root=ValueError("invalid input"),
    )
    validation_details = [
        ValidationDetail(
            type="pydantic.missing",
            msg="Field required",
            loc=("customer_id",),
        )
    ]
    activity_error = ApplicationError(
        "Failed to validate trigger inputs",
        validation_details,
        runtime_error_detail("trigger", envelope),
        type="ValidationError",
        non_retryable=True,
    )

    wrapped = _format_trigger_input_validation_error(
        activity_error,
        workflow_exec_id="wf_test/exec_test",
    )

    assert "Missing required field(s): 'customer_id'" in wrapped.message
    assert wrapped.type == "ValidationError"
    assert wrapped.non_retryable is True
    assert validation_details in wrapped.details
    extracted = extract_runtime_error_from_details(wrapped.details)
    assert extracted is not None
    assert extracted.kind == RuntimeErrorKind.USER
    assert extracted.code == "dsl.trigger_inputs.validation_failed"
    assert extracted.workflow_exec_id == "wf_test/exec_test"


def test_first_payload_detail_skips_runtime_metadata() -> None:
    envelope = RuntimeErrorEnvelope.build(
        kind=RuntimeErrorKind.INFRA,
        code="dsl.trigger_inputs.retrieve_failed",
        message="Failed to retrieve trigger inputs",
        origin=RuntimeErrorOrigin.DSL,
        phase=RuntimeErrorPhase.PREPARE,
        retryable=True,
        root=OSError("object storage unavailable"),
    )
    payload = {"action": {"message": "failed"}}

    assert _first_payload_detail((runtime_error_detail("trigger", envelope),)) is None
    assert (
        _first_payload_detail((runtime_error_detail("trigger", envelope), payload))
        is payload
    )


def test_first_payload_detail_preserves_action_error_ref_named_runtime_errors() -> None:
    payload = {
        "runtime_errors": {
            "ref": "runtime_errors",
            "message": "Action failed",
            "type": "ValueError",
        }
    }

    assert _first_payload_detail((payload,)) is payload


def test_workflow_runtime_error_classifies_in_workflow_failure() -> None:
    root = RuntimeError("schedule missing")

    workflow_error = WorkflowRuntimeError.user(
        code="dsl.trigger_inputs.schedule_not_found",
        message="Failed to fetch trigger inputs as the schedule was not found",
        origin=RuntimeErrorOrigin.DSL,
        phase=RuntimeErrorPhase.PREPARE,
        error_type="TracecatNotFoundError",
        root=root,
        workflow_exec_id="wf_test/exec_test",
        metadata={"schedule_id": "sched_test"},
    )

    assert workflow_error.type == "TracecatNotFoundError"
    assert workflow_error.non_retryable is True
    envelope = extract_runtime_error_from_details(workflow_error.details)
    assert envelope is not None
    assert envelope.kind == RuntimeErrorKind.USER
    assert envelope.code == "dsl.trigger_inputs.schedule_not_found"
    assert envelope.workflow_exec_id == "wf_test/exec_test"
    assert envelope.metadata == {"schedule_id": "sched_test"}
