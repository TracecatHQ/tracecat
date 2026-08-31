"""Terminal error adaptation policy for DSL workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Never

from temporalio.exceptions import ApplicationError, is_cancelled_exception

from tracecat.dsl.error_transport import parse_classified_action_error_payload
from tracecat.dsl.types import ActionErrorInfo, TaskExceptionInfo
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    select_error_classification,
)
from tracecat.temporal.errors import (
    application_error_from_classification,
    build_error_transport_detail,
    extract_error_classifications,
    raise_application_error_from_classification,
)


def raise_child_failures_application_error(
    *, task_ref: str, failures: list[tuple[int, BaseException]]
) -> Never:
    """Raise child failures without letting cancellation erase their cause."""
    child_details: list[ActionErrorInfo] = []
    child_classifications: list[RuntimeErrorClassification | None] = []
    for child_index, failure in failures:
        classifications = extract_error_classifications(failure)
        if classifications:
            classification = select_error_classification(classifications)
            child_message = classification.message
            child_type = classification.cause_type or type(failure).__name__
        else:
            classification = None
            child_message = str(failure)
            child_type = type(failure).__name__
        child_classifications.append(classification)
        child_details.append(
            ActionErrorInfo(
                ref=f"{task_ref}[{child_index}]",
                message=child_message,
                type=child_type,
            )
        )

    message = f"{len(failures)} child workflow(s) failed"
    aggregate = ActionErrorInfo(
        ref=task_ref,
        message=message,
        type="ChildWorkflowAggregateError",
        children=child_details,
    )
    classified_failures = [
        classification
        for classification in child_classifications
        if classification is not None
    ]
    unclassified_failures = [
        failure
        for (_, failure), classification in zip(
            failures, child_classifications, strict=True
        )
        if classification is None
    ]
    if not classified_failures or any(
        not is_cancelled_exception(failure) for failure in unclassified_failures
    ):
        raise ApplicationError(
            message,
            {task_ref: aggregate},
            non_retryable=True,
            type=ApplicationError.__name__,
        )

    primary_classification = select_error_classification(classified_failures)
    aggregate_classification = primary_classification.model_copy(
        update={
            "message": message,
            "retry_disposition": RetryDisposition.NON_RETRYABLE,
            "cause_type": "ChildWorkflowAggregateError",
        }
    )
    raise_application_error_from_classification(
        aggregate_classification,
        build_error_transport_detail(aggregate_classification, aggregate),
        *(
            build_error_transport_detail(classification, diagnostic)
            for classification, diagnostic in zip(
                child_classifications, child_details, strict=True
            )
            if classification is not None
        ),
    )


def build_terminal_application_error(
    task_exceptions: Mapping[str, TaskExceptionInfo],
) -> ApplicationError:
    """Build the terminal error, treating concurrent cancellation as fallout."""
    n_exceptions = len(task_exceptions)
    task_classifications: dict[str, RuntimeErrorClassification] = {}
    unclassified_task_exceptions: list[BaseException] = []
    for ref, info in task_exceptions.items():
        classifications = extract_error_classifications(info.exception)
        if not classifications:
            unclassified_task_exceptions.append(info.exception)
            continue
        task_classifications[ref] = select_error_classification(classifications)

    if task_classifications and all(
        is_cancelled_exception(error) for error in unclassified_task_exceptions
    ):
        terminal_classification = select_error_classification(
            task_classifications.values()
        )
        if not unclassified_task_exceptions:
            error_details = {
                ref: build_error_transport_detail(
                    task_classifications[ref],
                    info.details,
                ).model_dump(mode="json")
                for ref, info in task_exceptions.items()
            }
        else:
            # A concurrent cancellation is diagnostic fallout, not a competing
            # causal classification. Keep the complete legacy map for the error
            # handler and append only the selected causal classification.
            error_details = {ref: info.details for ref, info in task_exceptions.items()}
        return application_error_from_classification(
            terminal_classification,
            error_details,
        )

    formatted_exceptions = "\n".join(
        f"{'=' * 10} ({i + 1}/{n_exceptions}) {details.expr_context}.{ref} {'=' * 10}\n\n{info.exception!s}"
        for i, (ref, info) in enumerate(task_exceptions.items())
        if (details := info.details)
    )
    return ApplicationError(
        f"Workflow failed with {n_exceptions} error(s)\n\n{formatted_exceptions}",
        {ref: info.details for ref, info in task_exceptions.items()},
        non_retryable=True,
        type=ApplicationError.__name__,
    )


def adapt_error_handler_details(
    details: Sequence[Any],
) -> list[ActionErrorInfo] | None:
    """Adapt terminal application error details for an error handler."""
    if not details:
        return None

    err_info_map = details[0]
    if not isinstance(err_info_map, dict):
        return [
            ActionErrorInfo(
                ref="N/A",
                message=(
                    "Unexpected error info object of type "
                    f"{type(err_info_map).__name__}: {err_info_map}"
                ),
                type=type(err_info_map).__name__,
            )
        ]
    if isinstance(parsed := parse_classified_action_error_payload(err_info_map), dict):
        return [
            transport_detail.diagnostic
            if transport_detail.diagnostic is not None
            else ActionErrorInfo(
                ref=child_ref,
                message=transport_detail.classification.message,
                type=transport_detail.classification.kind.value,
            )
            for child_ref, transport_detail in parsed.items()
        ]
    return [ActionErrorInfo.model_validate(data) for data in err_info_map.values()]
