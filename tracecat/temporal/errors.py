"""Temporal transport for the versioned Tracecat runtime error contract."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import timedelta
from typing import Any, Literal, Never

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from temporalio.exceptions import ApplicationError, FailureError

from tracecat.dsl.types import ActionErrorInfo
from tracecat.runtime.errors import (
    ErrorEnvelope,
    RetryDisposition,
    TracecatRuntimeError,
)

TEMPORAL_ERROR_DETAILS_SCHEMA = "tracecat.temporal_error.v1"


class TemporalErrorDetails(BaseModel):
    """Strict adapter for envelopes without an ``ActionErrorInfo`` payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    # Required so validation itself rejects undiscriminated payloads and the
    # discriminator always survives ``exclude_unset`` serialization.
    schema_: Literal["tracecat.temporal_error.v1"] = Field(alias="schema")
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
    transported_details = _serialized_error_details(
        details,
        envelope=None if existing_envelope is not None else envelope,
    )
    if (
        existing_envelope is None
        and _envelope_from_details(transported_details) is None
    ):
        adapter = TemporalErrorDetails.model_validate(
            {"schema": TEMPORAL_ERROR_DETAILS_SCHEMA, "envelope": resolved_envelope}
        )
        transported_details = (
            *transported_details,
            adapter.model_dump(mode="json"),
        )

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
    details_envelope = (
        _envelope_from_details(error.details)
        if isinstance(error, ApplicationError)
        else None
    )
    envelope = details_envelope or extract_error_envelope(error) or fallback
    if isinstance(error, ApplicationError):
        wrapped_details = (
            tuple(error.details)
            if details_envelope is not None and not details
            else tuple(details)
        )
        next_retry_delay = (
            error.next_retry_delay
            if envelope.retry_disposition is RetryDisposition.RETRYABLE
            else None
        )
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
        if (
            isinstance(current, ApplicationError)
            and (envelope := _envelope_from_details(current.details)) is not None
        ):
            return envelope
    return None


def extract_error_envelopes(error: BaseException) -> tuple[ErrorEnvelope, ...]:
    """Extract every valid envelope from an exception chain in transport order."""
    envelopes: list[ErrorEnvelope] = []
    seen: set[ErrorEnvelope] = set()
    for current in _error_chain(error):
        if isinstance(current, TracecatRuntimeError):
            _append_unique_envelope(envelopes, seen, current.envelope)
        if isinstance(current, ApplicationError):
            for envelope in extract_error_envelopes_from_details(current.details):
                _append_unique_envelope(envelopes, seen, envelope)
    return tuple(envelopes)


def extract_error_envelopes_from_details(
    details: Sequence[Any],
) -> tuple[ErrorEnvelope, ...]:
    """Extract envelopes only from explicitly classified transport details."""
    envelopes: list[ErrorEnvelope] = []
    seen: set[ErrorEnvelope] = set()
    for detail in details:
        for envelope in _envelopes_from_detail(detail):
            _append_unique_envelope(envelopes, seen, envelope)
    return tuple(envelopes)


def _envelope_from_details(details: Sequence[Any]) -> ErrorEnvelope | None:
    for detail in details:
        if envelopes := _envelopes_from_detail(detail):
            return envelopes[0]
    return None


def is_classified_detail(detail: Any) -> bool:
    """Return whether a single transport detail is explicitly classified."""
    return bool(_envelopes_from_detail(detail))


def _serialized_error_details(
    details: Sequence[Any], envelope: ErrorEnvelope | None
) -> tuple[Any, ...]:
    serialized: list[Any] = []
    for detail in details:
        if isinstance(detail, TemporalErrorDetails):
            serialized.append(detail.model_dump(mode="json"))
        elif isinstance(detail, ActionErrorInfo) and (
            detail.envelope is not None or envelope is not None
        ):
            classified = ActionErrorInfo(
                ref=detail.ref,
                message=detail.message,
                type=detail.type,
                expr_context=detail.expr_context,
                attempt=detail.attempt,
                stream_id=detail.stream_id,
                children=detail.children,
                envelope=detail.envelope or envelope,
            )
            serialized.append(classified.model_dump(mode="json"))
        else:
            serialized.append(detail)
    return tuple(serialized)


# The three classified transport shapes: a per-action error (optionally
# nesting children), a bare envelope adapter, and the established workflow
# ``{ref: info}`` details map. Anything else is unclassified. Validation is the
# classification gate: both model shapes require the explicit ``schema``
# discriminator, so an undiscriminated payload fails to parse.
_CLASSIFIED_DETAIL_ADAPTER: TypeAdapter[
    ActionErrorInfo | TemporalErrorDetails | dict[str, ActionErrorInfo]
] = TypeAdapter(ActionErrorInfo | TemporalErrorDetails | dict[str, ActionErrorInfo])


def _envelopes_from_detail(detail: Any) -> tuple[ErrorEnvelope, ...]:
    try:
        parsed = _CLASSIFIED_DETAIL_ADAPTER.validate_python(detail)
    except ValidationError:
        return ()
    match parsed:
        case ActionErrorInfo():
            return _envelopes_from_action_error(parsed)
        case TemporalErrorDetails():
            return (parsed.envelope,)
        case _:
            # Every map value must be a complete, classified ActionErrorInfo;
            # accepting a partial match would let arbitrary user payloads
            # override classification.
            envelopes: list[ErrorEnvelope] = []
            seen: set[ErrorEnvelope] = set()
            for value in parsed.values():
                value_envelopes = _envelopes_from_action_error(value)
                if not value_envelopes:
                    return ()
                for envelope in value_envelopes:
                    _append_unique_envelope(envelopes, seen, envelope)
            return tuple(envelopes)


def _envelopes_from_action_error(parsed: ActionErrorInfo) -> tuple[ErrorEnvelope, ...]:
    """Collect envelopes from an action error tree, all-or-nothing."""
    envelopes: list[ErrorEnvelope] = []
    seen: set[ErrorEnvelope] = set()
    if parsed.envelope is not None:
        _append_unique_envelope(envelopes, seen, parsed.envelope)
    for child in parsed.children or ():
        child_envelopes = _envelopes_from_action_error(child)
        if not child_envelopes:
            return ()
        for child_envelope in child_envelopes:
            _append_unique_envelope(envelopes, seen, child_envelope)
    return tuple(envelopes)


def _append_unique_envelope(
    envelopes: list[ErrorEnvelope],
    seen: set[ErrorEnvelope],
    envelope: ErrorEnvelope,
) -> None:
    if envelope not in seen:
        seen.add(envelope)
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
