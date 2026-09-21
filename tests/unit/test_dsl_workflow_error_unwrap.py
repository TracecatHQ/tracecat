from tracecat.dsl.workflow import DSLWorkflow
from tracecat.expressions.common import ExprContext
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


def test_trigger_validation_error_info_is_action_compatible() -> None:
    error_info = DSLWorkflow._trigger_validation_error_info(
        "Failed to validate trigger inputs",
        [
            ValidationDetail(
                type="pydantic.string_type",
                msg="Input should be a valid string",
                loc=("ticket_key",),
            )
        ],
    )

    assert error_info.ref == "<root>"
    assert error_info.type == "ValidationError"
    assert error_info.expr_context == ExprContext.TRIGGER
    assert error_info.children is not None
    assert len(error_info.children) == 1
    assert error_info.children[0].ref == "ticket_key"
    assert error_info.children[0].type == "pydantic.string_type"
    assert error_info.children[0].expr_context == ExprContext.TRIGGER
