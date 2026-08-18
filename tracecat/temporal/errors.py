"""Temporal transport for the versioned Tracecat runtime error contract."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import timedelta
from typing import Any, Literal

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


def application_error_from_envelope(
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


def wrap_application_error(
    error: BaseException,
    *,
    fallback: ErrorEnvelope,
    details: Sequence[Any] = (),
) -> ApplicationError:
    """Wrap an exception while preserving any existing classification."""
    envelope = extract_error_envelope(error) or fallback
    if isinstance(error, ApplicationError):
        wrapped_details = tuple(error.details) if not details else tuple(details)
        next_retry_delay = error.next_retry_delay
    else:
        wrapped_details = tuple(details)
        next_retry_delay = None

    return application_error_from_envelope(
        envelope,
        *wrapped_details,
        next_retry_delay=next_retry_delay,
    )


def extract_error_envelope(error: BaseException) -> ErrorEnvelope | None:
    """Extract the first valid envelope from an exception chain."""
    envelopes = extract_error_envelopes(error)
    return envelopes[0] if envelopes else None


def extract_error_envelopes(error: BaseException) -> tuple[ErrorEnvelope, ...]:
    """Extract every valid envelope from an exception chain in transport order."""
    envelopes: list[ErrorEnvelope] = []
    for current in _error_chain(error):
        if isinstance(current, TracecatRuntimeError):
            _append_unique_envelope(envelopes, current.envelope)
        if isinstance(current, ApplicationError):
            for envelope in _envelopes_from_details(current.details):
                _append_unique_envelope(envelopes, envelope)
    return tuple(envelopes)


def _envelope_from_details(details: Sequence[Any]) -> ErrorEnvelope | None:
    envelopes = _envelopes_from_details(details)
    return envelopes[0] if envelopes else None


def _envelopes_from_details(details: Sequence[Any]) -> tuple[ErrorEnvelope, ...]:
    envelopes: list[ErrorEnvelope] = []
    for detail in details:
        for envelope in _envelopes_from_detail(detail):
            _append_unique_envelope(envelopes, envelope)
    return tuple(envelopes)


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


def _envelopes_from_detail(detail: Any) -> tuple[ErrorEnvelope, ...]:
    if isinstance(detail, ClassifiedActionErrorInfo):
        return (detail.envelope,)
    if isinstance(detail, TemporalErrorDetails):
        return (detail.envelope,)
    if not isinstance(detail, Mapping):
        return ()

    if detail.get("schema") == TEMPORAL_ERROR_DETAILS_SCHEMA:
        if parse_error_envelope(detail.get("envelope")) is None:
            return ()
        try:
            return (TemporalErrorDetails.model_validate(detail).envelope,)
        except ValidationError:
            return ()

    if envelope := parse_error_envelope(detail.get("envelope")):
        return (envelope,)

    # Workflow-level action failures retain the established ``{ref: info}``
    # details shape. Validate each complete value as ActionErrorInfo rather than
    # recursively searching arbitrary mappings, which would allow user payload
    # keys to collide with the envelope contract.
    envelopes: list[ErrorEnvelope] = []
    for value in detail.values():
        try:
            parsed = ActionErrorInfoAdapter.validate_python(value)
        except ValidationError:
            continue
        if isinstance(parsed, ClassifiedActionErrorInfo):
            _append_unique_envelope(envelopes, parsed.envelope)
    return tuple(envelopes)


def _append_unique_envelope(
    envelopes: list[ErrorEnvelope], envelope: ErrorEnvelope
) -> None:
    if envelope not in envelopes:
        envelopes.append(envelope)


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
