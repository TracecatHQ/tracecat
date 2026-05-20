from tracecat.dsl.workflow import DSLWorkflow
from tracecat.runtime.errors import (
    RuntimeErrorEnvelope,
    RuntimeErrorKind,
    RuntimeErrorOrigin,
    RuntimeErrorPhase,
)
from tracecat.temporal.errors import (
    TemporalErrorDetails,
    WorkflowRuntimeError,
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


def test_temporal_error_details_carries_payload_and_runtime_error() -> None:
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
    parsed = TemporalErrorDetails.from_details(
        (
            TemporalErrorDetails.with_runtime_error(
                "trigger",
                envelope,
                payloads=(payload,),
            ),
        )
    )

    assert (
        TemporalErrorDetails.from_details(
            (TemporalErrorDetails.with_runtime_error("trigger", envelope),)
        ).first_payload
        is None
    )
    assert parsed.first_payload is payload
    assert parsed.runtime_errors == {"trigger": envelope}


def test_temporal_error_details_treats_plain_dict_as_payload() -> None:
    payload = {
        "runtime_errors": {
            "ref": "runtime_errors",
            "message": "Action failed",
            "type": "ValueError",
        }
    }

    assert TemporalErrorDetails.from_details((payload,)).first_payload is payload


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
    envelope = TemporalErrorDetails.runtime_error_from_details(workflow_error.details)
    assert envelope is not None
    assert envelope.kind == RuntimeErrorKind.USER
    assert envelope.code == "dsl.trigger_inputs.schedule_not_found"
    assert envelope.workflow_exec_id == "wf_test/exec_test"
    assert envelope.metadata == {"schedule_id": "sched_test"}
