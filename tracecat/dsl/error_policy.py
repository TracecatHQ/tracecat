"""Terminal error adaptation policy for DSL workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from temporalio.exceptions import ApplicationError

from tracecat.dsl.types import ActionErrorInfo, TaskExceptionInfo
from tracecat.runtime.errors import (
    RuntimeErrorClassification,
    select_error_classification,
)
from tracecat.temporal.errors import (
    application_error_from_classification,
    build_error_transport_detail,
    extract_error_classifications,
    parse_classified_error_payload,
)


def build_terminal_application_error(
    task_exceptions: Mapping[str, TaskExceptionInfo],
) -> ApplicationError:
    """Build the terminal workflow error without changing legacy payloads."""
    n_exceptions = len(task_exceptions)
    task_classifications: dict[str, RuntimeErrorClassification] = {}
    for ref, info in task_exceptions.items():
        classifications = extract_error_classifications(info.exception)
        if not classifications:
            break
        task_classifications[ref] = select_error_classification(classifications)
    else:
        if task_classifications:
            terminal_classification = select_error_classification(
                task_classifications.values()
            )
            error_details = {
                ref: build_error_transport_detail(
                    task_classifications[ref],
                    info.details,
                ).model_dump(mode="json")
                for ref, info in task_exceptions.items()
            }
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
    if isinstance(parsed := parse_classified_error_payload(err_info_map), dict):
        return [
            transport_detail.action_error
            if transport_detail.action_error is not None
            else ActionErrorInfo(
                ref=child_ref,
                message=transport_detail.classification.message,
                type=transport_detail.classification.kind.value,
            )
            for child_ref, transport_detail in parsed.items()
        ]
    return [ActionErrorInfo.model_validate(data) for data in err_info_map.values()]
