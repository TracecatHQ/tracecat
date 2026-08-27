"""Temporal transport for the versioned Tracecat runtime error contract.

Classified failures travel as ``ClassifiedErrorDetail`` transport details: a
required-discriminator wrapper carrying the ``ErrorEnvelope`` attribution
stamp plus an optional ``ActionErrorInfo`` payload. Terminal workflow errors
use the established ``{ref: ClassifiedErrorDetail}`` map. Classification is
structural — a payload either parses as one of those shapes or it is
unclassified; there is no partially classified state. Bare legacy payloads
(pre-envelope histories) therefore fall through as unclassified by
construction.
"""

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


class ClassifiedErrorDetail(BaseModel):
    """A classified transport detail: envelope stamp plus optional payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    # Required so validation itself rejects undiscriminated payloads and the
    # discriminator always survives ``exclude_unset`` serialization.
    schema_: Literal["tracecat.temporal_error.v1"] = Field(alias="schema")
    envelope: ErrorEnvelope
    error: ActionErrorInfo | None = None


# The two classified transport shapes: a single wrapped detail, or the
# established terminal workflow ``{ref: detail}`` map. Anything else —
# including bare legacy ``ActionErrorInfo`` payloads — is unclassified.
type ClassifiedDetail = ClassifiedErrorDetail | dict[str, ClassifiedErrorDetail]

_CLASSIFIED_DETAIL_ADAPTER: TypeAdapter[ClassifiedDetail] = TypeAdapter(
    ClassifiedDetail
)


def wrap_error(
    envelope: ErrorEnvelope, error: ActionErrorInfo | None = None
) -> ClassifiedErrorDetail:
    """Build a classified transport detail from an envelope and payload."""
    return ClassifiedErrorDetail.model_validate(
        {
            "schema": TEMPORAL_ERROR_DETAILS_SCHEMA,
            "envelope": envelope,
            "error": error,
        }
    )


def parse_classified_detail(
    detail: Any,
) -> ClassifiedDetail | None:
    """Parse a transport detail into its classified shape, or None."""
    try:
        return _CLASSIFIED_DETAIL_ADAPTER.validate_python(detail)
    except ValidationError:
        return None


def is_classified_detail(detail: Any) -> bool:
    """Return whether a single transport detail is explicitly classified."""
    return bool(_envelopes_from_detail(detail))


def application_error_from_envelope(
    envelope: ErrorEnvelope,
    *details: Any,
    next_retry_delay: timedelta | None = None,
) -> ApplicationError:
    """Build an ``ApplicationError`` with transport fields derived from its envelope.

    The passed envelope is authoritative for the transport fields (message,
    type, retryability) — detail order never overrides the caller's selection.
    Existing details remain in their original order; if none of them is a
    classified detail, a bare wrapper is appended. Temporal's error type
    mirrors the stable product kind and is never supplied as a second
    classification input.
    """
    transported_details = tuple(_serialized_detail(detail) for detail in details)
    if _envelope_from_details(details) is None:
        transported_details = (
            *transported_details,
            wrap_error(envelope).model_dump(mode="json"),
        )

    non_retryable = envelope.retry_disposition is RetryDisposition.NON_RETRYABLE
    if non_retryable and next_retry_delay is not None:
        raise ValueError("A non-retryable error cannot set next_retry_delay")

    return ApplicationError(
        envelope.message,
        *transported_details,
        type=envelope.kind.value,
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
    raise application_error_from_envelope(
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


def extract_error_envelopes(
    error: BaseException,
    *,
    include_implicit_context: bool = True,
) -> tuple[ErrorEnvelope, ...]:
    """Extract every valid envelope from an exception chain in transport order.

    Args:
        error: The exception whose classification should be extracted.
        include_implicit_context: Whether to traverse Python's incidental
            ``__context__`` chain in addition to Temporal and explicit causes.
    """
    envelopes: list[ErrorEnvelope] = []
    seen: set[ErrorEnvelope] = set()
    for current in _error_chain(
        error,
        include_implicit_context=include_implicit_context,
    ):
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


def _envelopes_from_detail(detail: Any) -> tuple[ErrorEnvelope, ...]:
    match parse_classified_detail(detail):
        case None:
            return ()
        case ClassifiedErrorDetail() as parsed:
            return (parsed.envelope,)
        case parsed:
            return tuple(wrapped.envelope for wrapped in parsed.values())


def _serialized_detail(detail: Any) -> Any:
    if isinstance(detail, ClassifiedErrorDetail):
        return detail.model_dump(mode="json")
    return detail


def _append_unique_envelope(
    envelopes: list[ErrorEnvelope],
    seen: set[ErrorEnvelope],
    envelope: ErrorEnvelope,
) -> None:
    if envelope not in seen:
        seen.add(envelope)
        envelopes.append(envelope)


def _error_chain(
    error: BaseException,
    *,
    include_implicit_context: bool = True,
) -> Iterator[BaseException]:
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
        elif (
            include_implicit_context
            and not current.__suppress_context__
            and isinstance(current.__context__, BaseException)
        ):
            current = current.__context__
        else:
            current = None
