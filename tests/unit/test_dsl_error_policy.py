"""Terminal and child aggregate DSL error policy tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from temporalio.exceptions import ApplicationError, CancelledError

from tests.shared import capture_application_error as _capture_application_error
from tracecat.dsl.error_policy import raise_child_failures_application_error
from tracecat.dsl.error_transport import (
    ActionErrorTransportDetail,
    parse_classified_action_error_payload,
)
from tracecat.dsl.types import ActionErrorInfo, TaskExceptionInfo
from tracecat.dsl.workflow import DSLWorkflow, _raise_workflow_application_error
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import (
    build_error_transport_detail,
    extract_error_classification,
    extract_error_classifications,
)


def _action_error_info(
    classification: RuntimeErrorClassification,
    *,
    ref: str,
) -> ActionErrorInfo:
    """Build the classification-free payload for a classified failure."""
    return ActionErrorInfo(
        ref=ref,
        message=classification.message,
        type=classification.cause_type or "ApplicationError",
    )


def _capture_workflow_application_error(
    task_exceptions: dict[str, TaskExceptionInfo],
) -> ApplicationError:
    with pytest.raises(ApplicationError) as exc_info:
        _raise_workflow_application_error(task_exceptions)
    return exc_info.value


def _capture_child_failures_application_error(
    *,
    task_ref: str,
    failures: list[tuple[int, BaseException]],
) -> ApplicationError:
    with pytest.raises(ApplicationError) as exc_info:
        raise_child_failures_application_error(
            task_ref=task_ref,
            failures=failures,
        )
    return exc_info.value


def test_workflow_error_keeps_one_classification_per_action() -> None:
    """The terminal map keeps each task detail and propagates each primary."""
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    user_detail = _action_error_info(user_classification, ref="user_action")
    platform_detail = _action_error_info(platform_classification, ref="platform_action")
    task_exceptions = {
        "user_action": TaskExceptionInfo(
            exception=_capture_application_error(
                user_classification,
                build_error_transport_detail(user_classification, user_detail),
            ),
            details=user_detail,
        ),
        "platform_action": TaskExceptionInfo(
            exception=_capture_application_error(
                platform_classification,
                build_error_transport_detail(platform_classification, platform_detail),
            ),
            details=platform_detail,
        ),
    }

    error = _capture_workflow_application_error(task_exceptions)

    assert error.message == platform_classification.message
    assert error.type == platform_classification.kind.value
    assert error.non_retryable is False
    assert extract_error_classifications(error) == (
        user_classification,
        platform_classification,
    )
    assert error.details[0]["user_action"][
        "classification"
    ] == user_classification.model_dump(mode="json")
    assert error.details[0]["user_action"]["diagnostic"] == user_detail.model_dump(
        mode="json"
    )
    assert error.details[0]["platform_action"][
        "classification"
    ] == platform_classification.model_dump(mode="json")
    assert error.details[0]["platform_action"][
        "diagnostic"
    ] == platform_detail.model_dump(mode="json")


def test_workflow_error_map_platform_entry_survives_terminal_aggregation() -> None:
    """A platform-owned entry in a task's child terminal map wins selection."""
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the child action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    user_detail = _action_error_info(user_classification, ref="user_child")
    platform_detail = _action_error_info(platform_classification, ref="platform_child")
    child_error = _capture_application_error(
        user_classification,
        {
            "user_child": build_error_transport_detail(
                user_classification, user_detail
            ).model_dump(mode="json"),
            "platform_child": build_error_transport_detail(
                platform_classification, platform_detail
            ).model_dump(mode="json"),
        },
    )
    aggregate_detail = ActionErrorInfo(
        ref="call_child",
        message=platform_classification.message,
        type="ApplicationError",
        children=[user_detail, platform_detail],
    )

    error = _capture_workflow_application_error(
        {
            "call_child": TaskExceptionInfo(
                exception=child_error, details=aggregate_detail
            )
        }
    )

    assert error.message == platform_classification.message
    assert error.type == platform_classification.kind.value
    assert error.non_retryable is False
    assert extract_error_classifications(error) == (platform_classification,)
    assert error.details[0]["call_child"][
        "classification"
    ] == platform_classification.model_dump(mode="json")
    assert error.details[0]["call_child"]["diagnostic"] == aggregate_detail.model_dump(
        mode="json"
    )


def test_workflow_error_selects_one_classification_from_one_aggregate() -> None:
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    user_detail = _action_error_info(user_classification, ref="fanout[0]")
    platform_detail = _action_error_info(platform_classification, ref="fanout[1]")
    aggregate_error = _capture_application_error(
        user_classification,
        build_error_transport_detail(user_classification, user_detail),
        build_error_transport_detail(platform_classification, platform_detail),
    )

    error = _capture_workflow_application_error(
        {
            "fanout": TaskExceptionInfo(
                exception=aggregate_error,
                details=user_detail,
            )
        }
    )

    assert extract_error_classifications(error) == (platform_classification,)


def test_child_failure_aggregate_selects_one_classification_per_child() -> None:
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    child_error = _capture_application_error(
        user_classification,
        build_error_transport_detail(
            user_classification,
            _action_error_info(user_classification, ref="nested[0]"),
        ),
        build_error_transport_detail(
            platform_classification,
            _action_error_info(platform_classification, ref="nested[1]"),
        ),
    )

    error = _capture_child_failures_application_error(
        task_ref="fanout",
        failures=[(7, child_error)],
    )

    assert error.non_retryable is True
    aggregate_transport = parse_classified_action_error_payload(error.details[0])
    assert isinstance(aggregate_transport, ActionErrorTransportDetail)
    aggregate = aggregate_transport.diagnostic
    assert aggregate is not None
    assert aggregate.children is not None
    assert [child.ref for child in aggregate.children] == ["fanout[7]"]
    assert len(error.details) == 2
    classifications = extract_error_classifications(error)
    assert len(classifications) == 2
    assert classifications[1] == platform_classification
    assert user_classification not in classifications
    assert {classification.retry_disposition for classification in classifications} == {
        RetryDisposition.RETRYABLE,
        RetryDisposition.NON_RETRYABLE,
    }


def test_child_failure_aggregate_includes_every_classified_child() -> None:
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the child action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )

    error = _capture_child_failures_application_error(
        task_ref="fanout",
        failures=[
            (3, _capture_application_error(user_classification)),
            (4, _capture_application_error(platform_classification)),
        ],
    )

    aggregate_transport = parse_classified_action_error_payload(error.details[0])
    assert isinstance(aggregate_transport, ActionErrorTransportDetail)
    aggregate = aggregate_transport.diagnostic
    assert aggregate is not None
    assert aggregate.children is not None
    assert [child.ref for child in aggregate.children] == ["fanout[3]", "fanout[4]"]
    classifications = extract_error_classifications(error)
    assert len(classifications) == 3
    assert classifications[0].owner is RuntimeErrorOwner.PLATFORM
    assert classifications[0].retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classifications[1:] == (
        user_classification,
        platform_classification,
    )


def test_mixed_child_failures_use_unclassified_aggregate_with_every_child() -> None:
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )

    error = _capture_child_failures_application_error(
        task_ref="fanout",
        failures=[
            (3, _capture_application_error(user_classification)),
            (4, RuntimeError("Legacy platform failure")),
        ],
    )

    assert error.message == "2 child workflow(s) failed"
    assert error.type == ApplicationError.__name__
    assert error.non_retryable is True
    assert extract_error_classifications(error) == ()
    assert len(error.details) == 1
    assert isinstance(error.details[0], dict)
    aggregate = ActionErrorInfo.model_validate(error.details[0]["fanout"])
    assert aggregate.children is not None
    assert [child.ref for child in aggregate.children] == ["fanout[3]", "fanout[4]"]
    assert [child.message for child in aggregate.children] == [
        user_classification.message,
        "Legacy platform failure",
    ]
    with patch("tracecat.dsl.workflow.workflow.patched") as patched_mock:
        DSLWorkflow._upsert_terminal_error_owner(error)
    patched_mock.assert_not_called()


def test_child_cancellation_does_not_erase_classified_causal_failure() -> None:
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The child action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )

    error = _capture_child_failures_application_error(
        task_ref="fanout",
        failures=[
            (3, _capture_application_error(user_classification)),
            (4, CancelledError()),
        ],
    )

    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.cause_type == "ChildWorkflowAggregateError"
    aggregate_transport = parse_classified_action_error_payload(error.details[0])
    assert isinstance(aggregate_transport, ActionErrorTransportDetail)
    assert aggregate_transport.diagnostic is not None
    assert aggregate_transport.diagnostic.children is not None
    assert [child.ref for child in aggregate_transport.diagnostic.children] == [
        "fanout[3]",
        "fanout[4]",
    ]
    assert aggregate_transport.diagnostic.children[1].type == "CancelledError"


def test_legacy_workflow_error_shape_is_unchanged() -> None:
    detail = ActionErrorInfo(ref="action", message="Failed", type="ValueError")
    error = _capture_workflow_application_error(
        {
            "action": TaskExceptionInfo(
                exception=ApplicationError("Failed", non_retryable=True),
                details=detail,
            )
        }
    )

    assert error.non_retryable is True
    assert error.details == ({"action": detail},)
    assert extract_error_classification(error) is None


def test_mixed_workflow_errors_use_the_unclassified_terminal_raise() -> None:
    """One unclassified task keeps the whole terminal raise on the legacy shape."""
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    user_detail = _action_error_info(user_classification, ref="user_action")
    legacy_detail = ActionErrorInfo(
        ref="legacy_action",
        message="Legacy failure",
        type="RuntimeError",
    )
    error = _capture_workflow_application_error(
        {
            "user_action": TaskExceptionInfo(
                exception=_capture_application_error(
                    user_classification,
                    build_error_transport_detail(user_classification, user_detail),
                ),
                details=user_detail,
            ),
            "legacy_action": TaskExceptionInfo(
                exception=ApplicationError("Legacy failure", non_retryable=True),
                details=legacy_detail,
            ),
        }
    )

    assert error.message.startswith("Workflow failed with 2 error(s)")
    assert error.details == (
        {"user_action": user_detail, "legacy_action": legacy_detail},
    )
    assert extract_error_classifications(error) == ()
    with patch("tracecat.dsl.workflow.workflow.patched") as patched_mock:
        assert DSLWorkflow._has_user_error_cause(error) is False
    patched_mock.assert_not_called()


def test_terminal_cancellation_does_not_erase_classified_causal_failure() -> None:
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    user_detail = _action_error_info(user_classification, ref="user_action")
    cancelled_detail = ActionErrorInfo(
        ref="cancelled_action",
        message="Cancelled",
        type="CancelledError",
    )

    error = _capture_workflow_application_error(
        {
            "user_action": TaskExceptionInfo(
                exception=_capture_application_error(user_classification),
                details=user_detail,
            ),
            "cancelled_action": TaskExceptionInfo(
                exception=CancelledError(),
                details=cancelled_detail,
            ),
        }
    )

    assert extract_error_classification(error) == user_classification
    assert error.details[0] == {
        "user_action": user_detail,
        "cancelled_action": cancelled_detail,
    }
    assert len(error.details) == 2


def test_terminal_error_named_cancelled_does_not_mask_unclassified_failure() -> None:
    """An error type string alone does not prove Temporal cancellation."""
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND,
        message="The child workflow could not be found",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    user_detail = _action_error_info(user_classification, ref="call_child")
    cancelled_detail = ActionErrorInfo(
        ref="slow_sibling",
        message="Cancelled",
        type=CancelledError.__name__,
    )

    error = _capture_workflow_application_error(
        {
            "call_child": TaskExceptionInfo(
                exception=_capture_application_error(user_classification),
                details=user_detail,
            ),
            "slow_sibling": TaskExceptionInfo(
                exception=ApplicationError(
                    "Cancelled",
                    non_retryable=True,
                    type=CancelledError.__name__,
                ),
                details=cancelled_detail,
            ),
        }
    )

    assert extract_error_classification(error) is None
    assert error.details[0] == {
        "call_child": user_detail,
        "slow_sibling": cancelled_detail,
    }


def test_terminal_cancellation_preserves_every_causal_classification() -> None:
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    user_detail = _action_error_info(user_classification, ref="user_action")
    platform_detail = _action_error_info(
        platform_classification,
        ref="platform_action",
    )
    cancelled_detail = ActionErrorInfo(
        ref="cancelled_action",
        message="Cancelled",
        type="CancelledError",
    )

    error = _capture_workflow_application_error(
        {
            "user_action": TaskExceptionInfo(
                exception=_capture_application_error(user_classification),
                details=user_detail,
            ),
            "platform_action": TaskExceptionInfo(
                exception=_capture_application_error(platform_classification),
                details=platform_detail,
            ),
            "cancelled_action": TaskExceptionInfo(
                exception=CancelledError(),
                details=cancelled_detail,
            ),
        }
    )

    assert error.message == platform_classification.message
    assert error.type == platform_classification.kind.value
    assert error.details[0] == {
        "user_action": user_detail,
        "platform_action": platform_detail,
        "cancelled_action": cancelled_detail,
    }
    assert extract_error_classifications(error) == (
        user_classification,
        platform_classification,
    )
    assert len(error.details) == 3
