"""DSL-specific adaptation for classified error transport payloads."""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from tracecat.dsl.types import ActionErrorInfo
from tracecat.temporal.errors import ErrorTransportDetail


class ActionErrorTransportDetail(ErrorTransportDetail[ActionErrorInfo]):
    """Classified transport specialized for DSL action diagnostics."""


type ClassifiedActionErrorPayload = (
    ActionErrorTransportDetail | dict[str, ActionErrorTransportDetail]
)

_CLASSIFIED_ACTION_ERROR_PAYLOAD_ADAPTER: TypeAdapter[ClassifiedActionErrorPayload] = (
    TypeAdapter(ClassifiedActionErrorPayload)
)


def parse_classified_action_error_payload(
    payload: object,
) -> ClassifiedActionErrorPayload | None:
    """Parse a classified payload carrying DSL action diagnostics."""
    try:
        return _CLASSIFIED_ACTION_ERROR_PAYLOAD_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


def is_classified_action_error_payload(payload: object) -> bool:
    """Return whether a payload has valid classification and action diagnostics."""
    return parse_classified_action_error_payload(payload) is not None
