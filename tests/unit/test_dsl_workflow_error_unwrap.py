from tracecat.dsl.workflow import DSLWorkflow


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
