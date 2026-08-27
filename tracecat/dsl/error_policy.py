"""Terminal error adaptation policy for DSL workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from temporalio.exceptions import ApplicationError

from tracecat.dsl.types import ActionErrorInfo, TaskExceptionInfo
from tracecat.runtime.errors import ErrorEnvelope, select_error_envelope
from tracecat.temporal.errors import (
    application_error_from_envelope,
    extract_error_envelopes,
    parse_classified_detail,
    wrap_error,
)


def build_terminal_application_error(
    task_exceptions: Mapping[str, TaskExceptionInfo],
) -> ApplicationError:
    """Build the terminal workflow error without changing legacy payloads."""
    n_exceptions = len(task_exceptions)
    task_envelopes: dict[str, ErrorEnvelope] = {}
    for ref, info in task_exceptions.items():
        envelopes = extract_error_envelopes(info.exception)
        if not envelopes:
            break
        task_envelopes[ref] = select_error_envelope(envelopes)
    else:
        if task_envelopes:
            terminal_envelope = select_error_envelope(task_envelopes.values())
            error_details = {
                ref: wrap_error(task_envelopes[ref], info.details).model_dump(
                    mode="json"
                )
                for ref, info in task_exceptions.items()
            }
            return application_error_from_envelope(
                terminal_envelope,
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
    if isinstance(parsed := parse_classified_detail(err_info_map), dict):
        return [
            wrapped.error
            if wrapped.error is not None
            else ActionErrorInfo(
                ref=child_ref,
                message=wrapped.envelope.message,
                type=wrapped.envelope.kind.value,
            )
            for child_ref, wrapped in parsed.items()
        ]
    return [ActionErrorInfo.model_validate(data) for data in err_info_map.values()]
