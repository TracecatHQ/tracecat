"""Temporal transport for the versioned Tracecat runtime error contract."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import timedelta
from typing import Any, Literal, Never

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio.exceptions import ApplicationError, FailureError

from tracecat.dsl.types import (
    ActionErrorInfo,
    ActionErrorInfoAdapter,
    ClassifiedActionErrorInfo,
)
from tracecat.runtime.errors import (
    ErrorEnvelope,
    RetryDisposition,
    TracecatRuntimeError,
    parse_error_envelope,
)

TEMPORAL_ERROR_DETAILS_SCHEMA = "tracecat.temporal_error.v1"


class TemporalErrorDetails(BaseModel):
    """Strict adapter for envelopes without an ``ActionErrorInfo`` payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    schema_: Literal["tracecat.temporal_error.v1"] = Field(
        default=TEMPORAL_ERROR_DETAILS_SCHEMA,
        alias="schema",
    )
    envelope: ErrorEnvelope


def _application_error_from_envelope(
    envelope: ErrorEnvelope,
    *details: Any,
    next_retry_delay: timedelta | None = None,
) -> ApplicationError:
    """Build an ``ApplicationError`` with transport fields derived from its envelope.

    Existing details remain in their original order. If they already contain a
    fully validated envelope, they are left unchanged; otherwise a strict
    non-action detail is appended. Temporal's error type mirrors the stable
    product kind and is never supplied as a second classification input.
    """
    existing_envelope = _envelope_from_details(details)
    resolved_envelope = existing_envelope or envelope
    serialized_details = _serialized_error_details(
        details,
        envelope=None if existing_envelope is not None else envelope,
    )
    transported_details = (
        serialized_details if serialized_details is not None else tuple(details)
    )
    if existing_envelope is None and serialized_details is None:
        adapter = TemporalErrorDetails(envelope=envelope)
        transported_details = (*details, adapter.model_dump(mode="json"))

    non_retryable = (
        resolved_envelope.retry_disposition is RetryDisposition.NON_RETRYABLE
    )
    if non_retryable and next_retry_delay is not None:
        raise ValueError("A non-retryable error cannot set next_retry_delay")

    return ApplicationError(
        resolved_envelope.message,
        *transported_details,
        type=resolved_envelope.kind.value,
        non_retryable=non_retryable,
        next_retry_delay=next_retry_delay,
    )


def raise_application_error_from_envelope(
    envelope: ErrorEnvelope,
    *details: Any,
    next_retry_delay: timedelta | None = None,
) -> Never:
    """Raise a classified error without serializing the active exception context.

    Temporal serializes exception chains into workflow history. Owning the raise
    here ensures callers cannot accidentally attach a sensitive caught exception
    through implicit chaining.
    """
    raise _application_error_from_envelope(
        envelope,
        *details,
        next_retry_delay=next_retry_delay,
    ) from None


def raise_wrapped_application_error(
    error: BaseException,
    *,
    fallback: ErrorEnvelope,
    details: Sequence[Any] = (),
) -> Never:
    """Raise a history-safe wrapper while preserving existing classification."""
    envelope = extract_error_envelope(error) or fallback
    if isinstance(error, ApplicationError):
        wrapped_details = tuple(error.details) if not details else tuple(details)
        next_retry_delay = error.next_retry_delay
    else:
        wrapped_details = tuple(details)
        next_retry_delay = None

    raise_application_error_from_envelope(
        envelope,
        *wrapped_details,
        next_retry_delay=next_retry_delay,
    )


def extract_error_envelope(error: BaseException) -> ErrorEnvelope | None:
    """Extract the first valid envelope from an exception chain."""
    for current in _error_chain(error):
        if isinstance(current, TracecatRuntimeError):
            return current.envelope
        if isinstance(current, ApplicationError):
            if envelope := _envelope_from_details(current.details):
                return envelope
    return None


def _envelope_from_details(details: Sequence[Any]) -> ErrorEnvelope | None:
    for detail in details:
        if envelope := _envelope_from_detail(detail):
            return envelope
    return None


def _serialized_error_details(
    details: Sequence[Any], envelope: ErrorEnvelope | None
) -> tuple[Any, ...] | None:
    serialized: list[Any] = []
    changed = False
    for detail in details:
        if isinstance(detail, ClassifiedActionErrorInfo):
            serialized.append(ActionErrorInfoAdapter.dump_python(detail, mode="json"))
            changed = True
        elif isinstance(detail, TemporalErrorDetails):
            serialized.append(detail.model_dump(mode="json"))
            changed = True
        elif isinstance(detail, ActionErrorInfo) and envelope is not None:
            classified = ClassifiedActionErrorInfo(
                ref=detail.ref,
                message=detail.message,
                type=detail.type,
                expr_context=detail.expr_context,
                attempt=detail.attempt,
                stream_id=detail.stream_id,
                children=detail.children,
                envelope=envelope,
            )
            serialized.append(
                ActionErrorInfoAdapter.dump_python(classified, mode="json")
            )
            changed = True
        else:
            serialized.append(detail)
    return tuple(serialized) if changed else None


def _envelope_from_detail(detail: Any) -> ErrorEnvelope | None:
    if isinstance(detail, ClassifiedActionErrorInfo):
        return detail.envelope
    if isinstance(detail, TemporalErrorDetails):
        return detail.envelope
    if not isinstance(detail, Mapping):
        return None

    if detail.get("schema") == TEMPORAL_ERROR_DETAILS_SCHEMA:
        if parse_error_envelope(detail.get("envelope")) is None:
            return None
        try:
            return TemporalErrorDetails.model_validate(detail).envelope
        except ValidationError:
            return None

    return parse_error_envelope(detail.get("envelope"))


def _error_chain(error: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None:
        current_id = id(current)
        if current_id in seen:
            return
        seen.add(current_id)
        yield current

        if isinstance(current, FailureError) and isinstance(
            current.cause, BaseException
        ):
            current = current.cause
        elif isinstance(current.__cause__, BaseException):
            current = current.__cause__
        elif not current.__suppress_context__ and isinstance(
            current.__context__, BaseException
        ):
            current = current.__context__
        else:
            current = None
