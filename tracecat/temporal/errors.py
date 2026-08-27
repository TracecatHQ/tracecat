"""Temporal transport for the versioned Tracecat runtime error contract.

Classified failures travel as ``ErrorTransportDetail`` values: a required
discriminator, a ``RuntimeErrorClassification``, and an optional
workflow-specific diagnostic. Terminal workflow errors use the established
``{ref: ErrorTransportDetail}`` map. The transport stays diagnostic-agnostic;
workflow layers specialize and validate their own diagnostic models.
Classification is structural — a payload either parses as one of those shapes
or it is unclassified; there is no partially classified state. Bare legacy
payloads (pre-classification histories) therefore fall through as unclassified
by construction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Literal, Never

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from temporalio.exceptions import ApplicationError, FailureError

from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    TracecatRuntimeError,
)

ERROR_TRANSPORT_DETAIL_SCHEMA = "tracecat.temporal_error.v1"


class ErrorTransportDetail[DiagnosticT](BaseModel):
    """A classified transport detail with an optional typed diagnostic."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    # Required so validation itself rejects undiscriminated payloads and the
    # discriminator always survives ``exclude_unset`` serialization.
    schema_: Literal["tracecat.temporal_error.v1"] = Field(alias="schema")
    classification: RuntimeErrorClassification
    diagnostic: DiagnosticT | None = None


# The two classified transport shapes: a single detail, or the established
# terminal workflow ``{ref: detail}`` map. Anything else —
# including bare legacy diagnostic payloads — is unclassified. The transport
# parser deliberately treats the diagnostic as opaque; workflow-specific
# adapters validate it before consumption or preservation.
type OpaqueErrorTransportDetail = ErrorTransportDetail[object]
type ClassifiedErrorPayload = (
    OpaqueErrorTransportDetail | dict[str, OpaqueErrorTransportDetail]
)

_CLASSIFIED_ERROR_PAYLOAD_ADAPTER: TypeAdapter[ClassifiedErrorPayload] = TypeAdapter(
    ClassifiedErrorPayload
)


def build_error_transport_detail[DiagnosticT](
    classification: RuntimeErrorClassification,
    diagnostic: DiagnosticT | None = None,
) -> ErrorTransportDetail[DiagnosticT]:
    """Build a transport detail from a classification and typed diagnostic."""
    return ErrorTransportDetail[DiagnosticT].model_validate(
        {
            "schema": ERROR_TRANSPORT_DETAIL_SCHEMA,
            "classification": classification,
            "diagnostic": diagnostic,
        }
    )


@contextmanager
def activity_error_boundary(
    classify: Callable[[Exception], RuntimeErrorClassification],
) -> Iterator[None]:
    """Classify an exception at one platform-owned activity call boundary."""
    try:
        yield
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if extract_error_classification(error) is not None:
            raise
        raise_wrapped_application_error(
            error,
            fallback_classification=classify(error),
        )


def parse_classified_error_payload(
    payload: Any,
) -> ClassifiedErrorPayload | None:
    """Parse a transport payload into its classified shape, or None."""
    if isinstance(payload, ErrorTransportDetail):
        payload = payload.model_dump(mode="json")
    try:
        return _CLASSIFIED_ERROR_PAYLOAD_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


def is_classified_error_payload(payload: Any) -> bool:
    """Return whether a transport payload is explicitly classified."""
    return bool(_classifications_from_payload(payload))


def application_error_from_classification(
    classification: RuntimeErrorClassification,
    *details: Any,
    next_retry_delay: timedelta | None = None,
) -> ApplicationError:
    """Build an ``ApplicationError`` from a runtime error classification.

    The passed classification is authoritative for message, type, and
    retryability; detail order never overrides the caller's selection. Existing
    details remain in their original order. If none is classified, a transport
    detail without action diagnostics is appended.
    """
    transported_details = tuple(_serialized_detail(detail) for detail in details)
    if _classification_from_details(details) is None:
        transported_details = (
            *transported_details,
            build_error_transport_detail(classification).model_dump(mode="json"),
        )

    non_retryable = classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    if non_retryable and next_retry_delay is not None:
        raise ValueError("A non-retryable error cannot set next_retry_delay")

    return ApplicationError(
        classification.message,
        *transported_details,
        type=classification.kind.value,
        non_retryable=non_retryable,
        next_retry_delay=next_retry_delay,
    )


def raise_application_error_from_classification(
    classification: RuntimeErrorClassification,
    *details: Any,
    next_retry_delay: timedelta | None = None,
) -> Never:
    """Raise a classified error without serializing the active exception context.

    Temporal serializes exception chains into workflow history. Owning the raise
    here ensures callers cannot accidentally attach a sensitive caught exception
    through implicit chaining.
    """
    raise application_error_from_classification(
        classification,
        *details,
        next_retry_delay=next_retry_delay,
    ) from None


def raise_wrapped_application_error(
    error: BaseException,
    *,
    fallback_classification: RuntimeErrorClassification,
    details: Sequence[Any] = (),
) -> Never:
    """Raise a history-safe application error preserving classification."""
    details_classification = (
        _classification_from_details(error.details)
        if isinstance(error, ApplicationError)
        else None
    )
    classification = (
        details_classification
        or extract_error_classification(error)
        or fallback_classification
    )
    if isinstance(error, ApplicationError):
        wrapped_details = (
            tuple(error.details)
            if details_classification is not None and not details
            else tuple(details)
        )
        next_retry_delay = (
            error.next_retry_delay
            if classification.retry_disposition is RetryDisposition.RETRYABLE
            else None
        )
    else:
        wrapped_details = tuple(details)
        next_retry_delay = None

    raise_application_error_from_classification(
        classification,
        *wrapped_details,
        next_retry_delay=next_retry_delay,
    )


def extract_error_classification(
    error: BaseException,
) -> RuntimeErrorClassification | None:
    """Extract the first valid classification from an exception chain."""
    for current in _error_chain(error):
        if isinstance(current, TracecatRuntimeError):
            return current.classification
        if (
            isinstance(current, ApplicationError)
            and (classification := _classification_from_details(current.details))
            is not None
        ):
            return classification
    return None


def extract_error_classifications(
    error: BaseException,
    *,
    include_implicit_context: bool = True,
) -> tuple[RuntimeErrorClassification, ...]:
    """Extract every valid classification in exception-chain transport order.

    Args:
        error: The exception whose classification should be extracted.
        include_implicit_context: Whether to traverse Python's incidental
            ``__context__`` chain in addition to Temporal and explicit causes.
    """
    classifications: list[RuntimeErrorClassification] = []
    seen: set[RuntimeErrorClassification] = set()
    for current in _error_chain(
        error,
        include_implicit_context=include_implicit_context,
    ):
        if isinstance(current, TracecatRuntimeError):
            _append_unique_classification(
                classifications,
                seen,
                current.classification,
            )
        if isinstance(current, ApplicationError):
            for classification in extract_error_classifications_from_details(
                current.details
            ):
                _append_unique_classification(classifications, seen, classification)
    return tuple(classifications)


def extract_error_classifications_from_details(
    details: Sequence[Any],
) -> tuple[RuntimeErrorClassification, ...]:
    """Extract classifications only from explicitly classified details."""
    classifications: list[RuntimeErrorClassification] = []
    seen: set[RuntimeErrorClassification] = set()
    for detail in details:
        for classification in _classifications_from_payload(detail):
            _append_unique_classification(classifications, seen, classification)
    return tuple(classifications)


def _classification_from_details(
    details: Sequence[Any],
) -> RuntimeErrorClassification | None:
    for detail in details:
        if classifications := _classifications_from_payload(detail):
            return classifications[0]
    return None


def _classifications_from_payload(
    payload: Any,
) -> tuple[RuntimeErrorClassification, ...]:
    match parse_classified_error_payload(payload):
        case None:
            return ()
        case ErrorTransportDetail() as parsed:
            return (parsed.classification,)
        case parsed:
            return tuple(
                transport_detail.classification for transport_detail in parsed.values()
            )


def _serialized_detail(detail: Any) -> Any:
    if isinstance(detail, ErrorTransportDetail):
        return detail.model_dump(mode="json")
    return detail


def _append_unique_classification(
    classifications: list[RuntimeErrorClassification],
    seen: set[RuntimeErrorClassification],
    classification: RuntimeErrorClassification,
) -> None:
    if classification not in seen:
        seen.add(classification)
        classifications.append(classification)


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
